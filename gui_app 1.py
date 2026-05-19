"""
CamLink – gui_app.py
WiFi Camera Manager for Show & Go

ARCHITECTURE (correct, production-grade):
══════════════════════════════════════════
  Camera (LAN RTSP) → Agent pulls locally → FFmpeg pushes RTMP → MediaMTX server
                                                                        ↓
                                                              AI Face Recognition

  The agent PUSHES outward. No tunnels. No port forwarding. No SSH relays.
  Works through every NAT, firewall, ISP, enterprise network, mobile hotspot.

  SERVER:  103.65.21.140  (your Ubuntu VPS)
  MediaMTX RTMP ingest  → port 1935
  MediaMTX RTSP out     → port 8554  (for your AI server to consume)
  MediaMTX Web UI       → port 9997
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import time
import json
import os
import uuid
import socket
import subprocess
import shutil
import urllib.parse
import urllib.request
from datetime import datetime
from modules.discovery import scan_network, scan_specific_ip
from modules.rtsp_builder import RTSPBuilder
from modules.logger import *

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════════════
#  SERVER CONFIG  — point this at your MediaMTX server
# ═══════════════════════════════════════════════════════════════════════════
MEDIA_SERVER_IP   = "103.65.21.140"
RTMP_PORT         = 1935
RTMP_APP          = "live"                  # MediaMTX default app name
RTSP_OUT_PORT     = 8554                    # port your AI reads from
MEDIAMTX_API_PORT = 9997                    # MediaMTX HTTP API

# Backend API — set this to your Django/FastAPI URL when ready
# For testing it falls back to local config only
BACKEND_API_URL   = ""   # e.g. "https://api.showandgo.com"

CONFIG_FILE       = "config/settings.json"
DEVICE_ID_FILE    = "config/device_id.txt"


# ═══════════════════════════════════════════════════════════════════════════
#  DESIGN TOKENS
# ═══════════════════════════════════════════════════════════════════════════
class T:
    BG       = "#F7F6F3"
    SURFACE  = "#FFFFFF"
    SURFACE2 = "#F0EEE9"
    BORDER   = "#E2DDD6"
    BRAND    = "#2A6496"
    BRAND_DK = "#1E4D73"
    BRAND_LT = "#D6E8F5"
    GREEN    = "#1A8C5B"
    GREEN_BG = "#E8F5EE"
    RED      = "#C0392B"
    RED_BG   = "#FDEEEC"
    AMBER    = "#B7770D"
    AMBER_BG = "#FDF4DC"
    BLUE     = "#2471A3"
    TEXT_H   = "#1A1815"
    TEXT_B   = "#3D3A35"
    TEXT_M   = "#857F76"
    TEXT_D   = "#C4BFB8"
    SANS     = "Helvetica Neue"
    MONO     = "Menlo"


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r,
           x2,y2-r, x2,y2, x2-r,y2, x1+r,y2,
           x1,y2, x1,y2-r, x1,y1+r, x1,y1, x1+r,y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def get_or_create_device_id():
    """Persistent unique ID for this installation."""
    os.makedirs("config", exist_ok=True)
    if os.path.exists(DEVICE_ID_FILE):
        with open(DEVICE_ID_FILE) as f:
            did = f.read().strip()
            if did:
                return did
    did = uuid.uuid4().hex[:12]
    with open(DEVICE_ID_FILE, "w") as f:
        f.write(did)
    return did


def find_ffmpeg():
    """Find ffmpeg binary cross-platform."""
    candidates = [
        "ffmpeg",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
    ]
    for c in candidates:
        p = shutil.which(c) or (c if os.path.isfile(c) else None)
        if p:
            return p
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  STREAM PUSH ENGINE
#  Replaces TunnelEngine entirely.
#  Pulls RTSP from camera locally → pushes RTMP to your MediaMTX server.
# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
#  STREAM PUSH ENGINE
#
#  Your server sits behind an HTTP proxy (visible from "invalid rtmp version 71"
#  in MediaMTX logs — 71=0x47=ASCII 'G' = start of "GET", meaning the proxy
#  rewrote RTMP as HTTP). So RTMP on port 1935 will NEVER work.
#
#  Protocol order (all outbound, no port-forwarding needed):
#    1. RTSP announce on port 8554  — works, confirmed in your server logs
#    2. SRT on port 8890            — fallback
#    3. RTMP on port 1935           — last resort (likely blocked by proxy)
#
#  Codec: H.265 cameras must be transcoded to H.264 (RTSP/FLV requirement)
# ═══════════════════════════════════════════════════════════════════════════
class StreamPushEngine:

    def __init__(self, on_status, on_log):
        self.on_status  = on_status
        self.on_log     = on_log
        self._proc      = None
        self._running   = False
        self._thread    = None
        self.stream_key = None
        self.viewer_url = None

    def start(self, local_rtsp, stream_key):
        if self._running:
            return
        self.stream_key = stream_key
        self.viewer_url = f"rtsp://{MEDIA_SERVER_IP}:{RTSP_OUT_PORT}/{stream_key}"
        self._running   = True
        self._thread    = threading.Thread(
            target=self._push_loop, args=(local_rtsp,), daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None

    @property
    def is_running(self):
        return self._running

    # ── helpers ───────────────────────────────────────────────────────────

    def _tcp_ok(self, host, port, timeout=4):
        try:
            s = socket.socket()
            s.settimeout(timeout)
            ok = s.connect_ex((host, port)) == 0
            s.close()
            return ok
        except Exception:
            return False

    def _detect_codec(self, ffmpeg, rtsp_url):
        try:
            r = subprocess.run(
                [ffmpeg, "-hide_banner", "-rtsp_transport", "tcp",
                 "-i", rtsp_url, "-t", "1", "-f", "null", "-"],
                capture_output=True, text=True, timeout=15)
            out = (r.stdout + r.stderr).lower()
            if "hevc" in out or "h265" in out: return "hevc"
            if "h264" in out or "avc"  in out: return "h264"
        except Exception:
            pass
        return "unknown"

    def _video_args(self, codec):
        """Return FFmpeg video encoding args for the given source codec."""
        if codec == "h264":
            return ["-c:v", "copy"]          # zero CPU
        # hevc or unknown → transcode to H.264
        return [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune",   "zerolatency",
            "-crf",    "26",
            "-maxrate","2000k",
            "-bufsize", "4000k",
            "-g",      "50",
            "-pix_fmt","yuv420p",
        ]

    def _audio_args(self):
        return ["-c:a", "aac", "-ar", "44100", "-b:a", "128k", "-ac", "2"]

    def _run(self, cmd):
        """Run cmd, stream output to log. Returns (exit_code, alive_seconds)."""
        self.on_log("FFmpeg: " + " ".join(cmd[1:5]) + " …", "info")
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
        except Exception as e:
            self.on_log(f"FFmpeg launch error: {e}", "error")
            return -1, 0

        t0 = time.time()
        for line in self._proc.stdout:
            line = line.strip()
            if not line: continue
            low = line.lower()
            if any(x in low for x in ("error","fail","refuse","timeout",
                                       "unable","invalid","closed","open")):
                self.on_log(f"FFmpeg: {line}", "warn")

        self._proc.wait()
        rc = self._proc.returncode
        self._proc = None
        return rc, time.time() - t0

    # ── main loop ─────────────────────────────────────────────────────────

    def _push_loop(self, local_rtsp):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.on_status(
                "FFmpeg not found!\n"
                "Windows: winget install Gyan.FFmpeg\n"
                "Mac: brew install ffmpeg\n"
                "Linux: sudo apt install ffmpeg", "error")
            self._running = False
            return

        # ── Detect codec once ─────────────────────────────────────────────
        self.on_status("Detecting camera codec…", "scanning")
        codec = self._detect_codec(ffmpeg, local_rtsp)
        vargs = self._video_args(codec)
        aargs = self._audio_args()
        self.on_log(f"Camera codec: {codec} → "
                    f"{'copy' if codec == 'h264' else 'transcode to H.264'}", "info")

        # ── Check reachability once ───────────────────────────────────────
        self.on_status("Checking server ports…", "scanning")
        rtsp_ok = self._tcp_ok(MEDIA_SERVER_IP, RTSP_OUT_PORT)
        srt_ok  = self._tcp_ok(MEDIA_SERVER_IP, 8890)
        rtmp_ok = self._tcp_ok(MEDIA_SERVER_IP, RTMP_PORT)
        self.on_log(
            f"Port check — RTSP:{RTSP_OUT_PORT} {'✓' if rtsp_ok else '✗'}  "
            f"SRT:8890 {'✓' if srt_ok else '✗'}  "
            f"RTMP:{RTMP_PORT} {'✓' if rtmp_ok else '✗'}", "info")

        if not rtsp_ok and not srt_ok and not rtmp_ok:
            self.on_status(
                f"All server ports blocked!\n"
                f"Open ports 8554, 8890, or 1935 on {MEDIA_SERVER_IP}\n"
                f"in your cloud provider's firewall/security-group panel.", "error")
            self._running = False
            return

        retry = 0
        while self._running:
            retry += 1
            if retry > 1:
                wait = min(60, retry * 10)
                self.on_status(f"Reconnecting in {wait}s (attempt {retry})…",
                               "scanning")
                time.sleep(wait)
                if not self._running: break

            # ── 1. RTSP ANNOUNCE (most reliable on your server) ───────────
            if rtsp_ok:
                push_url = (f"rtsp://{MEDIA_SERVER_IP}:{RTSP_OUT_PORT}"
                            f"/{self.stream_key}")
                cmd = [
                    ffmpeg, "-hide_banner", "-loglevel", "warning",
                    "-rtsp_transport", "tcp",
                    "-i", local_rtsp,
                    *vargs, *aargs,
                    "-f", "rtsp",
                    "-rtsp_transport", "tcp",   # force TCP for output too
                    push_url,
                ]
                self.on_status("Stream is LIVE (RTSP)", "online")
                self.on_log(f"Pushing via RTSP → {push_url}", "success")
                rc, alive = self._run(cmd)
                if self._running:
                    self.on_status("Stream dropped — reconnecting", "scanning")
                    self.on_log(f"RTSP stream ended (alive {alive:.0f}s, rc={rc})",
                                "warn")
                continue

            # ── 2. SRT fallback ───────────────────────────────────────────
            if srt_ok:
                srt_url = (f"srt://{MEDIA_SERVER_IP}:8890"
                           f"?streamid=publish:{self.stream_key}&latency=2000000")
                cmd = [
                    ffmpeg, "-hide_banner", "-loglevel", "warning",
                    "-rtsp_transport", "tcp",
                    "-i", local_rtsp,
                    *vargs, *aargs,
                    "-f", "mpegts",
                    srt_url,
                ]
                self.on_status("Stream is LIVE (SRT)", "online")
                self.on_log(f"Pushing via SRT → {srt_url}", "success")
                rc, alive = self._run(cmd)
                if self._running:
                    self.on_status("SRT stream dropped — reconnecting", "scanning")
                continue

            # ── 3. RTMP last resort ───────────────────────────────────────
            if rtmp_ok:
                rtmp_url = (f"rtmp://{MEDIA_SERVER_IP}:{RTMP_PORT}"
                            f"/{RTMP_APP}/{self.stream_key}")
                cmd = [
                    ffmpeg, "-hide_banner", "-loglevel", "warning",
                    "-rtsp_transport", "tcp",
                    "-i", local_rtsp,
                    *vargs, *aargs,
                    "-f", "flv",
                    rtmp_url,
                ]
                self.on_status("Stream is LIVE (RTMP)", "online")
                self.on_log(f"Pushing via RTMP → {rtmp_url}", "success")
                rc, alive = self._run(cmd)
                if self._running:
                    self.on_status("RTMP stream dropped — reconnecting", "scanning")
                continue

        self.on_status("Stream stopped", "idle")


# ═══════════════════════════════════════════════════════════════════════════
#  BACKEND REGISTRAR
#  Talks to your Django/FastAPI backend to get a stream key.
#  Falls back to local key generation if backend is not configured.
# ═══════════════════════════════════════════════════════════════════════════
class BackendRegistrar:

    def get_stream_key(self, device_id, camera_ip, camera_name, on_done):
        """
        on_done(stream_key: str, error: str|None)
        """
        def _run():
            if BACKEND_API_URL:
                try:
                    payload = json.dumps({
                        "device_id":   device_id,
                        "camera_ip":   camera_ip,
                        "camera_name": camera_name,
                    }).encode()
                    req = urllib.request.Request(
                        f"{BACKEND_API_URL}/cameras/stream-key",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=10) as r:
                        resp = json.loads(r.read())
                    key = resp.get("stream_key")
                    if key:
                        on_done(key, None)
                        return
                    on_done(None, "Backend returned no stream key")
                except Exception as e:
                    on_done(None, str(e))
            else:
                # No backend configured — generate a deterministic local key
                clean_ip = camera_ip.replace(".", "_")
                key = f"camlink_{device_id}_{clean_ip}"
                on_done(key, None)

        threading.Thread(target=_run, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════
#  STARTUP SERVICE HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def install_startup(app_path):
    import sys
    system = sys.platform
    try:
        if system == "win32":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "CamLink", 0, winreg.REG_SZ,
                              f'"{sys.executable}" "{app_path}"')
            winreg.CloseKey(key)
            return True, "Added to Windows startup ✓"
        elif system == "darwin":
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.camlink.agent</string>
  <key>ProgramArguments</key>
  <array><string>{sys.executable}</string><string>{app_path}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>"""
            plist_path = os.path.expanduser(
                "~/Library/LaunchAgents/com.camlink.agent.plist")
            with open(plist_path, "w") as f:
                f.write(plist)
            subprocess.run(["launchctl", "load", plist_path], capture_output=True)
            return True, "Added to macOS login items ✓"
        else:
            svc = (f"[Unit]\nDescription=CamLink Agent\nAfter=network.target\n\n"
                   f"[Service]\nExecStart={sys.executable} {app_path}\n"
                   f"Restart=always\n\n[Install]\nWantedBy=default.target\n")
            d = os.path.expanduser("~/.config/systemd/user/")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "camlink.service"), "w") as f:
                f.write(svc)
            subprocess.run(["systemctl", "--user", "enable", "camlink.service"],
                           capture_output=True)
            return True, "Installed as systemd service ✓"
    except Exception as e:
        return False, str(e)


