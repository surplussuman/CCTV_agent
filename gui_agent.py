"""
SmartEntry Agent - Professional CCTV Bridge Application
Industry-grade GUI with step-by-step workflow
"""

import customtkinter as ctk
from PIL import Image, ImageTk
import cv2
import threading
import time
from datetime import datetime
import sys
import os
from contextlib import contextmanager

# Add modules to path
sys.path.append(os.path.dirname(__file__))

from modules.discovery import scan_network
from modules.rtsp_builder import RTSPBuilder
import requests
import urllib.parse

# Reduce OpenCV logging if available
try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except Exception:
    try:
        cv2.setLogLevel(3)
    except Exception:
        pass


# Context manager to temporarily suppress native stderr (ffmpeg/OpenCV C++ logs)
@contextmanager
def suppress_stderr():
    """Temporarily redirect C-level stderr to os.devnull to hide ffmpeg/OpenCV noise."""
    try:
        devnull = open(os.devnull, 'w')
        old_stderr_fd = os.dup(2)
        os.dup2(devnull.fileno(), 2)
        yield
    finally:
        try:
            os.dup2(old_stderr_fd, 2)
            os.close(old_stderr_fd)
        except Exception:
            pass
        try:
            devnull.close()
        except Exception:
            pass

# --- PROFESSIONAL THEME ---
ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# Color Palette
COLOR_PRIMARY = "#6200EA"
COLOR_HOVER = "#3700B3"
COLOR_BG = "#FFFFFF"
COLOR_SURFACE = "#F5F5F5"
COLOR_TEXT_MAIN = "#000000"
COLOR_TEXT_SEC = "#757575"
COLOR_SUCCESS = "#00C853"
COLOR_ERROR = "#D32F2F"
COLOR_WARNING = "#FF6F00"

