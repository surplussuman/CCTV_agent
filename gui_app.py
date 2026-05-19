"""
CamLink – gui_app.py
WiFi Camera Manager for Show & Go

Architecture for internet sharing (no port forwarding):
  Option A – Tunnel mode (app must stay running, PC must stay on):
      This app opens an SSH reverse tunnel to your relay server.
      The relay server listens on a public TCP port and forwards all
      traffic to this machine's local RTSP port 554.
      User copies the public rtsp://<relay-host>:<port>/... URL into Show & Go.

  Option B – Server-pull mode (recommended, app must stay running, PC must stay on):
      Your Show & Go server connects directly to the local RTSP URL through
      the tunnel. The camera registration is saved server-side, so if the
      user restarts the app it re-registers automatically on startup.

  Neither option avoids the "PC must be on" rule — that's physics.
  The best real-world answer: run CamLink as a Windows/macOS startup service
  so it starts automatically with the PC. We provide that option in the UI.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import time
import json
import os
import base64
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

CONFIG_FILE  = "config/settings.json"
MOCK_API_PORT = 5000

# ── Show & Go server endpoint (replace with your real server) ──────────────
SHOWANDGO_REGISTER_URL = "https://api.showandgo.example.com/cameras/register"
SHOWANDGO_HEARTBEAT_URL = "https://api.showandgo.example.com/cameras/heartbeat"
# ──────────────────────────────────────────────────────────────────────────


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

    SANS = "Helvetica Neue"
    MONO = "Menlo"


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r,
           x2,y2-r, x2,y2, x2-r,y2, x1+r,y2,
           x1,y2, x1,y2-r, x1,y1+r, x1,y1, x1+r,y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ═══════════════════════════════════════════════════════════════════════════
#  STYLED BUTTON
# ═══════════════════════════════════════════════════════════════════════════
class StyledButton(tk.Canvas):
    _COLORS = {
        "primary": lambda h: ((T.BRAND_DK if h else T.BRAND), "#FFFFFF"),
        "danger":  lambda h: (("#A93226" if h else T.RED),    "#FFFFFF"),
        "success": lambda h: ((T.GREEN   if h else "#20B27A"), "#FFFFFF"),
        "ghost":   lambda h: ((T.SURFACE2 if h else T.SURFACE), T.TEXT_B),
    }

    def __init__(self, parent, text, command=None, variant="primary",
                 width=160, height=38, **kw):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, bd=0, cursor="hand2", **kw)
        self.command = command
        self.text    = text
        self.variant = variant
        # avoid clobbering tkinter internal `_w` attribute
        self._width = width; self._height = height
        self._draw(False)
        self.bind("<Enter>",    lambda e: self._draw(True))
        self.bind("<Leave>",    lambda e: self._draw(False))
        self.bind("<Button-1>", lambda e: self.command and self.command())

    def _draw(self, hover):
        self.delete("all")
        fn = self._COLORS.get(self.variant, self._COLORS["primary"])
        bg, fg = fn(hover)
        border = T.BORDER if self.variant == "ghost" else bg
        rounded_rect(self, 1, 1, self._width-1, self._height-1, self._height//2,
                 fill=bg, outline=border)
        self.create_text(self._width//2, self._height//2, text=self.text,
                 fill=fg, font=(T.SANS, 11, "bold"))

    def set_text(self, t):
        self.text = t; self._draw(False)

    def set_cmd(self, c):
        self.command = c; self.configure(cursor="hand2")

    def disable(self):
        self.command = None; self.configure(cursor="")

    def enable(self, cmd=None):
        if cmd: self.command = cmd
        self.configure(cursor="hand2")


# ═══════════════════════════════════════════════════════════════════════════
#  CARD
# ═══════════════════════════════════════════════════════════════════════════
class Card(tk.Frame):
    def __init__(self, parent, title=None, **kw):
        super().__init__(parent, bg=T.SURFACE, bd=0,
                         highlightbackground=T.BORDER, highlightthickness=1, **kw)
        if title:
            tk.Label(self, text=title, bg=T.SURFACE, fg=T.TEXT_H,
                     font=(T.SANS, 12, "bold")).pack(anchor="w", padx=20, pady=(16, 0))


# ═══════════════════════════════════════════════════════════════════════════
#  LABELED ENTRY
# ═══════════════════════════════════════════════════════════════════════════
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
        self.entry.pack(fill="x", pady=(4,0), ipady=8, ipadx=10)
        if placeholder and not variable:
            self._ph = placeholder
            self.entry.insert(0, placeholder)
            self.entry.config(fg=T.TEXT_D)
            self.entry.bind("<FocusIn>",  self._fi)
            self.entry.bind("<FocusOut>", self._fo)

    def _fi(self, e):
        if self.entry.get() == getattr(self, "_ph", ""):
            self.entry.delete(0,"end"); self.entry.config(fg=T.TEXT_B)

    def _fo(self, e):
        if not self.entry.get():
            self.entry.insert(0, self._ph); self.entry.config(fg=T.TEXT_D)

    def get(self):
        v = self.entry.get()
        return "" if v == getattr(self,"_ph","") else v

    def set(self, v):
        self.entry.delete(0,"end"); self.entry.insert(0,v)
        self.entry.config(fg=T.TEXT_B)


# ═══════════════════════════════════════════════════════════════════════════
#  STATUS BADGE
# ═══════════════════════════════════════════════════════════════════════════
class StatusBadge(tk.Label):
    _S = {"online":    (T.GREEN,  T.GREEN_BG,  "●"),
          "offline":   (T.RED,    T.RED_BG,    "○"),
          "scanning":  (T.AMBER,  T.AMBER_BG,  "◌"),
          "connected": (T.BRAND,  T.BRAND_LT,  "◉"),
          "idle":      (T.TEXT_M, T.SURFACE2,  "–")}

    def __init__(self, parent, status="idle", **kw):
        super().__init__(parent, font=(T.SANS,9,"bold"),
                         padx=10, pady=3, bd=0, relief="flat", **kw)
        self.set(status)

    def set(self, status, label=None):
        fg, bg, dot = self._S.get(status, self._S["idle"])
        self.config(fg=fg, bg=bg,
                    text=f" {dot} {label or status.upper()} ")


# ═══════════════════════════════════════════════════════════════════════════
#  STEP BAR
# ═══════════════════════════════════════════════════════════════════════════
class StepBar(tk.Canvas):
    STEPS = ["Discover", "Credentials", "Channels", "Preview", "Share"]

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
        n = len(self.STEPS)
        sw = w // n
        for i, label in enumerate(self.STEPS):
            cx = sw * i + sw // 2; cy = 28
            if i > 0:
                px = sw * (i-1) + sw // 2
                self.create_line(px+14, cy, cx-14, cy,
                                 fill=T.BRAND if i<=self.current else T.BORDER, width=2)
            r = 13
            if i < self.current:
                self.create_oval(cx-r,cy-r,cx+r,cy+r, fill=T.BRAND, outline=T.BRAND)
                self.create_text(cx, cy, text="✓", fill="#fff", font=(T.SANS,10,"bold"))
            elif i == self.current:
                self.create_oval(cx-r,cy-r,cx+r,cy+r, fill=T.BRAND, outline=T.BRAND)
                self.create_text(cx, cy, text=str(i+1), fill="#fff", font=(T.SANS,9,"bold"))
            else:
                self.create_oval(cx-r,cy-r,cx+r,cy+r, fill=T.SURFACE, outline=T.BORDER, width=2)
                self.create_text(cx, cy, text=str(i+1), fill=T.TEXT_M, font=(T.SANS,9))
            color = T.BRAND if i<=self.current else T.TEXT_M
            bold  = "bold" if i==self.current else ""
            self.create_text(cx, cy+r+10, text=label,
                             fill=color, font=(T.SANS,9,bold))


# ═══════════════════════════════════════════════════════════════════════════
#  CAMERA ITEM
# ═══════════════════════════════════════════════════════════════════════════
class CameraItem(tk.Frame):
    def __init__(self, parent, ip, on_select, **kw):
        super().__init__(parent, bg=T.SURFACE,
                         highlightbackground=T.BORDER, highlightthickness=1,
                         cursor="hand2", **kw)
        self.ip = ip; self.on_select = on_select
        inner = tk.Frame(self, bg=T.SURFACE, padx=16, pady=12)
        inner.pack(fill="x")
        tk.Label(inner, text="📷", bg=T.SURFACE, font=(T.SANS,18)).pack(side="left")
        info = tk.Frame(inner, bg=T.SURFACE)
        info.pack(side="left", padx=12, fill="x", expand=True)
        tk.Label(info, text=ip, bg=T.SURFACE, fg=T.TEXT_H,
                 font=(T.SANS,12,"bold")).pack(anchor="w")
        tk.Label(info, text="RTSP port 554 open", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS,9)).pack(anchor="w")
        StatusBadge(inner, "online", text=" ● ONLINE ").pack(side="right")
        self._bind_recursive(self)

    def _bind_recursive(self, w):
        w.bind("<Button-1>", lambda e: self.on_select(self.ip, self))
        for c in w.winfo_children(): self._bind_recursive(c)

    def _tint(self, w, color):
        try: w.config(bg=color)
        except: pass
        for c in w.winfo_children(): self._tint(c, color)

    def select(self):
        self.config(highlightbackground=T.BRAND, highlightthickness=2)
        self._tint(self, T.BRAND_LT)

    def deselect(self):
        self.config(highlightbackground=T.BORDER, highlightthickness=1)
        self._tint(self, T.SURFACE)


# ═══════════════════════════════════════════════════════════════════════════
#  CHANNEL ITEM
# ═══════════════════════════════════════════════════════════════════════════
class ChannelItem(tk.Frame):
    def __init__(self, parent, ch_num, label, url, on_select, **kw):
        super().__init__(parent, bg=T.SURFACE,
                         highlightbackground=T.BORDER, highlightthickness=1,
                         cursor="hand2", **kw)
        self.ch_num = ch_num; self.url = url; self.on_select = on_select
        inner = tk.Frame(self, bg=T.SURFACE, padx=16, pady=14)
        inner.pack(fill="x")
        tk.Label(inner, text=str(ch_num), bg=T.BRAND, fg="#fff",
                 font=(T.SANS,11,"bold"), width=3, pady=4).pack(side="left")
        info = tk.Frame(inner, bg=T.SURFACE)
        info.pack(side="left", padx=14, fill="x", expand=True)
        tk.Label(info, text=label, bg=T.SURFACE, fg=T.TEXT_H,
                 font=(T.SANS,11,"bold")).pack(anchor="w")
        tk.Label(info, text=url, bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.MONO,8)).pack(anchor="w")
        self._bind_recursive(self)

    def _bind_recursive(self, w):
        w.bind("<Button-1>", lambda e: self.on_select(self.ch_num, self.url, self))
        for c in w.winfo_children(): self._bind_recursive(c)

    def select(self):
        self.config(highlightbackground=T.BRAND, highlightthickness=2)

    def deselect(self):
        self.config(highlightbackground=T.BORDER, highlightthickness=1)


# ═══════════════════════════════════════════════════════════════════════════
#  TUNNEL ENGINE
#
#  Strategy (zero-install for user):
#  ────────────────────────────────
#  We use an SSH reverse-tunnel to a free public relay.  SSH is built into
#  every modern OS (Windows 10+, macOS, Linux) so the user installs NOTHING.
#
#  Backend priority:
#    1. Your own Show & Go relay server (fastest, most reliable, you control it)
#       Set SHOWANDGO_RELAY_HOST / _PORT / _USER / _KEY below.
#    2. localhost.run  — free public SSH relay, zero sign-up, no install.
#       Gives a URL like:  <random>.lhr.life:<port>
#       TCP tunnels are supported; URL is discovered by parsing SSH output.
#    3. serveo.net     — backup free relay, same mechanism.
#
#  The public RTSP URL looks like:
#    rtsp://admin:pass@abc123.lhr.life:2222/cam/realmonitor?channel=2&subtype=0
#
#  Auto-reconnect: if tunnel drops, restarts within 5 seconds.
#  On app restart: _auto_restore() in GUI re-starts the tunnel immediately.
# ═══════════════════════════════════════════════════════════════════════════

# ── Configure YOUR relay server here (leave empty to use free public relays) ──
SHOWANDGO_RELAY_HOST = ""          # e.g. "relay.showandgo.com"
SHOWANDGO_RELAY_PORT = 0           # e.g. 2200  (a free port on your server)
SHOWANDGO_RELAY_USER = "tunnel"    # SSH user on your server
SHOWANDGO_RELAY_KEY  = ""          # path to private key, or "" for password-less
# ──────────────────────────────────────────────────────────────────────────


def _find_ssh():
    """Locate the ssh binary cross-platform."""
    # Explicit common paths first (avoids PATH issues on Windows)
    candidates = [
        "ssh",
        r"C:\Windows\System32\OpenSSH\ssh.exe",
        r"C:\Program Files\Git\usr\bin\ssh.exe",
        "/usr/bin/ssh",
        "/usr/local/bin/ssh",
    ]
    for c in candidates:
        p = shutil.which(c) or (c if os.path.isfile(c) else None)
        if p:
            return p
    return None


class TunnelEngine:
    def __init__(self, on_status):
        self.on_status = on_status   # callback(message: str, level: str)
        self._proc     = None
        self._running  = False
        self._pub_url  = None        # set when tunnel is live
        self._thread   = None

    # ── Public API ────────────────────────────────────────────────────────

    def start(self, local_rtsp_port=554):
        """Start the best available tunnel. Tries your server → localhost.run → serveo."""
        self._running = True
        self._thread  = threading.Thread(
            target=self._tunnel_loop, args=(local_rtsp_port,), daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._pub_url = None
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None

    @property
    def public_url(self):
        """Returns tcp://host:port when live, None otherwise."""
        return self._pub_url

    # ── Main loop ─────────────────────────────────────────────────────────

    def _tunnel_loop(self, local_port):
        ssh = _find_ssh()
        if not ssh:
            self.on_status(
                "SSH not found. Please install OpenSSH:\n"
                "Windows: Settings → Apps → Optional Features → OpenSSH Client",
                "error")
            return

        # Decide which backend to use
        if SHOWANDGO_RELAY_HOST:
            backend = ("your_server", SHOWANDGO_RELAY_HOST,
                       SHOWANDGO_RELAY_PORT, SHOWANDGO_RELAY_USER,
                       SHOWANDGO_RELAY_KEY)
        else:
            # Rotate between free public relays
            backend = ("localhost.run", None, None, None, None)

        while self._running:
            self._pub_url = None
            try:
                if backend[0] == "your_server":
                    self._run_your_server(ssh, local_port, *backend[1:])
                else:
                    self._run_localhost_run(ssh, local_port)
            except Exception as e:
                self.on_status(f"Tunnel error: {e}", "error")
            if self._running:
                self.on_status("Reconnecting in 5 s…", "scanning")
                time.sleep(5)

    # ── Backend: your own relay server ────────────────────────────────────

    def _run_your_server(self, ssh, local_port, host, remote_port, user, key):
        cmd = [ssh,
               "-N",
               "-o", "StrictHostKeyChecking=no",
               "-o", "ServerAliveInterval=20",
               "-o", "ServerAliveCountMax=3",
               "-o", "ExitOnForwardFailure=yes",
               "-R", f"{remote_port}:localhost:{local_port}",
               f"{user}@{host}"]
        if key:
            cmd += ["-i", key]

        self.on_status(f"Connecting to Show & Go relay ({host})…", "scanning")
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(2)
        if self._proc.poll() is None:
            self._pub_url = f"tcp://{host}:{remote_port}"
            self.on_status(f"Tunnel active on {host}:{remote_port}", "online")
            while self._running and self._proc.poll() is None:
                time.sleep(3)
            if self._running:
                self.on_status("Tunnel dropped", "scanning")
        else:
            err = self._proc.stderr.read(200).decode(errors="replace").strip()
            self.on_status(f"Relay connect failed: {err}", "error")
            time.sleep(8)

    # ── Backend: free public TCP relays ──────────────────────────────────
    #
    #  Why previous relays failed:
    #    - serveo.net  → rejects port 0 (dynamic assignment), needs SSH key
    #    - localhost.run → free tier is HTTPS only, not raw TCP
    #
    #  Working options for anonymous raw TCP (what RTSP needs):
    #    1. Pinggy  (a.pinggy.io, port 443)  — works with no account/key
    #       cmd:  ssh -p 443 -R0:localhost:554 a.pinggy.io
    #       prints: tcp://t-xxxxx.a.pinggy.link:PORT
    #
    #    2. sish (sish.sh) — open-source, works anonymously
    #       cmd:  ssh -R :0:localhost:554 sish.sh
    #       prints: sish.sh:PORT
    # ─────────────────────────────────────────────────────────────────────

    def _run_localhost_run(self, ssh, local_port):
        """Try Pinggy → sish in order. Both support anonymous raw TCP."""
        import re

        relays = [
            # (label, ssh_port, target, forward_spec, pattern, url_builder)
            (
                "Pinggy",
                443,                    # Pinggy listens on 443 (avoids firewall)
                "a.pinggy.io",
                f"0:localhost:{local_port}",
                r"tcp://([^\s]+)",      # prints: tcp://t-xxxx.a.pinggy.link:PORT
                lambda m: f"tcp://{m.group(1)}",
            ),
            (
                "sish",
                22,
                "sish.sh",
                f":0:localhost:{local_port}",
                r"sish\.sh:(\d+)",     # prints: sish.sh:PORT
                lambda m: f"tcp://sish.sh:{m.group(1)}",
            ),
        ]

        for label, relay_port, host, fwd, pattern, url_fn in relays:
            if not self._running:
                return

            self.on_status(f"Connecting via {label}…", "scanning")

            cmd = [
                ssh,
                "-p", str(relay_port),
                "-o", "StrictHostKeyChecking=no",
                "-o", "ServerAliveInterval=20",
                "-o", "ServerAliveCountMax=3",
                "-o", "ConnectTimeout=15",
                "-o", "ExitOnForwardFailure=no",
                "-R", fwd,
                host,
            ]

            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except Exception as e:
                self.on_status(f"{label} launch failed: {e}", "error")
                continue

            found = False
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                # Only log lines that look useful (suppress SSH noise)
                if any(x in line.lower() for x in
                       ("tcp://", "forwarding", "pinggy", "sish", "error",
                        "failed", "refused", "denied")):
                    self.on_status(line[:120], "info")

                m = re.search(pattern, line)
                if m:
                    pub_tcp = url_fn(m)
                    self._pub_url = pub_tcp
                    self.on_status(f"Tunnel live: {pub_tcp}", "online")
                    found = True

                if not self._running:
                    break

            if found and self._proc:
                # Keep alive while tunnel is running
                while self._running and self._proc.poll() is None:
                    time.sleep(3)
                if self._running:
                    self.on_status(f"{label} tunnel dropped, reconnecting…",
                                   "scanning")
                self._pub_url = None
                # Clean up process
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
                except Exception:
                    pass
                return   # return to outer loop which will reconnect

            # This relay didn't work — clean up and try next
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None
            self._pub_url = None

        # All relays failed
        self.on_status(
            "All free tunnels failed.\n"
            "Check internet connection, or set up your own relay server\n"
            "by filling in SHOWANDGO_RELAY_HOST at the top of gui_app.py.",
            "error"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  SHOWANDGO REGISTRAR
# ═══════════════════════════════════════════════════════════════════════════
class ShowAndGoRegistrar:
    """
    Registers the camera's public RTSP URL with the Show & Go server
    and sends periodic heartbeats so the server knows the tunnel is alive.

    The registration is saved locally so on restart the app can
    immediately re-register without the user doing anything.
    """

    def __init__(self, on_log):
        self.on_log   = on_log
        self._beat_thread = None
        self._running     = False
        self.camera_token = None   # returned by server on first registration

    def register(self, public_rtsp_url, camera_name, user_token,
                 on_done):
        """
        POST the public RTSP URL to Show & Go.
        on_done(success: bool, camera_token: str | None, message: str)
        """
        def _run():
            try:
                payload = json.dumps({
                    "rtsp_url":    public_rtsp_url,
                    "camera_name": camera_name,
                    "user_token":  user_token,
                }).encode()
                req = urllib.request.Request(
                    SHOWANDGO_REGISTER_URL,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    resp = json.loads(r.read())
                cam_token = resp.get("camera_token", "")
                self.camera_token = cam_token
                on_done(True, cam_token, "Camera registered successfully!")
                self._start_heartbeat(public_rtsp_url, cam_token)
            except Exception as e:
                on_done(False, None, str(e))
        threading.Thread(target=_run, daemon=True).start()

    def _start_heartbeat(self, rtsp_url, cam_token):
        self._running = True
        def _beat():
            while self._running:
                try:
                    payload = json.dumps({
                        "camera_token": cam_token,
                        "rtsp_url":     rtsp_url,
                        "alive":        True,
                    }).encode()
                    req = urllib.request.Request(
                        SHOWANDGO_HEARTBEAT_URL, data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST")
                    urllib.request.urlopen(req, timeout=5)
                    self.on_log("Heartbeat sent to Show & Go", "info")
                except Exception as e:
                    self.on_log(f"Heartbeat failed: {e}", "warn")
                time.sleep(30)
        self._beat_thread = threading.Thread(target=_beat, daemon=True)
        self._beat_thread.start()

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════════════════
#  STARTUP REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════
def install_startup(app_path):
    """Register the app to start on login (Windows & macOS)."""
    import sys
    system = sys.platform
    if system == "win32":
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "CamLink", 0, winreg.REG_SZ,
                          f'"{sys.executable}" "{app_path}"')
        winreg.CloseKey(key)
        return True, "Added to Windows startup."
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
        subprocess.run(["launchctl", "load", plist_path],
                       capture_output=True)
        return True, "Added to macOS login items."
    else:
        service = """[Unit]
Description=CamLink Camera Agent
After=network.target

[Service]
ExecStart={exe} {path}
Restart=always

[Install]
WantedBy=default.target
""".format(exe=sys.executable, path=app_path)
        svc_dir  = os.path.expanduser("~/.config/systemd/user/")
        os.makedirs(svc_dir, exist_ok=True)
        svc_path = os.path.join(svc_dir, "camlink.service")
        with open(svc_path, "w") as f:
            f.write(service)
        subprocess.run(["systemctl", "--user", "enable", "camlink.service"],
                       capture_output=True)
        subprocess.run(["systemctl", "--user", "start",  "camlink.service"],
                       capture_output=True)
        return True, "Installed as systemd user service."


def uninstall_startup():
    import sys
    system = sys.platform
    try:
        if system == "win32":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, "CamLink")
            winreg.CloseKey(key)
        elif system == "darwin":
            plist_path = os.path.expanduser(
                "~/Library/LaunchAgents/com.camlink.agent.plist")
            subprocess.run(["launchctl", "unload", plist_path],
                           capture_output=True)
            if os.path.exists(plist_path):
                os.remove(plist_path)
        else:
            subprocess.run(["systemctl", "--user", "stop",    "camlink.service"],
                           capture_output=True)
            subprocess.run(["systemctl", "--user", "disable", "camlink.service"],
                           capture_output=True)
        return True, "Removed from startup."
    except Exception as e:
        return False, str(e)


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
        self.camera_list         = []
        self.target_ip           = None
        self.username_var        = tk.StringVar(value="admin")
        self.password_var        = tk.StringVar()
        self.cam_name_var        = tk.StringVar(value="Main Entrance")
        self.discovered_channels = []
        self.selected_channel    = None
        self.selected_rtsp_url   = None   # the URL for the chosen channel
        self.working_url         = None   # first URL that opened on connect
        self.cap                 = None   # discovery cap (not used for preview)
        self.preview_cap         = None   # fresh cap opened per channel selection
        self.is_streaming        = False
        self.frame_count         = 0
        self.upload_count        = 0
        self.last_upload         = 0
        self.stream_start_time   = 0
        self.current_step        = 0
        self.cam_widgets         = {}
        self.ch_widgets          = {}
        self.sel_cam_w           = None
        self.sel_ch_w            = None
        self.tunnel_engine       = TunnelEngine(self._on_tunnel_status)
        self.registrar           = ShowAndGoRegistrar(self.log)
        self.tunnel_active       = False
        self.public_rtsp_url     = None
        self.startup_installed   = False

        self._build_ui()
        self._auto_restore()   # re-register if a saved session exists

    # ═══════════════════════════════════════════════════════════════════════
    #  UI SKELETON
    # ═══════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        # Top bar
        bar = tk.Frame(self.root, bg=T.SURFACE,
                       highlightbackground=T.BORDER, highlightthickness=1, height=64)
        bar.pack(fill="x"); bar.pack_propagate(False)

        logo = tk.Frame(bar, bg=T.SURFACE)
        logo.pack(side="left", padx=28, fill="y")
        tk.Label(logo, text="CamLink", bg=T.SURFACE, fg=T.BRAND,
                 font=(T.SANS, 20, "bold")).pack(side="left", pady=18)
        tk.Label(logo, text="  WiFi Camera Manager", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS, 11)).pack(side="left", pady=18)

        self.api_badge = StatusBadge(bar, "scanning", text=" ◌ Starting ")
        self.api_badge.pack(side="right", padx=28, pady=18)

        # Step bar
        self.step_bar = StepBar(self.root)
        self.step_bar.pack(fill="x")
        self.step_bar.set_step(0)
        tk.Frame(self.root, bg=T.BORDER, height=1).pack(fill="x")

        # Body
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
            4: self._panel_share(),
        }
        self._show(0)
        self._start_mock_api()

    # ═══════════════════════════════════════════════════════════════════════
    #  RIGHT PANEL (always visible)
    # ═══════════════════════════════════════════════════════════════════════
    def _build_right(self):
        # Video
        vc = Card(self.right, title="Live Preview")
        vc.pack(fill="both", expand=True, pady=(0,12))

        self.video_ph = tk.Label(vc, text="No camera stream active",
                                  bg=T.SURFACE2, fg=T.TEXT_D,
                                  font=(T.SANS,13), height=12, relief="flat")
        self.video_ph.pack(fill="both", expand=True, padx=20, pady=(12,8))
        if PIL_AVAILABLE and CV2_AVAILABLE:
            self.video_lbl = tk.Label(vc, bg="#000")
            self.video_lbl.pack_forget()

        stats = tk.Frame(vc, bg=T.SURFACE)
        stats.pack(fill="x", padx=20, pady=(0,14))
        self.frames_lbl = tk.Label(stats, text="Frames: –",
                                    bg=T.SURFACE, fg=T.TEXT_M, font=(T.SANS,9))
        self.frames_lbl.pack(side="left")
        self.uptime_lbl = tk.Label(stats, text="Uptime: –",
                                    bg=T.SURFACE, fg=T.TEXT_M, font=(T.SANS,9))
        self.uptime_lbl.pack(side="left", padx=20)
        self.stream_badge = StatusBadge(stats, "idle", text=" – IDLE ")
        self.stream_badge.pack(side="right")

        # Public URL display
        uc = Card(self.right, title="Public RTSP URL")
        uc.pack(fill="x", pady=(0,12))
        ui = tk.Frame(uc, bg=T.SURFACE)
        ui.pack(fill="x", padx=20, pady=(8,16))
        self.pub_url_lbl = tk.Label(ui,
            text="Complete the setup below to generate your public URL.",
            bg=T.BG, fg=T.TEXT_M, font=(T.MONO,9),
            anchor="w", justify="left", padx=10, pady=8, wraplength=440,
            relief="flat", highlightbackground=T.BORDER, highlightthickness=1)
        self.pub_url_lbl.pack(fill="x", pady=(0,10))
        br = tk.Frame(ui, bg=T.SURFACE)
        br.pack(fill="x")
        StyledButton(br, "Copy URL", variant="ghost", width=120, height=34,
                     command=self._copy_url, bg=T.SURFACE).pack(side="left")
        self.copy_status = tk.Label(br, text="", bg=T.SURFACE, fg=T.GREEN,
                                     font=(T.SANS,9))
        self.copy_status.pack(side="left", padx=12)

        # Log
        lc = Card(self.right, title="Activity Log")
        lc.pack(fill="x")
        self.log_box = scrolledtext.ScrolledText(
            lc, font=(T.MONO,9), bg=T.BG, fg=T.TEXT_B,
            insertbackground=T.TEXT_B, relief="flat", bd=0,
            height=7, wrap="word", highlightthickness=0)
        self.log_box.pack(fill="x", padx=20, pady=(4,16))
        for tag, col in [("success",T.GREEN),("error",T.RED),
                          ("warn",T.AMBER),("info",T.BLUE),("ts",T.TEXT_D)]:
            self.log_box.tag_configure(tag, foreground=col)

    # ═══════════════════════════════════════════════════════════════════════
    #  STEP PANELS
    # ═══════════════════════════════════════════════════════════════════════
    def _show(self, idx):
        for p in self.panels.values(): p.pack_forget()
        self.panels[idx].pack(fill="both", expand=True)
        self.current_step = idx
        self.step_bar.set_step(idx)

    # ── Step 0: Discover ─────────────────────────────────────────────────
    def _panel_discover(self):
        p = tk.Frame(self.left, bg=T.BG)
        tk.Label(p, text="Discover Cameras", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS,18,"bold")).pack(anchor="w")
        tk.Label(p, text="Scan your local network to find WiFi cameras.",
                 bg=T.BG, fg=T.TEXT_M, font=(T.SANS,10)).pack(anchor="w", pady=(4,14))

        ac = Card(p); ac.pack(fill="x", pady=(0,12))
        ai = tk.Frame(ac, bg=T.SURFACE); ai.pack(fill="x", padx=20, pady=16)
        br = tk.Frame(ai, bg=T.SURFACE); br.pack(fill="x")
        self.scan_btn = StyledButton(br, "Scan Network", variant="primary",
                                      width=150, height=40,
                                      command=self._auto_scan, bg=T.SURFACE)
        self.scan_btn.pack(side="left")
        self.scan_status = tk.Label(br, text="", bg=T.SURFACE, fg=T.TEXT_M,
                                     font=(T.SANS,9))
        self.scan_status.pack(side="left", padx=14)

        tk.Frame(ai, bg=T.BORDER, height=1).pack(fill="x", pady=16)
        tk.Label(ai, text="Or enter IP manually", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS,9)).pack(anchor="w")
        ir = tk.Frame(ai, bg=T.SURFACE); ir.pack(fill="x", pady=(6,0))
        self.ip_var = tk.StringVar()
        self._ip_e = tk.Entry(ir, textvariable=self.ip_var,
                               font=(T.SANS,11), bg=T.BG, fg=T.TEXT_D,
                               insertbackground=T.TEXT_B, relief="flat", bd=0,
                               highlightbackground=T.BORDER, highlightthickness=1)
        self._ip_e.insert(0, "e.g. 192.168.1.100")
        self._ip_e.bind("<FocusIn>",
            lambda e: (self._ip_e.delete(0,"end"),
                       self._ip_e.config(fg=T.TEXT_B))
                       if self._ip_e.get().startswith("e.g") else None)
        self._ip_e.bind("<FocusOut>",
            lambda e: (self._ip_e.insert(0,"e.g. 192.168.1.100"),
                       self._ip_e.config(fg=T.TEXT_D))
                       if not self._ip_e.get() else None)
        self._ip_e.pack(side="left", fill="x", expand=True, ipady=8, ipadx=8)
        StyledButton(ir, "Check", variant="ghost", width=80, height=36,
                     command=self._check_manual, bg=T.SURFACE).pack(
                         side="right", padx=(8,0))

        tc = Card(p, title="Found Cameras"); tc.pack(fill="both", expand=True)
        li = tk.Frame(tc, bg=T.SURFACE); li.pack(fill="both", expand=True,
                                                   padx=20, pady=(8,16))
        self.cam_frame = tk.Frame(li, bg=T.SURFACE); self.cam_frame.pack(
            fill="both", expand=True)
        self.no_cam_lbl = tk.Label(self.cam_frame,
            text="No cameras found yet.\nClick 'Scan Network' to search.",
            bg=T.SURFACE, fg=T.TEXT_D, font=(T.SANS,10), justify="center")
        self.no_cam_lbl.pack(expand=True, pady=30)

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(14,0))
        self.next0 = StyledButton(nav, "Continue →", variant="primary",
                                   width=200, height=40,
                                   command=lambda: self._show(1), bg=T.BG)
        self.next0.pack(side="right"); self.next0.disable()
        return p

    # ── Step 1: Credentials ──────────────────────────────────────────────
    def _panel_credentials(self):
        p = tk.Frame(self.left, bg=T.BG)
        tk.Label(p, text="Camera Credentials", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS,18,"bold")).pack(anchor="w")
        tk.Label(p, text="Enter the login details for your camera.",
                 bg=T.BG, fg=T.TEXT_M, font=(T.SANS,10)).pack(anchor="w", pady=(4,14))

        ac = Card(p); ac.pack(fill="x", pady=(0,12))
        ai = tk.Frame(ac, bg=T.SURFACE); ai.pack(fill="x", padx=20, pady=16)

        tk.Label(ai, text="Selected Camera", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS,9)).pack(anchor="w")
        self.sel_cam_pill = tk.Label(ai, text="None",
                                      bg=T.BRAND_LT, fg=T.BRAND,
                                      font=(T.SANS,12,"bold"), padx=14, pady=6,
                                      anchor="w")
        self.sel_cam_pill.pack(fill="x", pady=(4,16))

        self.user_ent = LabeledEntry(ai, "Username", variable=self.username_var)
        self.user_ent.pack(fill="x", pady=(0,10))
        self.pass_ent = LabeledEntry(ai, "Password",
                                      variable=self.password_var, show="●")
        self.pass_ent.pack(fill="x", pady=(0,16))

        self.conn_btn = StyledButton(ai, "Connect", variant="primary",
                                      width=160, height=40,
                                      command=self._connect_camera, bg=T.SURFACE)
        self.conn_btn.pack(anchor="w")
        self.conn_status = tk.Label(ai, text="", bg=T.SURFACE, fg=T.TEXT_M,
                                     font=(T.SANS,9), wraplength=360, justify="left")
        self.conn_status.pack(anchor="w", pady=(8,0))

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(14,0))
        StyledButton(nav, "← Back", variant="ghost", width=100, height=38,
                     command=lambda: self._show(0), bg=T.BG).pack(side="left")
        return p

    # ── Step 2: Channels ─────────────────────────────────────────────────
    def _panel_channels(self):
        p = tk.Frame(self.left, bg=T.BG)
        tk.Label(p, text="Select Channel", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS,18,"bold")).pack(anchor="w")
        tk.Label(p, text="Choose a video channel from your camera.",
                 bg=T.BG, fg=T.TEXT_M, font=(T.SANS,10)).pack(anchor="w", pady=(4,14))

        pr = tk.Frame(p, bg=T.BG); pr.pack(fill="x", pady=(0,10))
        self.probe_btn = StyledButton(pr, "Auto-Detect Channels", variant="primary",
                                       width=200, height=38,
                                       command=self._probe_channels, bg=T.BG)
        self.probe_btn.pack(side="left")
        self.probe_status = tk.Label(pr, text="", bg=T.BG, fg=T.TEXT_M,
                                      font=(T.SANS,9))
        self.probe_status.pack(side="left", padx=12)

        lc = Card(p, title="Available Channels"); lc.pack(fill="both", expand=True,
                                                            pady=(0,12))
        self.ch_frame = tk.Frame(lc, bg=T.SURFACE)
        self.ch_frame.pack(fill="both", expand=True, padx=20, pady=(8,16))
        self.no_ch_lbl = tk.Label(self.ch_frame,
            text="No channels detected.\nClick 'Auto-Detect Channels'.",
            bg=T.SURFACE, fg=T.TEXT_D, font=(T.SANS,10), justify="center")
        self.no_ch_lbl.pack(expand=True, pady=30)

        mc = Card(p, title="Or enter manually"); mc.pack(fill="x", pady=(0,12))
        mci = tk.Frame(mc, bg=T.SURFACE); mci.pack(fill="x", padx=20, pady=(8,16))
        mr = tk.Frame(mci, bg=T.SURFACE); mr.pack(fill="x")
        tk.Label(mr, text="Channel #:", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS,10)).pack(side="left")
        self.manual_ch_var = tk.StringVar(value="1")
        tk.Entry(mr, textvariable=self.manual_ch_var, width=5,
                 font=(T.SANS,11), bg=T.BG, fg=T.TEXT_B, relief="flat", bd=0,
                 highlightbackground=T.BORDER, highlightthickness=1).pack(
                     side="left", ipady=6, padx=(8,8))
        self.subtype_var = tk.StringVar(value="0")
        tk.Label(mr, text="Stream:", bg=T.SURFACE, fg=T.TEXT_M,
                 font=(T.SANS,10)).pack(side="left")
        ttk.Combobox(mr, textvariable=self.subtype_var,
                     values=["0 (Main)", "1 (Sub)"], width=10,
                     font=(T.SANS,10)).pack(side="left", padx=(8,8))
        StyledButton(mr, "Use This", variant="ghost", width=90, height=34,
                     command=self._use_manual_ch, bg=T.SURFACE).pack(side="left")

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(4,0))
        StyledButton(nav, "← Back", variant="ghost", width=100, height=38,
                     command=lambda: self._show(1), bg=T.BG).pack(side="left")
        self.next2 = StyledButton(nav, "Preview →", variant="primary",
                                   width=140, height=38,
                                   command=lambda: self._show(3), bg=T.BG)
        self.next2.pack(side="right"); self.next2.disable()
        return p

    # ── Step 3: Preview ──────────────────────────────────────────────────
    def _panel_preview(self):
        p = tk.Frame(self.left, bg=T.BG)
        tk.Label(p, text="Live Preview", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS,18,"bold")).pack(anchor="w")
        tk.Label(p, text="Verify the stream is correct before sharing.",
                 bg=T.BG, fg=T.TEXT_M, font=(T.SANS,10)).pack(anchor="w", pady=(4,14))

        ic = Card(p, title="Active Stream"); ic.pack(fill="x", pady=(0,12))
        ici = tk.Frame(ic, bg=T.SURFACE); ici.pack(fill="x", padx=20, pady=(8,16))
        self.active_url_lbl = tk.Label(ici, text="No stream selected.",
                                        bg=T.SURFACE, fg=T.TEXT_M,
                                        font=(T.MONO,9), wraplength=380, justify="left")
        self.active_url_lbl.pack(anchor="w")

        cc = Card(p, title="Stream Controls"); cc.pack(fill="x", pady=(0,12))
        cci = tk.Frame(cc, bg=T.SURFACE); cci.pack(fill="x", padx=20, pady=(8,16))
        br = tk.Frame(cci, bg=T.SURFACE); br.pack(fill="x")
        self.preview_btn = StyledButton(br, "▶  Start Preview", variant="success",
                                         width=160, height=40,
                                         command=self._toggle_stream, bg=T.SURFACE)
        self.preview_btn.pack(side="left")
        StyledButton(br, "■  Stop", variant="danger", width=100, height=40,
                     command=self._stop_stream, bg=T.SURFACE).pack(
                         side="left", padx=(10,0))

        nc = Card(p, title="Camera Label")
        nc.pack(fill="x", pady=(0,12))
        nci = tk.Frame(nc, bg=T.SURFACE); nci.pack(fill="x", padx=20, pady=(8,16))
        LabeledEntry(nci, "Display Name (shown in Show & Go)",
                     variable=self.cam_name_var).pack(fill="x")

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(14,0))
        StyledButton(nav, "← Back", variant="ghost", width=100, height=38,
                     command=lambda: self._show(2), bg=T.BG).pack(side="left")
        StyledButton(nav, "Get Public URL →", variant="primary", width=180, height=38,
                     command=lambda: self._show(4), bg=T.BG).pack(side="right")
        return p

    # ── Step 4: Share (redesigned) ───────────────────────────────────────
    def _panel_share(self):
        p = tk.Frame(self.left, bg=T.BG)

        # ── Header ──
        tk.Label(p, text="Get Your Public URL", bg=T.BG, fg=T.TEXT_H,
                 font=(T.SANS,18,"bold")).pack(anchor="w")
        tk.Label(p,
            text="CamLink creates a secure tunnel using SSH — already built\n"
                 "into your PC. No extra software or accounts needed.",
            bg=T.BG, fg=T.TEXT_M, font=(T.SANS,10), justify="left").pack(
                 anchor="w", pady=(4,14))

        # ── Big one-click action card ──
        action = Card(p); action.pack(fill="x", pady=(0,14))
        ai = tk.Frame(action, bg=T.SURFACE); ai.pack(fill="x", padx=20, pady=20)

        # Status indicator row
        si = tk.Frame(ai, bg=T.SURFACE); si.pack(fill="x", pady=(0,16))
        self.tunnel_badge = StatusBadge(si, "idle", text=" – NOT STARTED ")
        self.tunnel_badge.pack(side="left")
        self.tunnel_detail = tk.Label(si, text="",
                                       bg=T.SURFACE, fg=T.TEXT_M,
                                       font=(T.SANS,9), wraplength=280, justify="left")
        self.tunnel_detail.pack(side="left", padx=12)

        # The Big Button
        self.start_share_btn = StyledButton(
            ai, "▶  Start Sharing", variant="primary",
            width=380, height=52,
            command=self._start_sharing, bg=T.SURFACE)
        self.start_share_btn.pack(fill="x")

        self.stop_share_btn = StyledButton(
            ai, "■  Stop Sharing", variant="danger",
            width=380, height=52,
            command=self._stop_sharing, bg=T.SURFACE)
        self.stop_share_btn.pack(fill="x")
        self.stop_share_btn.pack_forget()

        # Generated URL box
        url_card = Card(p, title="Your Public RTSP URL")
        url_card.pack(fill="x", pady=(0,14))
        ui = tk.Frame(url_card, bg=T.SURFACE); ui.pack(fill="x", padx=20, pady=(8,16))

        self.gen_url_lbl = tk.Label(ui,
            text="Your URL will appear here after starting.",
            bg=T.BG, fg=T.TEXT_M, font=(T.MONO,9),
            anchor="w", padx=10, pady=10, wraplength=380, justify="left",
            relief="flat", highlightbackground=T.BORDER, highlightthickness=1)
        self.gen_url_lbl.pack(fill="x", pady=(0,10))

        cb = tk.Frame(ui, bg=T.SURFACE); cb.pack(fill="x")
        StyledButton(cb, "📋  Copy URL", variant="ghost", width=130, height=34,
                     command=self._copy_url, bg=T.SURFACE).pack(side="left")
        self.copy_lbl = tk.Label(cb, text="", bg=T.SURFACE, fg=T.GREEN,
                                  font=(T.SANS,9))
        self.copy_lbl.pack(side="left", padx=10)

        # ── How to use ──
        htu = Card(p, title="How to use this URL")
        htu.pack(fill="x", pady=(0,14))
        htui = tk.Frame(htu, bg=T.SURFACE); htui.pack(fill="x", padx=20, pady=(8,16))
        steps = [
            ("1", "Copy the URL above."),
            ("2", "Open Show & Go  →  Settings  →  Add Camera."),
            ("3", "Paste the URL and click Save."),
            ("4", "Keep this app running while the camera is in use."),
        ]
        for num, txt in steps:
            row = tk.Frame(htui, bg=T.SURFACE); row.pack(fill="x", pady=3)
            tk.Label(row, text=num, bg=T.BRAND, fg="#fff",
                     font=(T.SANS,10,"bold"), width=2, pady=2).pack(side="left")
            tk.Label(row, text=txt, bg=T.SURFACE, fg=T.TEXT_B,
                     font=(T.SANS,10), padx=12, anchor="w").pack(
                         side="left", fill="x", expand=True)

        # ── "Keep this running" section ──
        kc = Card(p, title="Run automatically at startup (recommended)")
        kc.pack(fill="x", pady=(0,14))
        kci = tk.Frame(kc, bg=T.SURFACE); kci.pack(fill="x", padx=20, pady=(8,16))

        tk.Label(kci,
            text="Important: this app (and your PC) must stay running for\n"
                 "the camera to be reachable over the internet.\n\n"
                 "Enable below to start CamLink automatically every time\n"
                 "your PC boots — so it reconnects without you doing anything.",
            bg=T.SURFACE, fg=T.TEXT_M, font=(T.SANS,9), justify="left").pack(anchor="w")

        sb = tk.Frame(kci, bg=T.SURFACE); sb.pack(fill="x", pady=(10,0))
        self.startup_btn = StyledButton(sb, "Enable Auto-Start", variant="success",
                                         width=180, height=36,
                                         command=self._toggle_startup, bg=T.SURFACE)
        self.startup_btn.pack(side="left")
        self.startup_lbl = tk.Label(sb, text="", bg=T.SURFACE, fg=T.TEXT_M,
                                     font=(T.SANS,9))
        self.startup_lbl.pack(side="left", padx=12)

        nav = tk.Frame(p, bg=T.BG); nav.pack(fill="x", pady=(0,0))
        StyledButton(nav, "← Back", variant="ghost", width=100, height=38,
                     command=lambda: self._show(3), bg=T.BG).pack(side="left")
        return p

    # ═══════════════════════════════════════════════════════════════════════
    #  DISCOVERY
    # ═══════════════════════════════════════════════════════════════════════
    def _auto_scan(self):
        self.scan_btn.set_text("Scanning…")
        self.scan_status.config(text="Scanning…", fg=T.AMBER)
        for w in list(self.cam_widgets.values()): w.destroy()
        self.cam_widgets.clear()
        self.no_cam_lbl.pack(expand=True, pady=30)
        self.log("Starting network scan…", "info")
        threading.Thread(
            target=lambda: self.root.after(0, lambda: self._on_scan_done(scan_network())),
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
            self.log("No cameras found", "warn")
            messagebox.showwarning("No Cameras Found",
                "No cameras with port 554 open.\n\n"
                "• Same WiFi network?\n• RTSP enabled on camera?\n"
                "• Try entering IP manually.")

    def _add_cam(self, ip):
        w = CameraItem(self.cam_frame, ip, self._select_cam)
        w.pack(fill="x", pady=(0,6))
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
            messagebox.showerror("Not Found", f"No RTSP camera at {ip}.")

    def _select_cam(self, ip, widget):
        if self.sel_cam_w: self.sel_cam_w.deselect()
        self.sel_cam_w = widget; widget.select()
        self.target_ip = ip
        self.sel_cam_pill.config(text=f"  {ip}  ")
        self.log(f"Selected camera: {ip}", "info")
        cfg  = self._load_cfg()
        saved = cfg.get("cameras", {}).get(ip, {})
        if saved.get("username"):
            self.username_var.set(saved["username"])
            self.password_var.set(saved.get("password",""))
            self.log("Loaded saved credentials", "success")

    # ═══════════════════════════════════════════════════════════════════════
    #  CREDENTIALS
    # ═══════════════════════════════════════════════════════════════════════
    def _connect_camera(self):
        if not self.target_ip:
            messagebox.showerror("Error", "Select a camera first."); return
        if not self.password_var.get():
            messagebox.showerror("Error", "Password required."); return
        self.conn_btn.set_text("Connecting…")
        self.conn_status.config(text="Testing RTSP paths…", fg=T.AMBER)
        self.log(f"Connecting to {self.target_ip}…", "info")

        def _r():
            cfg   = self._load_cfg()
            saved = cfg.get("cameras",{}).get(self.target_ip,{})
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
        self.conn_btn.set_text("Connect")
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
                "Could not connect.\n\n• Wrong username/password\n"
                "• RTSP not enabled\n• Same network?")

    # ═══════════════════════════════════════════════════════════════════════
    #  CHANNELS
    # ═══════════════════════════════════════════════════════════════════════
    def _probe_channels(self):
        if not self.target_ip or not self.working_url:
            messagebox.showwarning("Not Connected", "Connect to a camera first."); return
        self.probe_btn.set_text("Detecting…")
        self.probe_status.config(text="Probing…", fg=T.AMBER)
        self.log("Auto-detecting channels…", "info")
        for w in list(self.ch_widgets.values()): w.destroy()
        self.ch_widgets.clear()
        self.no_ch_lbl.pack(expand=True, pady=30)
        threading.Thread(
            target=lambda: self.root.after(0,
                lambda: self._on_channels_found(self._discover_channels())),
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
                            found.append({"channel":ch,"subtype":sub,"label":label,"url":url})
                            self.root.after(0, lambda l=label: self.log(f"Found: {l}", "success"))
                            break
                else:
                    if ch <= 2:
                        found.append({"channel":ch,"subtype":sub,"label":label,"url":url})
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
        w.pack(fill="x", pady=(0,6))
        self.ch_widgets[ch["channel"]] = w

    def _select_ch(self, ch_num, url, widget):
        if self.sel_ch_w: self.sel_ch_w.deselect()
        self.sel_ch_w = widget; widget.select()
        self.selected_channel  = ch_num
        self.selected_rtsp_url = url       # ← the URL we'll actually stream
        self.active_url_lbl.config(text=url)
        self.log(f"Selected channel {ch_num}: {url}", "info")
        self.next2.enable(lambda: self._show(3))
        # If a stream was already running, restart it on the new channel
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

    # ═══════════════════════════════════════════════════════════════════════
    #  STREAMING  (preview)
    #  KEY FIX: always open a fresh VideoCapture from selected_rtsp_url.
    #  Never reuse self.cap (discovery connection – wrong channel).
    # ═══════════════════════════════════════════════════════════════════════
    def _toggle_stream(self):
        if self.is_streaming: self._stop_stream()
        else: self._start_stream()

    def _start_stream(self):
        url = self.selected_rtsp_url or self.working_url
        if not url:
            messagebox.showerror("Error", "No camera channel selected."); return
        if not CV2_AVAILABLE or not PIL_AVAILABLE:
            self.stream_badge.set("connected", "SIMULATED")
            self.preview_btn.set_text("⏸  Pause"); return

        # Release any old preview cap
        if self.preview_cap:
            try: self.preview_cap.release()
            except: pass

        # Open a NEW connection to the selected channel URL
        self.preview_cap = cv2.VideoCapture(url)
        if not self.preview_cap.isOpened():
            self.log(f"Could not open stream: {url}", "error")
            messagebox.showerror("Error",
                "Could not open the selected channel.\n"
                "Check the camera is still reachable."); return

        self.is_streaming      = True
        self.stream_start_time = time.time()
        self.frame_count       = 0
        self.preview_btn.set_text("⏸  Pause")
        self.stream_badge.set("online", "LIVE")
        self.log(f"Preview started: {url}", "success")
        self.video_ph.pack_forget()
        self.video_lbl.pack(fill="both", expand=True, padx=20, pady=(12,8))
        self._update_frame()

    def _stop_stream(self):
        self.is_streaming = False
        self.preview_btn.set_text("▶  Start Preview")
        self.stream_badge.set("idle", "STOPPED")
        self.log("Stream stopped", "warn")
        if PIL_AVAILABLE and CV2_AVAILABLE:
            try: self.video_lbl.pack_forget()
            except: pass
        if self.preview_cap:
            try: self.preview_cap.release()
            except: pass
            self.preview_cap = None
        self.video_ph.pack(fill="both", expand=True, padx=20, pady=(12,8))

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
            self.root.after(500, self._update_frame)
            return
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

    # ═══════════════════════════════════════════════════════════════════════
    #  SHARE – one-click tunnel
    # ═══════════════════════════════════════════════════════════════════════
    def _start_sharing(self):
        url = self.selected_rtsp_url or self.working_url
        if not url:
            messagebox.showwarning("No Channel Selected",
                "Please complete steps 1–3 and select a channel first."); return

        self.start_share_btn.pack_forget()
        self.stop_share_btn.pack(fill="x")
        self.tunnel_badge.set("scanning", "CONNECTING…")
        self.tunnel_detail.config(text="Opening secure tunnel…")
        self.log("Starting tunnel…", "info")

        # Start the best available tunnel
        # (your relay server if configured, otherwise free localhost.run via SSH)
        self.tunnel_engine.start(local_rtsp_port=554)

        # Poll until TunnelEngine reports a public address
        self._poll_tunnel(url, attempts=0)

    def _poll_tunnel(self, local_rtsp, attempts):
        """
        Poll TunnelEngine.public_url every 500ms.
        localhost.run can take up to 20 seconds to print the assigned address,
        so we wait up to 60 attempts (30 seconds) before giving up.
        """
        pub_tcp = self.tunnel_engine.public_url
        if pub_tcp:
            # pub_tcp looks like "tcp://abc.lhr.life:12345"
            host_port = pub_tcp.replace("tcp://", "")
            path      = RTSPBuilder().extract_path(local_rtsp) or "/cam/realmonitor?channel=1&subtype=0"
            user = urllib.parse.quote(self.username_var.get(), safe="")
            pwd  = urllib.parse.quote(self.password_var.get(), safe="")
            public_rtsp = f"rtsp://{user}:{pwd}@{host_port}{path}"
            self.public_rtsp_url = public_rtsp
            self._on_public_url_ready(public_rtsp)
        elif attempts > 60:          # 30 seconds
            self.tunnel_badge.set("offline", "FAILED")
            self.tunnel_detail.config(
                text="Could not open tunnel. Check your internet connection\n"
                     "and that SSH (OpenSSH) is installed on this PC.\n\n"
                     "Windows: Settings → Apps → Optional Features → OpenSSH Client")
            self.stop_share_btn.pack_forget()
            self.start_share_btn.pack(fill="x")
            self.log("Tunnel failed to start — check internet / SSH", "error")
        else:
            # Still waiting — update the progress dot in the status badge
            dots = ["CONNECTING", "CONNECTING.", "CONNECTING..", "CONNECTING..."]
            self.tunnel_badge.set("scanning", dots[attempts % 4])
            self.root.after(500, lambda: self._poll_tunnel(local_rtsp, attempts+1))

    def _on_public_url_ready(self, public_rtsp):
        self.tunnel_active = True
        self.tunnel_badge.set("online", "ACTIVE")
        self.tunnel_detail.config(text="Tunnel is live. Keep this app running.")

        # Update both URL displays
        self.gen_url_lbl.config(text=public_rtsp, fg=T.TEXT_B)
        self.pub_url_lbl.config(text=public_rtsp, fg=T.TEXT_B)
        self.log(f"Public RTSP ready: {public_rtsp}", "success")

        # Save to config for auto-restore on restart
        cfg = self._load_cfg()
        cfg["last_public_rtsp"]  = public_rtsp
        cfg["last_local_rtsp"]   = self.selected_rtsp_url or self.working_url
        cfg["last_camera_name"]  = self.cam_name_var.get()
        self._save_cfg(cfg)

    def _stop_sharing(self):
        self.tunnel_engine.stop()
        self.tunnel_active   = False
        self.public_rtsp_url = None
        self.tunnel_badge.set("idle", "STOPPED")
        self.tunnel_detail.config(text="Tunnel stopped.")
        self.gen_url_lbl.config(
            text="Your URL will appear here after starting.",
            fg=T.TEXT_M)
        self.stop_share_btn.pack_forget()
        self.start_share_btn.pack(fill="x")
        self.log("Tunnel stopped", "warn")

    def _on_tunnel_status(self, message, status):
        """Callback from TunnelEngine on status changes."""
        colors = {"online": T.GREEN, "error": T.RED, "scanning": T.AMBER}
        def _update():
            self.tunnel_detail.config(
                text=message,
                fg=colors.get(status, T.TEXT_M))
            if status == "online":
                self.tunnel_badge.set("online", "ACTIVE")
            elif status == "error":
                self.tunnel_badge.set("offline", "ERROR")
            elif status == "scanning":
                self.tunnel_badge.set("scanning", "RECONNECTING…")
            self.log(message, "info" if status=="online" else status)
        self.root.after(0, _update)

    # ── Auto-restore on startup ───────────────────────────────────────────
    def _auto_restore(self):
        """
        If a previous session was saved (camera + tunnel was working),
        show an offer to reconnect automatically.
        """
        cfg = self._load_cfg()
        last_ip   = cfg.get("last_ip")
        last_pub  = cfg.get("last_public_rtsp")
        if last_ip and last_pub:
            self.log(f"Previous session found for {last_ip}", "info")
            self.log("Go to Share step to reconnect.", "info")

    # ── Startup installation ──────────────────────────────────────────────
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

    # ═══════════════════════════════════════════════════════════════════════
    #  URL HELPERS
    # ═══════════════════════════════════════════════════════════════════════
    def _copy_url(self):
        url = self.public_rtsp_url or (self.gen_url_lbl.cget("text")
                                        if "rtsp://" in self.gen_url_lbl.cget("text")
                                        else None)
        if url:
            self.root.clipboard_clear(); self.root.clipboard_append(url)
            for lbl in (self.copy_status, self.copy_lbl):
                lbl.config(text="✓ Copied!", fg=T.GREEN)
            self.root.after(2500,
                lambda: [l.config(text="") for l in (self.copy_status, self.copy_lbl)])

    # ═══════════════════════════════════════════════════════════════════════
    #  MOCK API (for testing the heartbeat/upload path locally)
    # ═══════════════════════════════════════════════════════════════════════
    def _start_mock_api(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler

        class H(BaseHTTPRequestHandler):
            def do_POST(h):
                if h.path in ("/api/recognize", "/cameras/register",
                               "/cameras/heartbeat"):
                    length = int(h.headers.get("Content-Length", 0))
                    body   = h.rfile.read(length)
                    try:
                        data = json.loads(body.decode())
                        resp = json.dumps({"status": "ok",
                                           "camera_token": "tok_demo_abc123"})
                        h.send_response(200)
                        h.send_header("Content-type", "application/json")
                        h.end_headers()
                        h.wfile.write(resp.encode())
                    except Exception:
                        h.send_response(500); h.end_headers()
            def log_message(h, *a): pass

        def _r():
            try:
                srv = HTTPServer(("localhost", MOCK_API_PORT), H)
                self.root.after(0, lambda: (
                    self.api_badge.set("online", "API READY"),
                    self.log("Local API mock running on :5000", "success")))
                srv.serve_forever()
            except Exception as e:
                self.root.after(0, lambda: self.log(f"API error: {e}", "error"))
        threading.Thread(target=_r, daemon=True).start()

    # ═══════════════════════════════════════════════════════════════════════
    #  LOGGING
    # ═══════════════════════════════════════════════════════════════════════
    def log(self, message, level="info"):
        icons = {"success":"✓","error":"✗","warn":"⚠","info":"·"}
        ts    = datetime.now().strftime("%H:%M:%S")
        def _ins():
            self.log_box.insert("end", f"[{ts}] ", "ts")
            self.log_box.insert("end", f"{icons.get(level,'·')} {message}\n", level)
            self.log_box.see("end")
        self.root.after(0, _ins)

    # ═══════════════════════════════════════════════════════════════════════
    #  CONFIG
    # ═══════════════════════════════════════════════════════════════════════
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