def uninstall_startup():
    import sys
    try:
        if sys.platform == "win32":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run",
                               0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(k, "CamLink")
            winreg.CloseKey(k)
        elif sys.platform == "darwin":
            p = os.path.expanduser("~/Library/LaunchAgents/com.camlink.agent.plist")
            subprocess.run(["launchctl", "unload", p], capture_output=True)
            if os.path.exists(p): os.remove(p)
        else:
            subprocess.run(["systemctl","--user","stop","camlink.service"],
                           capture_output=True)
            subprocess.run(["systemctl","--user","disable","camlink.service"],
                           capture_output=True)
        return True, "Removed from startup"
    except Exception as e:
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════
#  CUSTOM WIDGETS
# ═══════════════════════════════════════════════════════════════════════════
class StyledButton(tk.Canvas):
    _COLORS = {
        "primary": lambda h: (T.BRAND_DK if h else T.BRAND,  "#FFFFFF"),
        "danger":  lambda h: ("#A93226"  if h else T.RED,    "#FFFFFF"),
        "success": lambda h: (T.GREEN    if h else "#20B27A", "#FFFFFF"),
        "ghost":   lambda h: (T.SURFACE2 if h else T.SURFACE, T.TEXT_B),
    }

    def __init__(self, parent, text, command=None, variant="primary",
                 width=160, height=38, **kw):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, bd=0, cursor="hand2", **kw)
        self.command = command
        self.text = text
        self.variant = variant
        self._width = width
        self._height = height
        self._draw(False)
        self.bind("<Enter>",    lambda e: self._draw(True))
        self.bind("<Leave>",    lambda e: self._draw(False))
        self.bind("<Button-1>", lambda e: self.command and self.command())

    def _draw(self, hover):
        self.delete("all")
        bg, fg = self._COLORS.get(self.variant, self._COLORS["primary"])(hover)
        border = T.BORDER if self.variant == "ghost" else bg
        rounded_rect(self, 1, 1, self._width-1, self._height-1,
                     self._height//2, fill=bg, outline=border)
        self.create_text(self._width//2, self._height//2, text=self.text,
                         fill=fg, font=(T.SANS, 11, "bold"))

    def set_text(self, t):
        self.text = t; self._draw(False)

    def disable(self):
        self.command = None; self.configure(cursor="")

    def enable(self, cmd=None):
        if cmd: self.command = cmd
        self.configure(cursor="hand2")


class Card(tk.Frame):
    def __init__(self, parent, title=None, **kw):
        super().__init__(parent, bg=T.SURFACE, bd=0,
                         highlightbackground=T.BORDER, highlightthickness=1, **kw)
        if title:
            tk.Label(self, text=title, bg=T.SURFACE, fg=T.TEXT_H,
                     font=(T.SANS, 12, "bold")).pack(anchor="w", padx=20, pady=(16, 0))


class LabeledEntry(tk.Frame):
    def __init__(self, parent, label, variable=None, show=None,
                 placeholder="", **kw):
        super().__init__(parent, bg=T.SURFACE, **kw)
        tk.Label(self, text=label, bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS, 9)).pack(anchor="w")
        self.entry = tk.Entry(self, textvariable=variable, show=show,
                              font=(T.SANS, 11), bg=T.BG, fg=T.TEXT_B,
                              insertbackground=T.TEXT_B, relief="flat", bd=0,
                              highlightbackground=T.BORDER, highlightthickness=1,
                              highlightcolor=T.BRAND)
        self.entry.pack(fill="x", pady=(4, 0), ipady=8, ipadx=10)
        if placeholder and not variable:
            self._ph = placeholder
            self.entry.insert(0, placeholder)
            self.entry.config(fg=T.TEXT_D)
            self.entry.bind("<FocusIn>",  self._fi)
            self.entry.bind("<FocusOut>", self._fo)

    def _fi(self, e):
        if self.entry.get() == getattr(self, "_ph", ""):
            self.entry.delete(0, "end"); self.entry.config(fg=T.TEXT_B)

    def _fo(self, e):
        if not self.entry.get():
            self.entry.insert(0, self._ph); self.entry.config(fg=T.TEXT_D)

    def get(self):
        v = self.entry.get()
        return "" if v == getattr(self, "_ph", "") else v

    def set(self, v):
        self.entry.delete(0, "end"); self.entry.insert(0, v)
        self.entry.config(fg=T.TEXT_B)