class SmartEntryAgent(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Window Setup
        self.title("SmartEntry Agent - CCTV Bridge Setup")
        self.geometry("1000x650")
        self.configure(fg_color=COLOR_BG)
        self.resizable(False, False)
        
        # State Variables
        self.current_step = 0
        self.camera_list = []
        self.selected_camera_ip = None
        self.username = ""
        self.password = ""
        self.available_channels = []
        self.selected_channel_url = None
        self.camera_name = "Main Entrance"
        self.is_streaming = False
        self.stream_thread = None
        
        # Setup Layout
        self.setup_layout()
        
    def setup_layout(self):
        """Create main layout: Sidebar + Content"""
        # Grid Configuration
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # --- LEFT SIDEBAR ---
        self.sidebar = ctk.CTkFrame(self, width=280, fg_color=COLOR_SURFACE, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        
        # Logo
        logo_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo_frame.pack(pady=(50, 30), padx=25)
        
        ctk.CTkLabel(
            logo_frame, text="SmartEntry", 
            font=("Segoe UI", 28, "bold"), 
            text_color=COLOR_PRIMARY
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            logo_frame, text="CCTV Bridge Agent", 
            font=("Segoe UI", 13), 
            text_color=COLOR_TEXT_SEC
        ).pack(anchor="w", pady=(5, 0))
        
        # Stepper
        self.steps = [
            "Network Discovery",
            "Camera Selection", 
            "Authentication",
            "Channel Setup",
            "Live Preview"
        ]
        self.step_indicators = []
        
        steps_container = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        steps_container.pack(fill="x", padx=25, pady=(20, 0))
        
        for i, step in enumerate(self.steps, 1):
            step_frame = ctk.CTkFrame(steps_container, fg_color="transparent")
            step_frame.pack(fill="x", pady=12)
            
            # Number indicator
            num_label = ctk.CTkLabel(
                step_frame, text=str(i), 
                font=("Segoe UI", 12, "bold"),
                text_color=COLOR_TEXT_SEC,
                width=30, height=30,
                fg_color="#E0E0E0",
                corner_radius=15
            )
            num_label.pack(side="left")
            
            # Step text
            text_label = ctk.CTkLabel(
                step_frame, text=step,
                font=("Segoe UI", 13),
                text_color=COLOR_TEXT_SEC,
                anchor="w"
            )
            text_label.pack(side="left", padx=(10, 0), fill="x", expand=True)
            
            # Make clickable (click only — avoid hover events that cause flicker)
            step_idx = i - 1
            step_frame.bind("<Button-1>", lambda e, idx=step_idx: self.navigate_to_step(idx))
            num_label.bind("<Button-1>", lambda e, idx=step_idx: self.navigate_to_step(idx))
            text_label.bind("<Button-1>", lambda e, idx=step_idx: self.navigate_to_step(idx))
            
            self.step_indicators.append((num_label, text_label))
        
        # Footer
        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=25, pady=25)
        
        ctk.CTkLabel(
            footer, 
            text="Version 2.0 Pro\n© 2026 SmartEntry Systems",
            font=("Segoe UI", 10),
            text_color=COLOR_TEXT_SEC,
            justify="left"
        ).pack(anchor="w")
        
        # --- RIGHT CONTENT AREA ---
        self.content_area = ctk.CTkFrame(self, fg_color=COLOR_BG)
        self.content_area.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        
        # Start with first step
        self.show_step_discovery()
        
    def update_stepper(self, active_step):
        """Update visual stepper"""
        for i, (num_lbl, text_lbl) in enumerate(self.step_indicators):
            if i == active_step:
                num_lbl.configure(fg_color=COLOR_PRIMARY, text_color="#FFFFFF")
                text_lbl.configure(text_color=COLOR_PRIMARY, font=("Segoe UI", 13, "bold"))
            elif i < active_step:
                num_lbl.configure(fg_color=COLOR_SUCCESS, text_color="#FFFFFF", text="✓")
                text_lbl.configure(text_color=COLOR_TEXT_SEC, font=("Segoe UI", 13))
            else:
                num_lbl.configure(fg_color="#E0E0E0", text_color=COLOR_TEXT_SEC, text=str(i+1))
                text_lbl.configure(text_color=COLOR_TEXT_SEC, font=("Segoe UI", 13))
    
    def navigate_to_step(self, step_idx):
        """Navigate to a specific step when sidebar is clicked"""
        # Only allow navigation to completed or current steps
        if step_idx == 0:
            self.show_step_discovery()
        elif step_idx == 1 and len(self.camera_list) > 0:
            self.show_step_selection()
        elif step_idx == 2 and self.selected_camera_ip:
            self.show_step_auth()
        elif step_idx == 3 and len(self.available_channels) > 0:
            self.show_step_channels()
        elif step_idx == 4 and self.selected_channel_url:
            self.show_step_preview()
    
    def clear_content(self):
        """Clear content area"""
        for widget in self.content_area.winfo_children():
            widget.destroy()
    
    # ============ STEP 1: DISCOVERY ============
    def show_step_discovery(self):
        """Step 1: Network Discovery"""
        self.clear_content()
        self.current_step = 0
        self.update_stepper(0)
        
        # Container
        container = ctk.CTkFrame(self.content_area, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=50, pady=40)
        
        # Header
        ctk.CTkLabel(
            container, text="🔍 Network Discovery",
            font=("Segoe UI", 32, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            container, 
            text="Scan your local network to find RTSP/ONVIF cameras automatically",
            font=("Segoe UI", 14),
            text_color=COLOR_TEXT_SEC
        ).pack(anchor="w", pady=(8, 30))
        
        # Auto Scan Card
        scan_card = ctk.CTkFrame(container, fg_color=COLOR_SURFACE, corner_radius=12)
        scan_card.pack(fill="x", pady=(0, 20))
        
        card_inner = ctk.CTkFrame(scan_card, fg_color="transparent")
        card_inner.pack(fill="both", padx=30, pady=25)
        
        ctk.CTkLabel(
            card_inner, text="🌐 Automatic Network Scan",
            font=("Segoe UI", 18, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            card_inner, 
            text="Recommended: Automatically discover all cameras on your network (192.168.100.0/24)",
            font=("Segoe UI", 12),
            text_color=COLOR_TEXT_SEC
        ).pack(anchor="w", pady=(5, 20))
        
        self.scan_btn = ctk.CTkButton(
            card_inner, text="Start Network Scan",
            font=("Segoe UI", 15, "bold"),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            height=50, width=250,
            corner_radius=8,
            command=self.start_network_scan
        )
        self.scan_btn.pack(anchor="w")
        
        # Progress indicator (hidden initially)
        self.scan_progress_frame = ctk.CTkFrame(card_inner, fg_color="transparent")
        
        self.scan_progress = ctk.CTkProgressBar(
            self.scan_progress_frame, 
            width=400, height=8,
            progress_color=COLOR_PRIMARY
        )
        self.scan_progress.pack(pady=(10, 5))
        self.scan_progress.set(0)
        
        self.scan_status_label = ctk.CTkLabel(
            self.scan_progress_frame,
            text="Scanning network...",
            font=("Segoe UI", 12),
            text_color=COLOR_TEXT_SEC
        )
        self.scan_status_label.pack()
        
        # OR Divider
        ctk.CTkLabel(
            container, text="OR",
            font=("Segoe UI", 12, "bold"),
            text_color=COLOR_TEXT_SEC
        ).pack(pady=15)
        
        # Manual Entry Card
        manual_card = ctk.CTkFrame(container, fg_color=COLOR_SURFACE, corner_radius=12)
        manual_card.pack(fill="x")
        
        manual_inner = ctk.CTkFrame(manual_card, fg_color="transparent")
        manual_inner.pack(fill="both", padx=30, pady=25)
        
        ctk.CTkLabel(
            manual_inner, text="🎯 Manual IP Entry",
            font=("Segoe UI", 18, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            manual_inner, 
            text="Enter camera IP address if you already know it",
            font=("Segoe UI", 12),
            text_color=COLOR_TEXT_SEC
        ).pack(anchor="w", pady=(5, 15))
        
        entry_row = ctk.CTkFrame(manual_inner, fg_color="transparent")
        entry_row.pack(fill="x")
        
        self.manual_ip_entry = ctk.CTkEntry(
            entry_row,
            placeholder_text="e.g., 192.168.100.11",
            font=("Segoe UI", 14),
            height=60,
            width=520
        )
        self.manual_ip_entry.pack(side="left", fill="x", expand=True, padx=(0, 15))
        
        ctk.CTkButton(
            entry_row, text="Add Camera",
            font=("Segoe UI", 13, "bold"),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            height=45, width=150,
            corner_radius=8,
            command=self.add_manual_camera
        ).pack(side="left")
        
        # Results Area
        results_frame = ctk.CTkFrame(container, fg_color="transparent")
        results_frame.pack(fill="both", expand=True, pady=(25, 0))
        
        ctk.CTkLabel(
            results_frame, text="📷 Discovered Cameras",
            font=("Segoe UI", 16, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w", pady=(0, 10))
        
        # Camera list container (interactive cards)
        list_container = ctk.CTkFrame(results_frame, fg_color="#FAFAFA", corner_radius=8, border_width=1, border_color="#E0E0E0")
        list_container.pack(fill="both", expand=True)

        # Scrollable frame to hold camera cards
        self.camera_list_container = ctk.CTkScrollableFrame(
            list_container,
            fg_color="#FAFAFA",
            height=180
        )
        self.camera_list_container.pack(fill="both", expand=True, padx=6, pady=6)

        # placeholder label when empty
        self._no_cameras_label = ctk.CTkLabel(self.camera_list_container, text="  No cameras discovered yet. Click 'Start Network Scan' above.", font=("Segoe UI", 13), text_color=COLOR_TEXT_SEC, anchor="w")
        self._no_cameras_label.pack(anchor='w', pady=(10,0), padx=10)
        
        # Next Button
        self.next_discovery_btn = ctk.CTkButton(
            container, text="Next: Select Camera →",
            font=("Segoe UI", 14, "bold"),
            fg_color=COLOR_SUCCESS,
            hover_color="#00A844",
            height=50,
            corner_radius=8,
            state="disabled",
            command=self.show_step_selection
        )
        self.next_discovery_btn.pack(side="right", pady=(20, 0))

        # Additional explicit Camera Selection button (visible but disabled until user selects)
        self.goto_selection_btn = ctk.CTkButton(
            container, text="Camera Selection",
            font=("Segoe UI", 12, "bold"),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            height=40,
            corner_radius=8,
            state="disabled",
            command=self.show_step_selection
        )
        self.goto_selection_btn.pack(side="left", pady=(20, 0))
    
    def start_network_scan(self):
        """Start network scanning"""
        self.scan_btn.configure(state="disabled", text="⏳ Scanning...")
        self.scan_progress_frame.pack(pady=(15, 0))
        self.scan_progress.start()
        self.scan_status_label.configure(text="Scanning 192.168.100.0/24 network (this may take 10-30 seconds)...")
        
        def scan_worker():
            try:
                cameras = scan_network()
                self.after(0, lambda cams=cameras: self.on_scan_complete(cams))
            except Exception as e:
                err = str(e)
                self.after(0, lambda err=err: self.on_scan_error(err))
        
        threading.Thread(target=scan_worker, daemon=True).start()
    
    def on_scan_complete(self, cameras):
        """Handle scan completion"""
        self.scan_progress.stop()
        self.scan_progress.set(1.0)
        
        if cameras:
            self.camera_list = cameras
            self.scan_status_label.configure(
                text=f"✓ Scan complete! Found {len(cameras)} camera(s). Select a camera below.",
                text_color=COLOR_SUCCESS
            )
            self.scan_btn.configure(state="normal", text="Scan Again")

            # Populate interactive camera cards
            try:
                self._clear_camera_cards()
            except Exception:
                pass
            for idx, ip in enumerate(cameras, 1):
                self._create_camera_card(idx, ip, online=True)

            # Keep next button disabled until a camera is selected by user
            self.next_discovery_btn.configure(state="disabled")
            # enable the explicit Camera Selection button so user can move to step 2
            try:
                self.goto_selection_btn.configure(state='normal')
            except Exception:
                pass

            # If we found at least one camera, auto-select the first one and
            # immediately navigate to the Camera Selection step (Step 2).
            if cameras and len(cameras) > 0:
                try:
                    first_ip = cameras[0].get('ip') if isinstance(cameras[0], dict) else cameras[0]
                    self.selected_camera_ip = first_ip
                except Exception:
                    self.selected_camera_ip = None

                # Navigate to selection step so user can see/pre-configure channels
                try:
                    self.show_step_selection()
                except Exception:
                    pass
        else:
            self.scan_status_label.configure(
                text="✗ No cameras found. Try manual IP entry or check network connection",
                text_color=COLOR_ERROR
            )
            self.scan_btn.configure(state="normal", text="Retry Scan")
    
    def on_scan_error(self, error):
        """Handle scan error"""
        self.scan_progress.stop()
        self.scan_status_label.configure(
            text=f"✗ Scan failed: {error}",
            text_color=COLOR_ERROR
        )
        self.scan_btn.configure(state="normal", text="Retry Scan")

    # -- Camera card helpers -------------------------------------------------
    def _clear_camera_cards(self):
        """Remove all camera card widgets and show placeholder"""
        for w in self.camera_list_container.winfo_children():
            w.destroy()
        # recreate placeholder
        self._no_cameras_label = ctk.CTkLabel(self.camera_list_container, text="  No cameras discovered yet. Click 'Start Network Scan' above.", font=("Segoe UI", 13), text_color=COLOR_TEXT_SEC, anchor="w")
        self._no_cameras_label.pack(anchor='w', pady=(10,0), padx=10)

    def _create_camera_card(self, idx, ip, online=True, manual=False):
        """Create an interactive camera card in the discovery results."""
        # remove placeholder if present
        try:
            self._no_cameras_label.destroy()
        except Exception:
            pass

        card = ctk.CTkFrame(self.camera_list_container, fg_color=COLOR_SURFACE, corner_radius=8)
        card.pack(fill='x', pady=8, padx=10)

        left = ctk.CTkFrame(card, fg_color='transparent')
        left.pack(side='left', padx=(10,0), pady=10)
        ctk.CTkLabel(left, text=f"{idx}.", font=("Segoe UI", 14, "bold"), text_color=COLOR_PRIMARY).pack()

        body = ctk.CTkFrame(card, fg_color='transparent')
        body.pack(side='left', fill='both', expand=True, padx=10, pady=10)
        ctk.CTkLabel(body, text="📷 RTSP Candidate", font=("Segoe UI", 14, "bold"), text_color=COLOR_TEXT_MAIN, anchor='w').pack(anchor='w')
        ctk.CTkLabel(body, text=f"IP Address: {ip}", font=("Consolas", 12), text_color=COLOR_TEXT_SEC, anchor='w').pack(anchor='w', pady=(6,0))
        status_text = "Online (Manual)" if manual else ("Online" if online else "Unknown")
        ctk.CTkLabel(body, text=f"Status: {status_text}", font=("Segoe UI", 11), text_color=COLOR_TEXT_SEC, anchor='w').pack(anchor='w', pady=(2,0))

        btns = ctk.CTkFrame(card, fg_color='transparent')
        btns.pack(side='right', padx=10, pady=10)

        # Inspect button (opens web root in default browser)
        def _inspect():
            import webbrowser
            webbrowser.open(f"http://{ip}")

        ctk.CTkButton(btns, text="Inspect", width=90, height=34, fg_color="#E0E0E0", text_color=COLOR_TEXT_MAIN, command=_inspect).pack(pady=(0,6))

        # Select button
        def _select():
            self._on_select_card(ip, card)

        ctk.CTkButton(btns, text="Select", width=90, height=34, fg_color=COLOR_PRIMARY, hover_color=COLOR_HOVER, command=_select).pack()

        return card

    def _on_select_card(self, ip, card_widget):
        """Handle selecting a camera card: mark selected and enable Next"""
        # store selection
        self.selected_camera_ip = ip
        # reset others' styling
        for child in self.camera_list_container.winfo_children():
            try:
                child.configure(border_width=0)
            except Exception:
                pass
        # highlight selected
        try:
            card_widget.configure(border_width=2, border_color=COLOR_PRIMARY)
        except Exception:
            pass
        # enable next
        self.next_discovery_btn.configure(state='normal')
        # enable the explicit Camera Selection button as well
        try:
            self.goto_selection_btn.configure(state='normal')
        except Exception:
            pass
    
    def add_manual_camera(self):
        """Add camera manually"""
        ip = self.manual_ip_entry.get().strip()
        if not ip:
            return
        
        if ip not in self.camera_list:
            self.camera_list.append(ip)
            # add interactive card for manual camera
            try:
                self._no_cameras_label.destroy()
            except Exception:
                pass
            idx = len(self.camera_list)
            self._create_camera_card(idx, ip, online=True, manual=True)
            # do not auto-enable Next; require explicit Select
            self.manual_ip_entry.delete(0, "end")
    
    # ============ STEP 2: SELECTION ============
    def show_step_selection(self):
        """Step 2: Camera Selection"""
        self.clear_content()
        self.current_step = 1
        self.update_stepper(1)
        
        container = ctk.CTkFrame(self.content_area, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=50, pady=40)
        
        # Header
        ctk.CTkLabel(
            container, text="🎯 Select Camera",
            font=("Segoe UI", 32, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            container, 
            text="Choose the camera you want to connect to SmartEntry cloud",
            font=("Segoe UI", 14),
            text_color=COLOR_TEXT_SEC
        ).pack(anchor="w", pady=(8, 30))
        
        # Camera cards
        scroll_frame = ctk.CTkScrollableFrame(
            container, 
            fg_color="transparent",
            height=300
        )
        scroll_frame.pack(fill="both", expand=True)
        
        self.camera_radio_var = ctk.StringVar(value="")
        
        for ip in self.camera_list:
            card = ctk.CTkFrame(scroll_frame, fg_color=COLOR_SURFACE, corner_radius=12)
            card.pack(fill="x", pady=8)
            
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=25, pady=20)
            
            radio = ctk.CTkRadioButton(
                inner, text="",
                variable=self.camera_radio_var,
                value=ip,
                fg_color=COLOR_PRIMARY,
                hover_color=COLOR_HOVER
            )
            radio.pack(side="left")
            
            info_frame = ctk.CTkFrame(inner, fg_color="transparent")
            info_frame.pack(side="left", fill="x", expand=True, padx=(15, 0))
            
            ctk.CTkLabel(
                info_frame, text=f"📷 RTSP Camera",
                font=("Segoe UI", 16, "bold"),
                text_color=COLOR_TEXT_MAIN,
                anchor="w"
            ).pack(anchor="w")
            
            ctk.CTkLabel(
                info_frame, text=f"IP Address: {ip}",
                font=("Consolas", 13),
                text_color=COLOR_TEXT_SEC,
                anchor="w"
            ).pack(anchor="w", pady=(5, 0))
        # if user already selected a camera on discovery, pre-select it here
        if self.selected_camera_ip:
            self.camera_radio_var.set(self.selected_camera_ip)
        
        # Navigation
        nav_frame = ctk.CTkFrame(container, fg_color="transparent")
        nav_frame.pack(fill="x", pady=(20, 0))
        
        ctk.CTkButton(
            nav_frame, text="← Back",
            font=("Segoe UI", 13),
            fg_color="transparent",
            text_color=COLOR_TEXT_SEC,
            hover_color="#F0F0F0",
            height=50, width=120,
            border_width=2,
            border_color="#E0E0E0",
            corner_radius=8,
            command=self.show_step_discovery
        ).pack(side="left")
        
        ctk.CTkButton(
            nav_frame, text="Next: Enter Credentials →",
            font=("Segoe UI", 14, "bold"),
            fg_color=COLOR_SUCCESS,
            hover_color="#00A844",
            height=50,
            corner_radius=8,
            command=self.proceed_to_auth
        ).pack(side="right")
    
    def proceed_to_auth(self):
        """Validate selection and proceed"""
        selected = self.camera_radio_var.get()
        if not selected:
            self.show_error_dialog("Please select a camera")
            return
        self.selected_camera_ip = selected
        self.show_step_auth()
    
    # ============ STEP 3: AUTHENTICATION ============
    def show_step_auth(self):
        """Step 3: Authentication"""
        self.clear_content()
        self.current_step = 2
        self.update_stepper(2)
        
        container = ctk.CTkFrame(self.content_area, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=50, pady=40)
        
        # Header
        ctk.CTkLabel(
            container, text="🔐 Smart Authentication",
            font=("Segoe UI", 32, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            container, 
            text="Enter the camera login credentials to establish connection",
            font=("Segoe UI", 14),
            text_color=COLOR_TEXT_SEC
        ).pack(anchor="w", pady=(8, 30))
        
        # Selected camera info
        info_card = ctk.CTkFrame(container, fg_color="#E3F2FD", corner_radius=12, border_width=1, border_color="#2196F3")
        info_card.pack(fill="x", pady=(0, 25))
        
        info_inner = ctk.CTkFrame(info_card, fg_color="transparent")
        info_inner.pack(padx=20, pady=15)
        
        ctk.CTkLabel(
            info_inner, text="Selected Camera:",
            font=("Segoe UI", 11),
            text_color=COLOR_TEXT_SEC
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            info_inner, text=self.selected_camera_ip,
            font=("Consolas", 18, "bold"),
            text_color="#1976D2"
        ).pack(anchor="w")
        
        # Form
        form_card = ctk.CTkFrame(container, fg_color=COLOR_SURFACE, corner_radius=12)
        form_card.pack(fill="x")
        
        form_inner = ctk.CTkFrame(form_card, fg_color="transparent")
        form_inner.pack(padx=30, pady=30)
        
        ctk.CTkLabel(
            form_inner, text="Username",
            font=("Segoe UI", 13, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w")
        
        self.username_entry = ctk.CTkEntry(
            form_inner,
            placeholder_text="admin",
            font=("Segoe UI", 14),
            height=45
        )
        self.username_entry.pack(fill="x", pady=(8, 20))
        
        ctk.CTkLabel(
            form_inner, text="Password",
            font=("Segoe UI", 13, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w")
        
        self.password_entry = ctk.CTkEntry(
            form_inner,
            placeholder_text="Enter password",
            font=("Segoe UI", 14),
            height=45,
            show="•"
        )
        self.password_entry.pack(fill="x", pady=(8, 15))
        
        ctk.CTkLabel(
            form_inner, 
            text="✓ Special characters like @, #, $ are automatically handled",
            font=("Segoe UI", 11),
            text_color=COLOR_SUCCESS
        ).pack(anchor="w")
        
        # Connect button
        self.connect_btn = ctk.CTkButton(
            container, text="Connect & Discover Channels",
            font=("Segoe UI", 15, "bold"),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            height=55,
            corner_radius=8,
            command=self.connect_and_discover
        )
        self.connect_btn.pack(pady=(25, 15))
        
        # Status label
        self.auth_status = ctk.CTkLabel(
            container, text="",
            font=("Segoe UI", 12),
            text_color=COLOR_TEXT_SEC
        )
        self.auth_status.pack()
        
        # Navigation
        nav_frame = ctk.CTkFrame(container, fg_color="transparent")
        nav_frame.pack(fill="x", pady=(20, 0))
        
        ctk.CTkButton(
            nav_frame, text="← Back",
            font=("Segoe UI", 13),
            fg_color="transparent",
            text_color=COLOR_TEXT_SEC,
            hover_color="#F0F0F0",
            height=50, width=120,
            border_width=2,
            border_color="#E0E0E0",
            corner_radius=8,
            command=self.show_step_selection
        ).pack(side="left")
    
    def connect_and_discover(self):
        """Connect to camera and discover channels"""
        self.username = self.username_entry.get().strip()
        self.password = self.password_entry.get().strip()
        
        if not self.username or not self.password:
            self.auth_status.configure(
                text="✗ Please enter both username and password",
                text_color=COLOR_ERROR
            )
            return
        
        self.connect_btn.configure(state="disabled", text="🔄 Connecting & Discovering...")
        self.auth_status.configure(
            text="Testing connection and discovering available channels...",
            text_color=COLOR_PRIMARY
        )
        
        def discover_worker():
            try:
                builder = RTSPBuilder(self.selected_camera_ip, self.username, self.password)
                channels = []
                tried_urls = []
                
                # Test channels 1-16, both main and sub streams
                for channel in range(1, 17):
                    for subtype in [0, 1]:  # 0=main, 1=sub
                        url = builder.build_url(channel, subtype)
                        tried_urls.append(url)

                        cap = None
                        # Try FFmpeg backend first (more reliable for RTSP), fall back to default
                        try:
                            with suppress_stderr():
                                cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                        except Exception:
                            cap = None

                        # If FFmpeg backend didn't open, try default open in a safe try/except
                        if not (cap and cap.isOpened()):
                            try:
                                with suppress_stderr():
                                    cap = cv2.VideoCapture(url)
                            except Exception:
                                cap = None

                        if cap is not None:
                            try:
                                if cap.isOpened():
                                    ret, frame = cap.read()
                                    if ret and frame is not None:
                                        stream_type = "Main Stream" if subtype == 0 else "Sub Stream"
                                        channels.append({
                                            'channel': channel,
                                            'subtype': subtype,
                                            'url': url,
                                            'name': f"Channel {channel} ({stream_type})",
                                            'frame': frame
                                        })
                            except Exception:
                                # ignore errors while reading
                                pass
                            try:
                                cap.release()
                            except Exception:
                                pass
                
                # Save tried urls for debugging/diagnostics (masked)
                self.last_tried_urls = tried_urls
                self.after(0, lambda chs=channels: self.on_discover_complete(chs))
            except Exception as e:
                err = str(e)
                self.after(0, lambda err=err: self.on_discover_error(err))
        
        threading.Thread(target=discover_worker, daemon=True).start()
    
    def on_discover_complete(self, channels):
        """Handle channel discovery completion"""
        if channels:
            self.available_channels = channels
            self.auth_status.configure(
                text=f"✓ Connection successful! Found {len(channels)} active channel(s)",
                text_color=COLOR_SUCCESS
            )
            self.connect_btn.configure(state="normal", text="✓ Connected")
            
            # Auto-proceed after 1 second
            self.after(1000, self.show_step_channels)
        else:
            self.auth_status.configure(
                text="✗ No active channels found. Check credentials or camera configuration",
                text_color=COLOR_ERROR
            )
            self.connect_btn.configure(state="normal", text="Retry Connection")
            # Display a small diagnostic sample of attempted paths (mask credentials)
            try:
                sample = []
                for u in (self.last_tried_urls or [])[:3]:
                    # remove credentials for display
                    if '@' in u:
                        parts = u.split('@', 1)
                        sample.append('rtsp://<credentials>@' + parts[1])
                    else:
                        sample.append(u)
                if sample:
                    self.auth_status.configure(
                        text=f"✗ No active channels found. Example tried: {sample[0]}",
                        text_color=COLOR_ERROR
                    )
            except Exception:
                pass
    
    def on_discover_error(self, error):
        """Handle discovery error"""
        self.auth_status.configure(
            text=f"✗ Connection failed: {error}",
            text_color=COLOR_ERROR
        )
        self.connect_btn.configure(state="normal", text="Retry Connection")
    
    # ============ STEP 4: CHANNEL SELECTION ============
    def show_step_channels(self):
        """Step 4: Channel Selection"""
        self.clear_content()
        self.current_step = 3
        self.update_stepper(3)
        
        container = ctk.CTkFrame(self.content_area, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=50, pady=40)
        
        # Header
        ctk.CTkLabel(
            container, text="📺 Channel Setup",
            font=("Segoe UI", 32, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            container, 
            text=f"Select the camera channel you want to stream ({len(self.available_channels)} available)",
            font=("Segoe UI", 14),
            text_color=COLOR_TEXT_SEC
        ).pack(anchor="w", pady=(8, 30))
        
        # Channel grid
        scroll_frame = ctk.CTkScrollableFrame(
            container, 
            fg_color="transparent",
            height=350
        )
        scroll_frame.pack(fill="both", expand=True)
        
        # Create grid of channel cards (2 per row)
        for idx, ch in enumerate(self.available_channels):
            if idx % 2 == 0:
                row_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
                row_frame.pack(fill="x", pady=8)
            
            card = ctk.CTkFrame(row_frame, fg_color=COLOR_SURFACE, corner_radius=12, border_width=2, border_color="#E0E0E0")
            card.pack(side="left", padx=8, fill="both", expand=True)
            
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(padx=20, pady=20)
            
            # Thumbnail
            try:
                frame_resized = cv2.resize(ch['frame'], (200, 150))
                frame_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                photo = ctk.CTkImage(light_image=img, dark_image=img, size=(200, 150))
                
                img_label = ctk.CTkLabel(inner, image=photo, text="")
                img_label.image = photo  # Keep reference
                img_label.pack()
            except:
                ctk.CTkLabel(
                    inner, text="[Preview]",
                    font=("Segoe UI", 12),
                    text_color=COLOR_TEXT_SEC,
                    width=200, height=150,
                    fg_color="#E0E0E0",
                    corner_radius=8
                ).pack()
            
            # Info
            ctk.CTkLabel(
                inner, text=ch['name'],
                font=("Segoe UI", 14, "bold"),
                text_color=COLOR_TEXT_MAIN
            ).pack(pady=(10, 5))
            
            ctk.CTkLabel(
                inner, text=f"Channel {ch['channel']} • {'Main' if ch['subtype']==0 else 'Sub'}",
                font=("Segoe UI", 11),
                text_color=COLOR_TEXT_SEC
            ).pack()
            
            # Select button
            ctk.CTkButton(
                inner, text="Select This Channel",
                font=("Segoe UI", 12, "bold"),
                fg_color=COLOR_PRIMARY,
                hover_color=COLOR_HOVER,
                height=38,
                corner_radius=8,
                command=lambda c=ch: self.select_channel(c)
            ).pack(pady=(15, 0))
        
        # Navigation
        nav_frame = ctk.CTkFrame(container, fg_color="transparent")
        nav_frame.pack(fill="x", pady=(20, 0))
        
        ctk.CTkButton(
            nav_frame, text="← Back",
            font=("Segoe UI", 13),
            fg_color="transparent",
            text_color=COLOR_TEXT_SEC,
            hover_color="#F0F0F0",
            height=50, width=120,
            border_width=2,
            border_color="#E0E0E0",
            corner_radius=8,
            command=self.show_step_auth
        ).pack(side="left")
    
    def select_channel(self, channel):
        """Select a channel"""
        self.selected_channel_url = channel['url']
        self.show_step_preview()
    
    # ============ STEP 5: PREVIEW ============
    def show_step_preview(self):
        """Step 5: Live Preview"""
        self.clear_content()
        self.current_step = 4
        self.update_stepper(4)
        
        container = ctk.CTkFrame(self.content_area, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=50, pady=40)
        
        # Header
        ctk.CTkLabel(
            container, text="🎨 Live Preview",
            font=("Segoe UI", 32, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            container, 
            text="Verify the camera feed before starting continuous streaming",
            font=("Segoe UI", 14),
            text_color=COLOR_TEXT_SEC
        ).pack(anchor="w", pady=(8, 25))
        
        # Video frame
        video_container = ctk.CTkFrame(container, fg_color="#000000", corner_radius=12, height=300)
        video_container.pack(fill="both", expand=True)
        video_container.pack_propagate(False)
        
        self.video_label = ctk.CTkLabel(video_container, text="")
        self.video_label.pack(fill="both", expand=True)
        
        # Camera name
        name_frame = ctk.CTkFrame(container, fg_color="transparent")
        name_frame.pack(fill="x", pady=(20, 0))
        
        ctk.CTkLabel(
            name_frame, text="Camera Name:",
            font=("Segoe UI", 13, "bold"),
            text_color=COLOR_TEXT_MAIN
        ).pack(side="left")
        
        self.camera_name_entry = ctk.CTkEntry(
            name_frame,
            placeholder_text="Main Entrance",
            font=("Segoe UI", 13),
            height=40,
            width=250
        )
        self.camera_name_entry.insert(0, "Main Entrance")
        self.camera_name_entry.pack(side="left", padx=(15, 0))
        
        # Controls
        controls = ctk.CTkFrame(container, fg_color="transparent")
        controls.pack(fill="x", pady=(15, 0))
        
        self.preview_btn = ctk.CTkButton(
            controls, text="▶ Start Preview",
            font=("Segoe UI", 14, "bold"),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            height=50, width=200,
            corner_radius=8,
            command=self.toggle_preview
        )
        self.preview_btn.pack(side="left")
        
        self.stream_btn = ctk.CTkButton(
            controls, text="Start Streaming to Cloud",
            font=("Segoe UI", 14, "bold"),
            fg_color=COLOR_SUCCESS,
            hover_color="#00A844",
            height=50,
            corner_radius=8,
            state="disabled",
            command=self.start_streaming
        )
        self.stream_btn.pack(side="right")
        
        # Stats
        self.stats_label = ctk.CTkLabel(
            container, text="",
            font=("Segoe UI", 11),
            text_color=COLOR_TEXT_SEC
        )
        self.stats_label.pack(pady=(10, 0))
        
        # Navigation
        nav_frame = ctk.CTkFrame(container, fg_color="transparent")
        nav_frame.pack(fill="x", pady=(15, 0))
        
        ctk.CTkButton(
            nav_frame, text="← Back",
            font=("Segoe UI", 13),
            fg_color="transparent",
            text_color=COLOR_TEXT_SEC,
            hover_color="#F0F0F0",
            height=50, width=120,
            border_width=2,
            border_color="#E0E0E0",
            corner_radius=8,
            command=self.show_step_channels
        ).pack(side="left")
        
        self.preview_active = False
        self.preview_thread = None
    
    def toggle_preview(self):
        """Toggle preview"""
        if not self.preview_active:
            self.preview_active = True
            self.preview_btn.configure(text="⏸ Stop Preview")
            self.stream_btn.configure(state="normal")
            
            def preview_worker():
                try:
                    with suppress_stderr():
                        cap = cv2.VideoCapture(self.selected_channel_url, cv2.CAP_FFMPEG)
                except Exception:
                    cap = None
                if not (cap and cap.isOpened()):
                    try:
                        with suppress_stderr():
                            cap = cv2.VideoCapture(self.selected_channel_url)
                    except Exception:
                        cap = None
                while self.preview_active:
                    ret, frame = cap.read()
                    if ret:
                        frame_resized = cv2.resize(frame, (640, 360))
                        frame_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
                        img = Image.fromarray(frame_rgb)
                        photo = ctk.CTkImage(light_image=img, dark_image=img, size=(640, 360))
                        
                        self.after(0, lambda p=photo: self.update_preview(p))
                    time.sleep(0.03)
                cap.release()
            
            self.preview_thread = threading.Thread(target=preview_worker, daemon=True)
            self.preview_thread.start()
        else:
            self.preview_active = False
            self.preview_btn.configure(text="▶ Start Preview")
    
    def update_preview(self, photo):
        """Update preview image"""
        self.video_label.configure(image=photo)
        self.video_label.image = photo
    
    def start_streaming(self):
        """Start streaming to cloud"""
        self.camera_name = self.camera_name_entry.get().strip() or "Main Entrance"
        
        # Stop preview
        if self.preview_active:
            self.toggle_preview()
        
        # Start streaming
        self.is_streaming = True
        self.stream_btn.configure(text="⏸ Pause Streaming", command=self.pause_streaming)
        self.preview_btn.configure(state="disabled")
        
        self.frame_count = 0
        self.upload_count = 0
        self.start_time = time.time()
        
        def stream_worker():
            cap = None
            try:
                with suppress_stderr():
                    cap = cv2.VideoCapture(self.selected_channel_url, cv2.CAP_FFMPEG)
            except Exception:
                cap = None

            if not (cap and cap.isOpened()):
                try:
                    with suppress_stderr():
                        cap = cv2.VideoCapture(self.selected_channel_url)
                except Exception:
                    cap = None

            try:
                while self.is_streaming:
                    ret, frame = (False, None)
                    try:
                        if cap is not None:
                            ret, frame = cap.read()
                    except Exception:
                        ret, frame = (False, None)

                    if ret and frame is not None:
                        self.frame_count += 1

                        # Display
                        frame_resized = cv2.resize(frame, (640, 360))
                        frame_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
                        img = Image.fromarray(frame_rgb)
                        photo = ctk.CTkImage(light_image=img, dark_image=img, size=(640, 360))
                        self.after(0, lambda p=photo: self.update_preview(p))

                        # Upload every 15 frames (approx 1 FPS)
                        if self.frame_count % 15 == 0:
                            try:
                                _, buffer = cv2.imencode('.jpg', frame)
                                files = {'frame': ('frame.jpg', buffer.tobytes(), 'image/jpeg')}
                                data = {
                                    'camera_name': self.camera_name,
                                    'camera_ip': self.selected_camera_ip,
                                    'timestamp': datetime.now().isoformat()
                                }
                                # requests.post('http://localhost:5000/api/recognize', files=files, data=data, timeout=2)
                                requests.post('http://103.65.21.239:5000/api/recognize', files=files, data=data, timeout=2)
                                self.upload_count += 1
                            except Exception:
                                pass

                        # Update stats
                        elapsed = time.time() - self.start_time
                        fps = self.frame_count / elapsed if elapsed > 0 else 0
                        self.after(0, lambda: self.stats_label.configure(
                            text=f"● LIVE  |  Frames: {self.frame_count}  |  Uploads: {self.upload_count}  |  FPS: {fps:.1f}  |  Uptime: {int(elapsed)}s"
                        ))

                    time.sleep(0.03)
            finally:
                try:
                    if cap is not None:
                        cap.release()
                except Exception:
                    pass
        
        self.stream_thread = threading.Thread(target=stream_worker, daemon=True)
        self.stream_thread.start()
    
    def pause_streaming(self):
        """Pause streaming"""
        self.is_streaming = False
        self.stream_btn.configure(text="Resume Streaming", command=self.start_streaming)
        self.preview_btn.configure(state="normal")
    
    def show_error_dialog(self, message):
        """Show error dialog"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Error")
        dialog.geometry("400x150")
        dialog.configure(fg_color=COLOR_BG)
        dialog.resizable(False, False)
        
        ctk.CTkLabel(
            dialog, text="⚠ Error",
            font=("Segoe UI", 18, "bold"),
            text_color=COLOR_ERROR
        ).pack(pady=(20, 10))
        
        ctk.CTkLabel(
            dialog, text=message,
            font=("Segoe UI", 12),
            text_color=COLOR_TEXT_MAIN
        ).pack(pady=(0, 20))
        
        ctk.CTkButton(
            dialog, text="OK",
            font=("Segoe UI", 13),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_HOVER,
            width=120,
            command=dialog.destroy
        ).pack()
        
        dialog.transient(self)
        dialog.grab_set()

if __name__ == "__main__":
    app = SmartEntryAgent()
    app.mainloop()