class StatusBadge(tk.Label):
    _S = {
        "online":    (T.GREEN,  T.GREEN_BG,  "●"),
        "offline":   (T.RED,    T.RED_BG,    "○"),
        "scanning":  (T.AMBER,  T.AMBER_BG,  "◌"),
        "connected": (T.BRAND,  T.BRAND_LT,  "◉"),
        "idle":      (T.TEXT_M, T.SURFACE2,  "–"),
    }

    def __init__(self, parent, status="idle", **kw):
        super().__init__(parent, font=(T.SANS, 9, "bold"),
                         padx=10, pady=3, bd=0, relief="flat", **kw)
        self.set(status)

    def set(self, status, label=None):
        fg, bg, dot = self._S.get(status, self._S["idle"])
        self.config(fg=fg, bg=bg, text=f" {dot} {label or status.upper()} ")


class StepBar(tk.Canvas):
    STEPS = ["Discover", "Credentials", "Channels", "Preview", "Stream"]

    def __init__(self, parent, **kw):
        super().__init__(parent, height=64, bg=T.SURFACE,
                         highlightthickness=0, bd=0, **kw)
        self.current = 0
        self.bind("<Configure>", lambda e: self._draw())

    def set_step(self, i):
        self.current = i; self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or 900
        sw = w // len(self.STEPS)
        for i, label in enumerate(self.STEPS):
            cx = sw * i + sw // 2; cy = 28
            if i > 0:
                px = sw * (i-1) + sw // 2
                self.create_line(px+14, cy, cx-14, cy,
                                 fill=T.BRAND if i<=self.current else T.BORDER,
                                 width=2)
            r = 13
            if i < self.current:
                self.create_oval(cx-r,cy-r,cx+r,cy+r, fill=T.BRAND, outline=T.BRAND)
                self.create_text(cx, cy, text="✓", fill="#fff",
                                 font=(T.SANS,10,"bold"))
            elif i == self.current:
                self.create_oval(cx-r,cy-r,cx+r,cy+r, fill=T.BRAND, outline=T.BRAND)
                self.create_text(cx, cy, text=str(i+1), fill="#fff",
                                 font=(T.SANS,9,"bold"))
            else:
                self.create_oval(cx-r,cy-r,cx+r,cy+r, fill=T.SURFACE,
                                 outline=T.BORDER, width=2)
                self.create_text(cx, cy, text=str(i+1), fill=T.TEXT_M,
                                 font=(T.SANS,9))
            color = T.BRAND if i<=self.current else T.TEXT_M
            bold  = "bold" if i==self.current else ""
            self.create_text(cx, cy+r+10, text=label,
                             fill=color, font=(T.SANS, 9, bold))


class CameraItem(tk.Frame):
    def __init__(self, parent, ip, on_select, **kw):
        super().__init__(parent, bg=T.SURFACE,
                         highlightbackground=T.BORDER, highlightthickness=1,
                         cursor="hand2", **kw)
        self.ip = ip; self.on_select = on_select
        inner = tk.Frame(self, bg=T.SURFACE, padx=16, pady=12)
        inner.pack(fill="x")
        tk.Label(inner, text="📷", bg=T.SURFACE,
                 font=(T.SANS, 18)).pack(side="left")
        info = tk.Frame(inner, bg=T.SURFACE)
        info.pack(side="left", padx=12, fill="x", expand=True)
        tk.Label(info, text=ip, bg=T.SURFACE, fg=T.TEXT_H,
                 font=(T.SANS, 12, "bold")).pack(anchor="w")
        tk.Label(info, text="RTSP port 554 open", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS, 9)).pack(anchor="w")
        StatusBadge(inner, "online", text=" ● ONLINE ").pack(side="right")
        self._bind_recursive(self)

    def _bind_recursive(self, w):
        w.bind("<Button-1>", lambda e: self.on_select(self.ip, self))
        for c in w.winfo_children(): self._bind_recursive(c)

    def _tint(self, w, c):
        try: w.config(bg=c)
        except: pass
        for ch in w.winfo_children(): self._tint(ch, c)

    def select(self):
        self.config(highlightbackground=T.BRAND, highlightthickness=2)
        self._tint(self, T.BRAND_LT)

    def deselect(self):
        self.config(highlightbackground=T.BORDER, highlightthickness=1)
        self._tint(self, T.SURFACE)


class ChannelItem(tk.Frame):
    def __init__(self, parent, ch_num, label, url, on_select, **kw):
        super().__init__(parent, bg=T.SURFACE,
                         highlightbackground=T.BORDER, highlightthickness=1,
                         cursor="hand2", **kw)
        self.ch_num = ch_num; self.url = url; self.on_select = on_select
        inner = tk.Frame(self, bg=T.SURFACE, padx=16, pady=14)
        inner.pack(fill="x")
        tk.Label(inner, text=str(ch_num), bg=T.BRAND, fg="#fff",
                 font=(T.SANS, 11, "bold"), width=3, pady=4).pack(side="left")
        info = tk.Frame(inner, bg=T.SURFACE)
        info.pack(side="left", padx=14, fill="x", expand=True)
        tk.Label(info, text=label, bg=T.SURFACE, fg=T.TEXT_H,
                 font=(T.SANS, 11, "bold")).pack(anchor="w")
        tk.Label(info, text=url, bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.MONO, 8)).pack(anchor="w")
        self._bind_recursive(self)

    def _bind_recursive(self, w):
        w.bind("<Button-1>", lambda e: self.on_select(self.ch_num, self.url, self))
        for c in w.winfo_children(): self._bind_recursive(c)

    def select(self):
        self.config(highlightbackground=T.BRAND, highlightthickness=2)

    def deselect(self):
        self.config(highlightbackground=T.BORDER, highlightthickness=1)


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN GUI
# ═══════════════════════════════════════════════════════════════════════════
class CamLinkGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CamLink – WiFi Camera Manager")
        self.root.geometry("1180x800")
        self.root.minsize(1000, 700)
        self.root.configure(bg=T.BG)

        # ── state ──
        self.device_id           = get_or_create_device_id()
        self.camera_list         = []
        self.target_ip           = None
        self.username_var        = tk.StringVar(value="admin")
        self.password_var        = tk.StringVar()
        self.cam_name_var        = tk.StringVar(value="Main Entrance")
        self.discovered_channels = []
        self.selected_channel    = None
        self.selected_rtsp_url   = None
        self.working_url         = None
        self.cap                 = None
        self.preview_cap         = None
        self.is_streaming        = False
        self.frame_count         = 0
        self.stream_start_time   = 0
        self.current_step        = 0
        self.cam_widgets         = {}
        self.ch_widgets          = {}
        self.sel_cam_w           = None
        self.sel_ch_w            = None
        self.push_engine         = StreamPushEngine(self._on_push_status, self.log)
        self.registrar           = BackendRegistrar()
        self.startup_installed   = False
        self.current_stream_key  = None

        self._build_ui()
        self._check_ffmpeg_on_start()
        self._auto_restore()

    # ─────────────────────────────────────────────────────────────────────
    #  UI SKELETON
    # ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        bar = tk.Frame(self.root, bg=T.SURFACE,
                       highlightbackground=T.BORDER, highlightthickness=1, height=64)
        bar.pack(fill="x"); bar.pack_propagate(False)

        logo = tk.Frame(bar, bg=T.SURFACE)
        logo.pack(side="left", padx=28, fill="y")
        tk.Label(logo, text="CamLink", bg=T.SURFACE, fg=T.BRAND,
                 font=(T.SANS, 20, "bold")).pack(side="left", pady=18)
        tk.Label(logo, text="  WiFi Camera Manager", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS, 11)).pack(side="left", pady=18)

        # Device ID badge (top-right)
        id_frame = tk.Frame(bar, bg=T.SURFACE)
        id_frame.pack(side="right", padx=20, pady=18)
        tk.Label(id_frame, text=f"Device: {self.device_id}",
                 bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.MONO, 9)).pack()

        self.step_bar = StepBar(self.root)
        self.step_bar.pack(fill="x")
        self.step_bar.set_step(0)
        tk.Frame(self.root, bg=T.BORDER, height=1).pack(fill="x")

        body = tk.Frame(self.root, bg=T.BG)
        body.pack(fill="both", expand=True)

        self.left = tk.Frame(body, bg=T.BG, width=440)
        self.left.pack(side="left", fill="y", padx=(20,10), pady=20)
        self.left.pack_propagate(False)

        self.right = tk.Frame(body, bg=T.BG)
        self.right.pack(side="right", fill="both", expand=True, padx=(0,20), pady=20)

        self._build_right()
        self.panels = {
            0: self._panel_discover(),
            1: self._panel_credentials(),
            2: self._panel_channels(),
            3: self._panel_preview(),
            4: self._panel_stream(),
        }
        self._show(0)

    # ─────────────────────────────────────────────────────────────────────
    #  RIGHT PANEL
    # ─────────────────────────────────────────────────────────────────────
    def _build_right(self):
        # Live preview card
        vc = Card(self.right, title="Live Preview")
        vc.pack(fill="both", expand=True, pady=(0, 12))

        self.video_ph = tk.Label(vc, text="No camera stream active",
                                  bg=T.SURFACE2, fg=T.TEXT_D,
                                  font=(T.SANS, 13), height=12, relief="flat")
        self.video_ph.pack(fill="both", expand=True, padx=20, pady=(12, 8))
        if PIL_AVAILABLE and CV2_AVAILABLE:
            self.video_lbl = tk.Label(vc, bg="#000")
            self.video_lbl.pack_forget()

        stats = tk.Frame(vc, bg=T.SURFACE)
        stats.pack(fill="x", padx=20, pady=(0, 14))
        self.frames_lbl = tk.Label(stats, text="Frames: –",
                                    bg=T.SURFACE, fg=T.TEXT_M, font=(T.SANS, 9))
        self.frames_lbl.pack(side="left")
        self.uptime_lbl = tk.Label(stats, text="Uptime: –",
                                    bg=T.SURFACE, fg=T.TEXT_M, font=(T.SANS, 9))
        self.uptime_lbl.pack(side="left", padx=20)
        self.stream_badge = StatusBadge(stats, "idle", text=" – IDLE ")
        self.stream_badge.pack(side="right")

        # Stream status card
        sc = Card(self.right, title="Stream Status")
        sc.pack(fill="x", pady=(0, 12))
        si = tk.Frame(sc, bg=T.SURFACE)
        si.pack(fill="x", padx=20, pady=(8, 16))

        self.push_status_lbl = tk.Label(si,
            text="Not streaming. Complete setup and click 'Start Streaming'.",
            bg=T.BG, fg=T.TEXT_M, font=(T.MONO, 9),
            anchor="w", justify="left", padx=10, pady=8,
            wraplength=440, relief="flat",
            highlightbackground=T.BORDER, highlightthickness=1)
        self.push_status_lbl.pack(fill="x", pady=(0, 8))

        self.stream_key_lbl = tk.Label(si, text="",
                                        bg=T.SURFACE, fg=T.TEXT_M,
                                        font=(T.MONO, 9), anchor="w",
                                        wraplength=440, justify="left")
        self.stream_key_lbl.pack(fill="x")

        # Log
        lc = Card(self.right, title="Activity Log")
        lc.pack(fill="x")
        self.log_box = scrolledtext.ScrolledText(
            lc, font=(T.MONO, 9), bg=T.BG, fg=T.TEXT_B,
            insertbackground=T.TEXT_B, relief="flat", bd=0,
            height=7, wrap="word", highlightthickness=0)
        self.log_box.pack(fill="x", padx=20, pady=(4, 16))
        for tag, col in [("success", T.GREEN), ("error", T.RED),
                          ("warn", T.AMBER), ("info", T.BLUE), ("ts", T.TEXT_D)]:
            self.log_box.tag_configure(tag, foreground=col)

    # ─────────────────────────────────────────────────────────────────────
    #  STEP SWITCHING
    # ─────────────────────────────────────────────────────────────────────
    def _show(self, idx):
        for p in self.panels.values(): p.pack_forget()
        self.panels[idx].pack(fill="both", expand=True)
        self.current_step = idx
        self.step_bar.set_step(idx)

    # ─────────────────────────────────────────────────────────────────────
    #  STEP 0 — DISCOVER
    # ─────────────────────────────────────────────────────────────────────
    def _panel_discover(self):
        p = tk.Frame(self.left, bg=T.BG)
        tk.Label(p, text="Discover Cameras", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS, 18, "bold")).pack(anchor="w")
        tk.Label(p, text="Scan your local network to find WiFi cameras.",
                 bg=T.BG, fg=T.TEXT_M, font=(T.SANS, 10)).pack(anchor="w", pady=(4, 14))

        ac = Card(p); ac.pack(fill="x", pady=(0, 12))
        ai = tk.Frame(ac, bg=T.SURFACE); ai.pack(fill="x", padx=20, pady=16)

        br = tk.Frame(ai, bg=T.SURFACE); br.pack(fill="x")
        self.scan_btn = StyledButton(br, "Scan Network", variant="primary",
                                      width=150, height=40,
                                      command=self._auto_scan, bg=T.SURFACE)
        self.scan_btn.pack(side="left")
        self.scan_status = tk.Label(br, text="", bg=T.SURFACE, fg=T.TEXT_M,
                                     font=(T.SANS, 9))
        self.scan_status.pack(side="left", padx=14)

        tk.Frame(ai, bg=T.BORDER, height=1).pack(fill="x", pady=16)
        tk.Label(ai, text="Or enter IP manually", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS, 9)).pack(anchor="w")
        ir = tk.Frame(ai, bg=T.SURFACE); ir.pack(fill="x", pady=(6, 0))
        self.ip_var = tk.StringVar()
        self._ip_e = tk.Entry(ir, textvariable=self.ip_var, font=(T.SANS, 11),
                               bg=T.BG, fg=T.TEXT_D, insertbackground=T.TEXT_B,
                               relief="flat", bd=0,
                               highlightbackground=T.BORDER, highlightthickness=1)
        self._ip_e.insert(0, "e.g. 192.168.1.100")
        self._ip_e.bind("<FocusIn>",
            lambda e: (self._ip_e.delete(0,"end"), self._ip_e.config(fg=T.TEXT_B))
                       if self._ip_e.get().startswith("e.g") else None)
        self._ip_e.bind("<FocusOut>",
            lambda e: (self._ip_e.insert(0,"e.g. 192.168.1.100"),
                       self._ip_e.config(fg=T.TEXT_D))
                       if not self._ip_e.get() else None)
        self._ip_e.pack(side="left", fill="x", expand=True, ipady=8, ipadx=8)
        StyledButton(ir, "Check", variant="ghost", width=80, height=36,
                     command=self._check_manual, bg=T.SURFACE).pack(
                         side="right", padx=(8, 0))

        tc = Card(p, title="Found Cameras"); tc.pack(fill="both", expand=True)
        li = tk.Frame(tc, bg=T.SURFACE); li.pack(fill="both", expand=True,
                                                   padx=20, pady=(8, 16))
        self.cam_frame = tk.Frame(li, bg=T.SURFACE)
        self.cam_frame.pack(fill="both", expand=True)
        self.no_cam_lbl = tk.Label(self.cam_frame,
            text="No cameras found yet.\nClick 'Scan Network' to search.",
            bg=T.SURFACE, fg=T.TEXT_D, font=(T.SANS, 10), justify="center")
        self.no_cam_lbl.pack(expand=True, pady=30)

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(14, 0))
        self.next0 = StyledButton(nav, "Continue →", variant="primary",
                                   width=200, height=40,
                                   command=lambda: self._show(1), bg=T.BG)
        self.next0.pack(side="right"); self.next0.disable()
        return p

    # ─────────────────────────────────────────────────────────────────────
    #  STEP 1 — CREDENTIALS
    # ─────────────────────────────────────────────────────────────────────
    def _panel_credentials(self):
        p = tk.Frame(self.left, bg=T.BG)
        tk.Label(p, text="Camera Credentials", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS, 18, "bold")).pack(anchor="w")
        tk.Label(p, text="Enter the login details for your camera.",
                 bg=T.BG, fg=T.TEXT_M, font=(T.SANS, 10)).pack(anchor="w", pady=(4, 14))

        ac = Card(p); ac.pack(fill="x", pady=(0, 12))
        ai = tk.Frame(ac, bg=T.SURFACE); ai.pack(fill="x", padx=20, pady=16)

        tk.Label(ai, text="Selected Camera", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS, 9)).pack(anchor="w")
        self.sel_cam_pill = tk.Label(ai, text="None",
                                      bg=T.BRAND_LT, fg=T.BRAND,
                                      font=(T.SANS, 12, "bold"),
                                      padx=14, pady=6, anchor="w")
        self.sel_cam_pill.pack(fill="x", pady=(4, 16))

        self.user_ent = LabeledEntry(ai, "Username", variable=self.username_var)
        self.user_ent.pack(fill="x", pady=(0, 10))
        self.pass_ent = LabeledEntry(ai, "Password",
                                      variable=self.password_var, show="●")
        self.pass_ent.pack(fill="x", pady=(0, 16))

        self.conn_btn = StyledButton(ai, "Connect & Test", variant="primary",
                                      width=180, height=40,
                                      command=self._connect_camera, bg=T.SURFACE)
        self.conn_btn.pack(anchor="w")
        self.conn_status = tk.Label(ai, text="", bg=T.SURFACE, fg=T.TEXT_M,
                                     font=(T.SANS, 9), wraplength=360, justify="left")
        self.conn_status.pack(anchor="w", pady=(8, 0))

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(14, 0))
        StyledButton(nav, "← Back", variant="ghost", width=100, height=38,
                     command=lambda: self._show(0), bg=T.BG).pack(side="left")
        return p

    # ─────────────────────────────────────────────────────────────────────
    #  STEP 2 — CHANNELS
    # ─────────────────────────────────────────────────────────────────────
    def _panel_channels(self):
        p = tk.Frame(self.left, bg=T.BG)
        tk.Label(p, text="Select Channel", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS, 18, "bold")).pack(anchor="w")
        tk.Label(p, text="Choose a video channel from your camera.",
                 bg=T.BG, fg=T.TEXT_M, font=(T.SANS, 10)).pack(anchor="w", pady=(4, 14))

        pr = tk.Frame(p, bg=T.BG); pr.pack(fill="x", pady=(0, 10))
        self.probe_btn = StyledButton(pr, "Auto-Detect Channels", variant="primary",
                                       width=200, height=38,
                                       command=self._probe_channels, bg=T.BG)
        self.probe_btn.pack(side="left")
        self.probe_status = tk.Label(pr, text="", bg=T.BG, fg=T.TEXT_M,
                                      font=(T.SANS, 9))
        self.probe_status.pack(side="left", padx=12)

        lc = Card(p, title="Available Channels")
        lc.pack(fill="both", expand=True, pady=(0, 12))
        self.ch_frame = tk.Frame(lc, bg=T.SURFACE)
        self.ch_frame.pack(fill="both", expand=True, padx=20, pady=(8, 16))
        self.no_ch_lbl = tk.Label(self.ch_frame,
            text="No channels detected.\nClick 'Auto-Detect Channels'.",
            bg=T.SURFACE, fg=T.TEXT_D, font=(T.SANS, 10), justify="center")
        self.no_ch_lbl.pack(expand=True, pady=30)

        mc = Card(p, title="Or enter manually"); mc.pack(fill="x", pady=(0, 12))
        mci = tk.Frame(mc, bg=T.SURFACE); mci.pack(fill="x", padx=20, pady=(8, 16))
        mr = tk.Frame(mci, bg=T.SURFACE); mr.pack(fill="x")
        tk.Label(mr, text="Channel #:", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS, 10)).pack(side="left")
        self.manual_ch_var = tk.StringVar(value="1")
        tk.Entry(mr, textvariable=self.manual_ch_var, width=5,
                 font=(T.SANS, 11), bg=T.BG, fg=T.TEXT_B,
                 relief="flat", bd=0,
                 highlightbackground=T.BORDER, highlightthickness=1).pack(
                     side="left", ipady=6, padx=(8, 8))
        self.subtype_var = tk.StringVar(value="0")
        tk.Label(mr, text="Stream:", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS, 10)).pack(side="left")
        ttk.Combobox(mr, textvariable=self.subtype_var,
                     values=["0 (Main)", "1 (Sub)"], width=10,
                     font=(T.SANS, 10)).pack(side="left", padx=(8, 8))
        StyledButton(mr, "Use This", variant="ghost", width=90, height=34,
                     command=self._use_manual_ch, bg=T.SURFACE).pack(side="left")

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(4, 0))
        StyledButton(nav, "← Back", variant="ghost", width=100, height=38,
                     command=lambda: self._show(1), bg=T.BG).pack(side="left")
        self.next2 = StyledButton(nav, "Preview →", variant="primary",
                                   width=140, height=38,
                                   command=lambda: self._show(3), bg=T.BG)
        self.next2.pack(side="right"); self.next2.disable()
        return p

    # ─────────────────────────────────────────────────────────────────────
    #  STEP 3 — PREVIEW
    # ─────────────────────────────────────────────────────────────────────
    def _panel_preview(self):
        p = tk.Frame(self.left, bg=T.BG)
        tk.Label(p, text="Live Preview", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS, 18, "bold")).pack(anchor="w")
        tk.Label(p, text="Verify the channel looks correct before streaming.",
                 bg=T.BG, fg=T.TEXT_M, font=(T.SANS, 10)).pack(anchor="w", pady=(4, 14))

        ic = Card(p, title="Active Channel"); ic.pack(fill="x", pady=(0, 12))
        ici = tk.Frame(ic, bg=T.SURFACE); ici.pack(fill="x", padx=20, pady=(8, 16))
        self.active_url_lbl = tk.Label(ici, text="No channel selected.",
                                        bg=T.SURFACE, fg=T.TEXT_M,
                                        font=(T.MONO, 9), wraplength=380, justify="left")
        self.active_url_lbl.pack(anchor="w")

        cc = Card(p, title="Preview Controls"); cc.pack(fill="x", pady=(0, 12))
        cci = tk.Frame(cc, bg=T.SURFACE); cci.pack(fill="x", padx=20, pady=(8, 16))
        br = tk.Frame(cci, bg=T.SURFACE); br.pack(fill="x")
        self.preview_btn = StyledButton(br, "▶  Start Preview", variant="success",
                                         width=160, height=40,
                                         command=self._toggle_stream, bg=T.SURFACE)
        self.preview_btn.pack(side="left")
        StyledButton(br, "■  Stop", variant="danger", width=100, height=40,
                     command=self._stop_stream, bg=T.SURFACE).pack(
                         side="left", padx=(10, 0))

        nc = Card(p, title="Camera Label"); nc.pack(fill="x", pady=(0, 12))
        nci = tk.Frame(nc, bg=T.SURFACE); nci.pack(fill="x", padx=20, pady=(8, 16))
        LabeledEntry(nci, "Name shown in Show & Go dashboard",
                     variable=self.cam_name_var).pack(fill="x")

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(14, 0))
        StyledButton(nav, "← Back", variant="ghost", width=100, height=38,
                     command=lambda: self._show(2), bg=T.BG).pack(side="left")
        StyledButton(nav, "Start Streaming →", variant="primary", width=190, height=38,
                     command=lambda: self._show(4), bg=T.BG).pack(side="right")
        return p

    # ─────────────────────────────────────────────────────────────────────
    #  STEP 4 — STREAM  (replaces the old tunnel/share page)
    # ─────────────────────────────────────────────────────────────────────
    def _panel_stream(self):
        p = tk.Frame(self.left, bg=T.BG)

        tk.Label(p, text="Start Streaming", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS, 18, "bold")).pack(anchor="w")
        tk.Label(p,
            text="CamLink reads your camera locally and uploads\n"
                 "directly to the Show & Go server — no port forwarding.",
            bg=T.BG, fg=T.TEXT_M, font=(T.SANS, 10), justify="left").pack(
                 anchor="w", pady=(4, 14))

        # ── Main action card ──
        ac = Card(p); ac.pack(fill="x", pady=(0, 14))
        ai = tk.Frame(ac, bg=T.SURFACE); ai.pack(fill="x", padx=20, pady=20)

        # Status row
        sr = tk.Frame(ai, bg=T.SURFACE); sr.pack(fill="x", pady=(0, 16))
        self.push_badge = StatusBadge(sr, "idle", text=" – NOT STARTED ")
        self.push_badge.pack(side="left")
        self.push_detail = tk.Label(sr, text="",
                                     bg=T.SURFACE, fg=T.TEXT_M,
                                     font=(T.SANS, 9), wraplength=260, justify="left")
        self.push_detail.pack(side="left", padx=12)

        self.start_btn = StyledButton(ai, "▶  Start Streaming",
                                       variant="primary", width=380, height=52,
                                       command=self._start_streaming, bg=T.SURFACE)
        self.start_btn.pack(fill="x")

        self.stop_btn = StyledButton(ai, "■  Stop Streaming",
                                      variant="danger", width=380, height=52,
                                      command=self._stop_streaming, bg=T.SURFACE)
        self.stop_btn.pack(fill="x")
        self.stop_btn.pack_forget()

        # ── Stream info card ──
        ic = Card(p, title="Stream Information"); ic.pack(fill="x", pady=(0, 14))
        ii = tk.Frame(ic, bg=T.SURFACE); ii.pack(fill="x", padx=20, pady=(8, 16))

        def info_row(parent, label, var_name):
            r = tk.Frame(parent, bg=T.SURFACE); r.pack(fill="x", pady=3)
            tk.Label(r, text=label, bg=T.SURFACE, fg=T.TEXT_M,
                     font=(T.SANS, 9), width=14, anchor="w").pack(side="left")
            lbl = tk.Label(r, text="–", bg=T.BG, fg=T.TEXT_B,
                            font=(T.MONO, 9), anchor="w", padx=8, pady=4,
                            relief="flat",
                            highlightbackground=T.BORDER, highlightthickness=1)
            lbl.pack(side="left", fill="x", expand=True)
            setattr(self, var_name, lbl)

        info_row(ii, "Camera",     "info_camera")
        info_row(ii, "Channel",    "info_channel")
        info_row(ii, "Stream Key", "info_key")
        info_row(ii, "Server",     "info_server")
        info_row(ii, "View URL",   "info_view_url")

        cb = tk.Frame(ii, bg=T.SURFACE); cb.pack(fill="x", pady=(10, 0))
        StyledButton(cb, "📋 Copy View URL", variant="ghost", width=170, height=34,
                     command=self._copy_view_url, bg=T.SURFACE).pack(side="left")
        self.copy_lbl = tk.Label(cb, text="", bg=T.SURFACE, fg=T.GREEN,
                                  font=(T.SANS, 9))
        self.copy_lbl.pack(side="left", padx=10)

        # ── How it works ──
        hc = Card(p, title="How it works"); hc.pack(fill="x", pady=(0, 14))
        hci = tk.Frame(hc, bg=T.SURFACE); hci.pack(fill="x", padx=20, pady=(8, 16))
        for num, txt in [
            ("1", "CamLink reads the camera stream on your local network."),
            ("2", "It uploads the video directly to the Show & Go server."),
            ("3", "Your AI server receives the stream and runs face recognition."),
            ("4", "No port forwarding, no tunnels, no router changes needed."),
        ]:
            r = tk.Frame(hci, bg=T.SURFACE); r.pack(fill="x", pady=3)
            tk.Label(r, text=num, bg=T.BRAND, fg="#fff",
                     font=(T.SANS, 10, "bold"), width=2, pady=2).pack(side="left")
            tk.Label(r, text=txt, bg=T.SURFACE, fg=T.TEXT_B,
                     font=(T.SANS, 10), padx=12, anchor="w").pack(
                         side="left", fill="x", expand=True)

        # ── Auto-start ──
        kc = Card(p, title="Run automatically at startup")
        kc.pack(fill="x", pady=(0, 14))
        kci = tk.Frame(kc, bg=T.SURFACE); kci.pack(fill="x", padx=20, pady=(8, 16))
        tk.Label(kci,
            text="Enable this so CamLink starts automatically when your PC boots\n"
                 "and reconnects the stream without you doing anything.",
            bg=T.SURFACE, fg=T.TEXT_M, font=(T.SANS, 9), justify="left").pack(anchor="w")
        sb = tk.Frame(kci, bg=T.SURFACE); sb.pack(fill="x", pady=(10, 0))
        self.startup_btn = StyledButton(sb, "Enable Auto-Start", variant="success",
                                         width=180, height=36,
                                         command=self._toggle_startup, bg=T.SURFACE)
        self.startup_btn.pack(side="left")
        self.startup_lbl = tk.Label(sb, text="", bg=T.SURFACE, fg=T.TEXT_M,
                                     font=(T.SANS, 9))
        self.startup_lbl.pack(side="left", padx=12)

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(0, 0))
        StyledButton(nav, "← Back", variant="ghost", width=100, height=38,
                     command=lambda: self._show(3), bg=T.BG).pack(side="left")
        return p

    # ─────────────────────────────────────────────────────────────────────
    #  DISCOVERY
    # ─────────────────────────────────────────────────────────────────────
    def _auto_scan(self):
        self.scan_btn.set_text("Scanning…")
        self.scan_status.config(text="Scanning…", fg=T.AMBER)
        for w in list(self.cam_widgets.values()): w.destroy()
        self.cam_widgets.clear()
        self.no_cam_lbl.pack(expand=True, pady=30)
        self.log("Starting network scan…", "info")
        threading.Thread(
            target=lambda: self.root.after(
                0, lambda: self._on_scan_done(scan_network())),
            daemon=True).start()

    def _on_scan_done(self, cameras):
        self.scan_btn.set_text("Scan Network")
        self.camera_list = cameras
        if cameras:
            self.no_cam_lbl.pack_forget()
            self.scan_status.config(text=f"{len(cameras)} found", fg=T.GREEN)
            self.log(f"Found {len(cameras)} camera(s)", "success")
            for ip in cameras: self._add_cam(ip)
            self.next0.enable(lambda: self._show(1))
        else:
            self.scan_status.config(text="None found", fg=T.RED)
            self.log("No cameras found on network", "warn")
            messagebox.showwarning("No Cameras Found",
                "No cameras with port 554 open.\n\n"
                "• Same WiFi network as camera?\n"
                "• RTSP enabled on camera?\n"
                "• Try entering IP manually below.")

    def _add_cam(self, ip):
        w = CameraItem(self.cam_frame, ip, self._select_cam)
        w.pack(fill="x", pady=(0, 6))
        self.cam_widgets[ip] = w

    def _check_manual(self):
        raw = self.ip_var.get().strip()
        if not raw or raw.startswith("e.g"):
            messagebox.showerror("Error", "Please enter an IP address."); return
        self.log(f"Checking {raw}…", "info")
        def _r():
            ok = scan_specific_ip(raw)
            self.root.after(0, lambda: self._on_manual_done(raw, ok))
        threading.Thread(target=_r, daemon=True).start()

    def _on_manual_done(self, ip, ok):
        if ok:
            self.log(f"Camera found at {ip}", "success")
            if ip not in self.camera_list:
                self.camera_list.append(ip)
                self.no_cam_lbl.pack_forget()
                self._add_cam(ip)
                self.next0.enable(lambda: self._show(1))
        else:
            self.log(f"No camera at {ip}", "error")
            messagebox.showerror("Not Found", f"No RTSP camera responding at {ip}.")

    def _select_cam(self, ip, widget):
        if self.sel_cam_w: self.sel_cam_w.deselect()
        self.sel_cam_w = widget; widget.select()
        self.target_ip = ip
        self.sel_cam_pill.config(text=f"  {ip}  ")
        self.log(f"Selected camera: {ip}", "info")
        cfg = self._load_cfg()
        saved = cfg.get("cameras", {}).get(ip, {})
        if saved.get("username"):
            self.username_var.set(saved["username"])
            self.password_var.set(saved.get("password", ""))
            self.log("Loaded saved credentials", "success")

    # ─────────────────────────────────────────────────────────────────────
    #  CREDENTIALS
    # ─────────────────────────────────────────────────────────────────────
    def _connect_camera(self):
        if not self.target_ip:
            messagebox.showerror("Error", "Select a camera first."); return
        if not self.password_var.get():
            messagebox.showerror("Error", "Password required."); return
        self.conn_btn.set_text("Connecting…")
        self.conn_status.config(text="Testing RTSP paths…", fg=T.AMBER)
        self.log(f"Connecting to {self.target_ip}…", "info")

        def _r():
            cfg = self._load_cfg()
            saved = cfg.get("cameras", {}).get(self.target_ip, {})
            cap, url = self._find_stream(self.target_ip,
                                          self.username_var.get(),
                                          self.password_var.get(),
                                          saved.get("rtsp_path"))
            self.root.after(0, lambda: self._on_connected(cap, url))
        threading.Thread(target=_r, daemon=True).start()

    def _find_stream(self, ip, user, pwd, saved_path=None):
        b = RTSPBuilder()
        if saved_path:
            self.root.after(0, lambda: self.log(f"Trying saved path: {saved_path}", "info"))
            for url in b.build_url(ip, user, pwd, path_override=saved_path):
                if not CV2_AVAILABLE: return "MOCK", url
                cap = cv2.VideoCapture(url)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret: return cap, url
                    cap.release()
        if not CV2_AVAILABLE:
            return "MOCK", RTSPBuilder(ip, user, pwd).build_url(1, 0)
        candidates = b.build_url(ip, user, pwd)
        self.root.after(0, lambda: self.log(
            f"Testing {len(candidates)} RTSP paths…", "info"))
        for idx, url in enumerate(candidates, 1):
            if idx % 20 == 0:
                self.root.after(0, lambda i=idx: self.log(
                    f"Progress: {i}/{len(candidates)}…", "info"))
            cap = cv2.VideoCapture(url)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret: return cap, url
                cap.release()
        return None, None

    def _on_connected(self, cap, url):
        self.conn_btn.set_text("Connect & Test")
        if cap:
            self.cap = cap; self.working_url = url
            self.conn_status.config(text="✓ Connected!", fg=T.GREEN)
            self.log(f"Connected: {url}", "success")
            path = RTSPBuilder().extract_path(url)
            cfg  = self._load_cfg()
            cfg.setdefault("cameras", {})[self.target_ip] = {
                "username": self.username_var.get(),
                "password": self.password_var.get(),
                "rtsp_path": path,
            }
            cfg["last_ip"] = self.target_ip
            self._save_cfg(cfg)
            self._show(2)
        else:
            self.conn_status.config(text="✗ Failed. Check credentials.", fg=T.RED)
            self.log("Connection failed", "error")
            messagebox.showerror("Failed",
                "Could not connect.\n\n"
                "• Wrong username/password?\n"
                "• RTSP enabled on camera?\n"
                "• Same network as camera?")

    # ─────────────────────────────────────────────────────────────────────
    #  CHANNELS
    # ─────────────────────────────────────────────────────────────────────
    def _probe_channels(self):
        if not self.target_ip or not self.working_url:
            messagebox.showwarning("Not Connected", "Connect to a camera first.")
            return
        self.probe_btn.set_text("Detecting…")
        self.probe_status.config(text="Probing…", fg=T.AMBER)
        self.log("Auto-detecting channels…", "info")
        for w in list(self.ch_widgets.values()): w.destroy()
        self.ch_widgets.clear()
        self.no_ch_lbl.pack(expand=True, pady=30)
        threading.Thread(
            target=lambda: self.root.after(
                0, lambda: self._on_channels_found(self._discover_channels())),
            daemon=True).start()

    def _discover_channels(self):
        found = []
        b = RTSPBuilder(self.target_ip, self.username_var.get(), self.password_var.get())
        for ch in range(1, 9):
            for sub in [0, 1]:
                url   = b.build_url(ch, sub)
                label = f"Channel {ch} – {'Main' if sub==0 else 'Sub'} Stream"
                if CV2_AVAILABLE:
                    cap = cv2.VideoCapture(url)
                    if cap.isOpened():
                        ret, _ = cap.read(); cap.release()
                        if ret:
                            found.append({"channel":ch,"subtype":sub,
                                           "label":label,"url":url})
                            self.root.after(0, lambda l=label:
                                self.log(f"Found: {l}", "success"))
                            break
                else:
                    if ch <= 2:
                        found.append({"channel":ch,"subtype":sub,
                                       "label":label,"url":url})
                        break
        return found

    def _on_channels_found(self, channels):
        self.probe_btn.set_text("Auto-Detect Channels")
        self.discovered_channels = channels
        if channels:
            self.no_ch_lbl.pack_forget()
            self.probe_status.config(text=f"{len(channels)} found", fg=T.GREEN)
            self.log(f"Found {len(channels)} channel(s)", "success")
            for ch in channels: self._add_ch(ch)
        else:
            self.probe_status.config(text="None found – enter manually", fg=T.AMBER)
            self.log("No channels auto-detected", "warn")

    def _add_ch(self, ch):
        w = ChannelItem(self.ch_frame, ch["channel"], ch["label"],
                         ch["url"], self._select_ch)
        w.pack(fill="x", pady=(0, 6))
        self.ch_widgets[ch["channel"]] = w

    def _select_ch(self, ch_num, url, widget):
        if self.sel_ch_w: self.sel_ch_w.deselect()
        self.sel_ch_w = widget; widget.select()
        self.selected_channel  = ch_num
        self.selected_rtsp_url = url
        self.active_url_lbl.config(text=url)
        self.log(f"Selected channel {ch_num}", "info")
        self.next2.enable(lambda: self._show(3))
        if self.is_streaming:
            self._stop_stream()

    def _use_manual_ch(self):
        try: ch = int(self.manual_ch_var.get())
        except ValueError:
            messagebox.showerror("Error", "Enter a valid channel number."); return
        raw_sub = self.subtype_var.get()
        sub = int(raw_sub[0]) if raw_sub else 0
        b   = RTSPBuilder(self.target_ip, self.username_var.get(), self.password_var.get())
        url = b.build_url(ch, sub)
        ch_data = {"channel": ch, "subtype": sub,
                    "label": f"Channel {ch} – {'Main' if sub==0 else 'Sub'} Stream",
                    "url": url}
        if ch not in self.ch_widgets:
            self.no_ch_lbl.pack_forget()
            self._add_ch(ch_data)
            self.discovered_channels.append(ch_data)
        self._select_ch(ch, url, self.ch_widgets[ch])
        self.log(f"Manual channel {ch} selected", "info")

    # ─────────────────────────────────────────────────────────────────────
    #  PREVIEW  (local only, uses fresh cap per channel)
    # ─────────────────────────────────────────────────────────────────────
    def _toggle_stream(self):
        if self.is_streaming: self._stop_stream()
        else: self._start_stream()

    def _start_stream(self):
        url = self.selected_rtsp_url or self.working_url
        if not url:
            messagebox.showerror("Error", "No channel selected."); return
        if not CV2_AVAILABLE or not PIL_AVAILABLE:
            self.stream_badge.set("connected", "SIMULATED")
            self.preview_btn.set_text("⏸  Pause"); return

        if self.preview_cap:
            try: self.preview_cap.release()
            except: pass

        self.preview_cap = cv2.VideoCapture(url)
        if not self.preview_cap.isOpened():
            self.log(f"Could not open: {url}", "error")
            messagebox.showerror("Error", "Could not open camera stream."); return

        self.is_streaming      = True
        self.stream_start_time = time.time()
        self.frame_count       = 0
        self.preview_btn.set_text("⏸  Pause")
        self.stream_badge.set("online", "LIVE")
        self.log(f"Preview: {url}", "success")
        self.video_ph.pack_forget()
        self.video_lbl.pack(fill="both", expand=True, padx=20, pady=(12, 8))
        self._update_frame()

    def _stop_stream(self):
        self.is_streaming = False
        self.preview_btn.set_text("▶  Start Preview")
        self.stream_badge.set("idle", "STOPPED")
        self.log("Preview stopped", "warn")
        if PIL_AVAILABLE and CV2_AVAILABLE:
            try: self.video_lbl.pack_forget()
            except: pass
        if self.preview_cap:
            try: self.preview_cap.release()
            except: pass
            self.preview_cap = None
        self.video_ph.pack(fill="both", expand=True, padx=20, pady=(12, 8))

    def _update_frame(self):
        if not self.is_streaming: return
        cap = self.preview_cap
        if cap is None: return
        ret, frame = cap.read()
        if not ret:
            self.log("Frame read failed – reconnecting…", "warn")
            try: cap.release()
            except: pass
            url = self.selected_rtsp_url or self.working_url
            self.preview_cap = cv2.VideoCapture(url)
            self.root.after(500, self._update_frame); return
        self.frame_count += 1
        disp  = cv2.resize(frame, (640, 400))
        img   = Image.fromarray(cv2.cvtColor(disp, cv2.COLOR_BGR2RGB))
        imgtk = ImageTk.PhotoImage(image=img)
        self.video_lbl.imgtk = imgtk
        self.video_lbl.config(image=imgtk)
        elapsed = int(time.time() - self.stream_start_time)
        h, m, s = elapsed//3600, (elapsed%3600)//60, elapsed%60
        self.frames_lbl.config(text=f"Frames: {self.frame_count}")
        self.uptime_lbl.config(text=f"Uptime: {h:02d}:{m:02d}:{s:02d}")
        self.root.after(33, self._update_frame)

    # ─────────────────────────────────────────────────────────────────────
    #  STREAMING  (push to server)
    # ─────────────────────────────────────────────────────────────────────
    def _start_streaming(self):
        url = self.selected_rtsp_url or self.working_url
        if not url:
            messagebox.showwarning("No Channel", "Select a channel first."); return
        if not find_ffmpeg():
            messagebox.showerror("FFmpeg Missing",
                "FFmpeg is required for streaming.\n\n"
                "Install it:\n"
                "• Windows: winget install Gyan.FFmpeg\n"
                "• Mac: brew install ffmpeg\n"
                "• Linux: sudo apt install ffmpeg"); return

        self.start_btn.pack_forget()
        self.stop_btn.pack(fill="x")
        self.push_badge.set("scanning", "GETTING KEY…")
        self.push_detail.config(text="Registering with server…")
        self.log("Getting stream key from server…", "info")

        def _got_key(stream_key, error):
            if error and not stream_key:
                self.root.after(0, lambda: self._on_stream_start_failed(
                    f"Could not get stream key: {error}"))
                return
            self.root.after(0, lambda: self._launch_push(url, stream_key))

        self.registrar.get_stream_key(
            device_id   = self.device_id,
            camera_ip   = self.target_ip or "unknown",
            camera_name = self.cam_name_var.get(),
            on_done     = _got_key,
        )

    def _launch_push(self, local_rtsp, stream_key):
        self.current_stream_key = stream_key
        # No auth on MediaMTX — paste directly into VLC, no password
        view_url = f"rtsp://{MEDIA_SERVER_IP}:{RTSP_OUT_PORT}/{stream_key}"

        self.info_camera.config(  text=self.target_ip or "–")
        self.info_channel.config( text=self.active_url_lbl.cget("text")[:60])
        self.info_key.config(     text=stream_key)
        self.info_server.config(  text=f"{MEDIA_SERVER_IP}:{RTSP_OUT_PORT}")
        self.info_view_url.config(text=view_url, fg=T.GREEN)
        self.push_status_lbl.config(
            text=f"Connecting to {MEDIA_SERVER_IP}…", fg=T.TEXT_B)
        self.stream_key_lbl.config(
            text=f"View URL (no password needed):\n{view_url}", fg=T.BRAND)

        cfg = self._load_cfg()
        cfg["last_stream_key"] = stream_key
        cfg["last_local_rtsp"] = local_rtsp
        cfg["last_cam_name"]   = self.cam_name_var.get()
        self._save_cfg(cfg)

        self.push_engine.start(local_rtsp, stream_key)
        self.log(f"Stream key: {stream_key}", "success")
        self.log(f"View in VLC (no password): {view_url}", "info")

    def _on_stream_start_failed(self, msg):
        self.stop_btn.pack_forget()
        self.start_btn.pack(fill="x")
        self.push_badge.set("offline", "FAILED")
        self.push_detail.config(text=msg)
        self.log(msg, "error")

    def _stop_streaming(self):
        self.push_engine.stop()
        self.stop_btn.pack_forget()
        self.start_btn.pack(fill="x")
        self.push_badge.set("idle", "STOPPED")
        self.push_detail.config(text="Stream stopped.")
        self.push_status_lbl.config(
            text="Not streaming. Click 'Start Streaming' to begin.",
            fg=T.TEXT_M)
        self.stream_key_lbl.config(text="")
        self.log("Streaming stopped", "warn")

    def _on_push_status(self, message, level):
        """Called by StreamPushEngine from background thread."""
        status_map = {
            "online":   ("online",  "LIVE"),
            "scanning": ("scanning","CONNECTING…"),
            "error":    ("offline", "ERROR"),
            "idle":     ("idle",    "STOPPED"),
        }
        badge_status, badge_label = status_map.get(level, ("idle", "STOPPED"))

        def _update():
            self.push_badge.set(badge_status, badge_label)
            self.push_detail.config(text=message)
            self.push_status_lbl.config(
                text=message,
                fg={"online": T.GREEN, "error": T.RED,
                    "scanning": T.AMBER}.get(level, T.TEXT_M))
        self.root.after(0, _update)

    def _copy_view_url(self):
        url = self.info_view_url.cget("text")
        if url and url != "–":
            self.root.clipboard_clear(); self.root.clipboard_append(url)
            self.copy_lbl.config(text="✓ Copied!", fg=T.GREEN)
            self.root.after(2500, lambda: self.copy_lbl.config(text=""))

    # ─────────────────────────────────────────────────────────────────────
    #  AUTO-RESTORE
    # ─────────────────────────────────────────────────────────────────────
    def _auto_restore(self):
        cfg = self._load_cfg()
        last_key = cfg.get("last_stream_key")
        last_rtsp = cfg.get("last_local_rtsp")
        if last_key and last_rtsp:
            self.log(f"Previous session found — key: {last_key}", "info")
            self.log("Go to 'Stream' step and click Start to reconnect.", "info")

    # ─────────────────────────────────────────────────────────────────────
    #  STARTUP SERVICE
    # ─────────────────────────────────────────────────────────────────────
    def _toggle_startup(self):
        import sys
        app_path = os.path.abspath(sys.argv[0])
        if not self.startup_installed:
            ok, msg = install_startup(app_path)
            if ok:
                self.startup_installed = True
                self.startup_btn.set_text("Disable Auto-Start")
                self.startup_lbl.config(text=msg, fg=T.GREEN)
                self.log(msg, "success")
            else:
                self.startup_lbl.config(text=msg, fg=T.RED)
                self.log(f"Startup install failed: {msg}", "error")
        else:
            ok, msg = uninstall_startup()
            if ok:
                self.startup_installed = False
                self.startup_btn.set_text("Enable Auto-Start")
                self.startup_lbl.config(text=msg, fg=T.AMBER)
                self.log(msg, "warn")
            else:
                self.startup_lbl.config(text=msg, fg=T.RED)

    # ─────────────────────────────────────────────────────────────────────
    #  FFMPEG CHECK
    # ─────────────────────────────────────────────────────────────────────
    def _check_ffmpeg_on_start(self):
        ff = find_ffmpeg()
        if ff:
            self.log(f"FFmpeg found: {ff}", "success")
        else:
            self.log(
                "FFmpeg NOT found. Install it before streaming:\n"
                "  Windows: winget install Gyan.FFmpeg\n"
                "  Mac:     brew install ffmpeg\n"
                "  Linux:   sudo apt install ffmpeg",
                "warn")

    # ─────────────────────────────────────────────────────────────────────
    #  LOGGING
    # ─────────────────────────────────────────────────────────────────────
    def log(self, message, level="info"):
        icons = {"success":"✓","error":"✗","warn":"⚠","info":"·"}
        ts    = datetime.now().strftime("%H:%M:%S")
        def _ins():
            self.log_box.insert("end", f"[{ts}] ", "ts")
            self.log_box.insert("end",
                                f"{icons.get(level,'·')} {message}\n", level)
            self.log_box.see("end")
        self.root.after(0, _ins)

    # ─────────────────────────────────────────────────────────────────────
    #  CONFIG
    # ─────────────────────────────────────────────────────────────────────
    def _load_cfg(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f: return json.load(f)
            except Exception: pass
        return {}

    def _save_cfg(self, data):
        os.makedirs("config", exist_ok=True)
        with open(CONFIG_FILE, "w") as f: json.dump(data, f, indent=2)


def main():
    root = tk.Tk()
    CamLinkGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()