import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import cv2
import time
import json
import os
import base64
from PIL import Image, ImageTk, ImageDraw, ImageFont
from datetime import datetime
from modules.discovery import scan_network, scan_specific_ip
from modules.rtsp_builder import RTSPBuilder
from modules.logger import *

# --- CONFIGURATION ---
CONFIG_FILE = "config/settings.json"
MOCK_API_PORT = 5000

# --- ELEGANT COLOR SCHEME ---
class Theme:
    # Background colors
    BG_DARK = "#0d1117"
    BG_MEDIUM = "#161b22"
    BG_LIGHT = "#21262d"
    BG_HOVER = "#30363d"
    
    # Primary colors (Elegant Blue-Violet)
    PRIMARY = "#6366f1"
    PRIMARY_HOVER = "#818cf8"
    PRIMARY_LIGHT = "#a5b4fc"
    
    # Secondary colors
    SECONDARY = "#8b5cf6"
    SECONDARY_HOVER = "#a78bfa"
    
    # Success/Error/Warning
    SUCCESS = "#10b981"
    ERROR = "#ef4444"
    WARNING = "#f59e0b"
    INFO = "#3b82f6"
    
    # Text colors
    TEXT_PRIMARY = "#f0f6fc"
    TEXT_SECONDARY = "#8b949e"
    TEXT_MUTED = "#6e7681"
    
    # Border
    BORDER = "#30363d"

class SmartEntryGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SmartEntry - Intelligent Camera Management System")
        self.root.geometry("1400x900")
        self.root.configure(bg=Theme.BG_DARK)
        
        # State variables
        self.camera_list = []
        self.target_ip = None
        self.username = tk.StringVar(value="admin")
        self.password = tk.StringVar()
        self.camera_name = tk.StringVar(value="Main Entrance")
        
        # Channel discovery
        self.discovered_channels = []
        self.selected_channel = None
        self.channel_previews = {}
        
        # Streaming
        self.cap = None
        self.working_url = None
        self.is_streaming = False
        self.is_previewing = False
        self.preview_cap = None
        self.mock_server = None
        self.frame_count = 0
        self.upload_count = 0
        self.last_upload = 0
        
        # Current step (wizard-like flow)
        self.current_step = 1  # 1=Discovery, 2=Credentials, 3=Channels, 4=Preview, 5=Streaming
        
        # Setup UI
        self.setup_ui()
        
        # Start mock API server
        self.start_mock_api()
        
    def setup_ui(self):
        """Creates the main UI layout with elegant design"""
        
        # Top Navigation Bar
        nav_frame = tk.Frame(self.root, bg=Theme.BG_MEDIUM, height=80)
        nav_frame.pack(fill='x', padx=0, pady=0)
        nav_frame.pack_propagate(False)
        
        # Logo and Title
        title_container = tk.Frame(nav_frame, bg=Theme.BG_MEDIUM)
        title_container.pack(side='left', padx=30, pady=15)
        
        tk.Label(
            title_container, 
            text="🎥 SmartEntry",
            font=('Segoe UI', 22, 'bold'),
            bg=Theme.BG_MEDIUM,
            fg=Theme.TEXT_PRIMARY
        ).pack(side='left')
        
        tk.Label(
            title_container,
            text=" Intelligent Camera Management",
            font=('Segoe UI', 12),
            bg=Theme.BG_MEDIUM,
            fg=Theme.TEXT_SECONDARY
        ).pack(side='left', padx=(10, 0))
        
        # Step Indicators
        steps_frame = tk.Frame(nav_frame, bg=Theme.BG_MEDIUM)
        steps_frame.pack(side='right', padx=30, pady=15)
        
        self.step_labels = []
        steps = [
            ("1", "Discover"),
            ("2", "Connect"),
            ("3", "Channels"),
            ("4", "Preview"),
            ("5", "Stream")
        ]
        
        for idx, (num, name) in enumerate(steps):
            step_frame = tk.Frame(steps_frame, bg=Theme.BG_MEDIUM)
            step_frame.pack(side='left', padx=5)
            
            # Circle
            canvas = tk.Canvas(step_frame, width=30, height=30, bg=Theme.BG_MEDIUM, 
                             highlightthickness=0)
            canvas.pack()
            
            circle = canvas.create_oval(2, 2, 28, 28, 
                                       fill=Theme.PRIMARY if idx == 0 else Theme.BG_LIGHT,
                                       outline=Theme.PRIMARY if idx == 0 else Theme.BORDER,
                                       width=2)
            text = canvas.create_text(15, 15, text=num, 
                                     fill=Theme.TEXT_PRIMARY,
                                     font=('Segoe UI', 10, 'bold'))
            
            # Label
            label = tk.Label(step_frame, text=name, font=('Segoe UI', 8),
                           bg=Theme.BG_MEDIUM,
                           fg=Theme.TEXT_PRIMARY if idx == 0 else Theme.TEXT_MUTED)
            label.pack()
            
            self.step_labels.append({'canvas': canvas, 'circle': circle, 
                                    'text': text, 'label': label})
        
        # Main Content Area
        content_frame = tk.Frame(self.root, bg=Theme.BG_DARK)
        content_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # Create a notebook (tabbed interface) for different steps
        self.notebook = ttk.Notebook(content_frame)
        
        # Custom style for notebook
        style = ttk.Style()
        style.theme_use('default')
        style.configure('TNotebook', background=Theme.BG_DARK, borderwidth=0)
        style.configure('TNotebook.Tab', background=Theme.BG_LIGHT, 
                       foreground=Theme.TEXT_SECONDARY, padding=[20, 10],
                       font=('Segoe UI', 10))
        style.map('TNotebook.Tab', background=[('selected', Theme.PRIMARY)],
                 foreground=[('selected', Theme.TEXT_PRIMARY)])
        
        # Step 1: Camera Discovery
        self.discovery_tab = self.create_discovery_tab()
        self.notebook.add(self.discovery_tab, text="  📡 Discover Cameras  ")
        
        # Step 2: Connection & Credentials
        self.connection_tab = self.create_connection_tab()
        self.notebook.add(self.connection_tab, text="  🔐 Connect Camera  ")
        
        # Step 3: Channel Discovery
        self.channel_tab = self.create_channel_tab()
        self.notebook.add(self.channel_tab, text="  📺 Select Channel  ")
        
        # Step 4: Live Preview & Configuration
        self.preview_tab = self.create_preview_tab()
        self.notebook.add(self.preview_tab, text="  👁️ Preview & Configure  ")
        
        # Step 5: Streaming
        self.streaming_tab = self.create_streaming_tab()
        self.notebook.add(self.streaming_tab, text="  🚀 Live Streaming  ")
        
        self.notebook.pack(fill='both', expand=True)
        
        # Initially disable all tabs except first
        for i in range(1, 5):
            self.notebook.tab(i, state='disabled')
        
        # Status Bar at Bottom
        status_bar = tk.Frame(self.root, bg=Theme.BG_MEDIUM, height=30)
        status_bar.pack(fill='x', side='bottom')
        status_bar.pack_propagate(False)
        
        self.status_label = tk.Label(
            status_bar,
            text="👋 Welcome! Let's get started by discovering cameras on your network.",
            font=('Segoe UI', 9),
            bg=Theme.BG_MEDIUM,
            fg=Theme.TEXT_SECONDARY,
            anchor='w'
        )
        self.status_label.pack(side='left', padx=20, fill='x', expand=True)
        
        # API Status
        self.api_status_label = tk.Label(
            status_bar,
            text="● Mock API: Starting...",
            font=('Segoe UI', 9),
            bg=Theme.BG_MEDIUM,
            fg=Theme.WARNING
        )
        self.api_status_label.pack(side='right', padx=20)
        
        # Title Bar
        title_frame = tk.Frame(self.root, bg='#2d2d30', height=60)
        title_frame.pack(fill='x', padx=0, pady=0)
        title_frame.pack_propagate(False)
        
        title_label = tk.Label(
            title_frame, 
            text="🎥 SmartEntry - Desktop Agent",
            font=('Segoe UI', 18, 'bold'),
            bg='#2d2d30',
            fg='#ffffff'
        )
        title_label.pack(side='left', padx=20, pady=10)
        
        status_label = tk.Label(
            title_frame,
            text="Production-Grade Multi-Vendor Camera Support",
            font=('Segoe UI', 9),
            bg='#2d2d30',
            fg='#aaaaaa'
        )
        status_label.pack(side='left', padx=10, pady=10)
        
        # Main Container
        main_container = tk.Frame(self.root, bg='#1e1e1e')
        main_container.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Left Panel - Controls
        left_panel = tk.Frame(main_container, bg='#252526', width=400)
        left_panel.pack(side='left', fill='y', padx=(0, 10))
        left_panel.pack_propagate(False)
        
        # Right Panel - Video & Logs
        right_panel = tk.Frame(main_container, bg='#1e1e1e')
        right_panel.pack(side='right', fill='both', expand=True)
        
        # === LEFT PANEL CONTENT ===
        
        # Discovery Section
        discovery_frame = tk.LabelFrame(
            left_panel,
            text=" 🔍 Camera Discovery ",
            font=('Segoe UI', 10, 'bold'),
            bg='#252526',
            fg='#ffffff',
            bd=2,
            relief='groove'
        )
        discovery_frame.pack(fill='x', padx=10, pady=10)
        
        # Auto Scan Button
        self.scan_btn = tk.Button(
            discovery_frame,
            text="🌐 Auto-Scan Network",
            font=('Segoe UI', 10, 'bold'),
            bg='#0e639c',
            fg='white',
            activebackground='#1177bb',
            activeforeground='white',
            cursor='hand2',
            command=self.auto_scan,
            height=2
        )
        self.scan_btn.pack(fill='x', padx=10, pady=10)
        
        # Manual IP Entry
        manual_frame = tk.Frame(discovery_frame, bg='#252526')
        manual_frame.pack(fill='x', padx=10, pady=5)
        
        tk.Label(
            manual_frame,
            text="Or Enter IP Manually:",
            font=('Segoe UI', 9),
            bg='#252526',
            fg='#cccccc'
        ).pack(anchor='w')
        
        ip_entry_frame = tk.Frame(manual_frame, bg='#252526')
        ip_entry_frame.pack(fill='x', pady=5)
        
        self.ip_entry = tk.Entry(
            ip_entry_frame,
            font=('Segoe UI', 10),
            bg='#3c3c3c',
            fg='#ffffff',
            insertbackground='white',
            relief='flat',
            bd=5
        )
        self.ip_entry.pack(side='left', fill='x', expand=True)
        
        tk.Button(
            ip_entry_frame,
            text="Check",
            font=('Segoe UI', 9),
            bg='#0e639c',
            fg='white',
            activebackground='#1177bb',
            activeforeground='white',
            cursor='hand2',
            command=self.check_manual_ip,
            width=8
        ).pack(side='right', padx=(5, 0))
        
        # Camera List
        tk.Label(
            discovery_frame,
            text="Available Cameras:",
            font=('Segoe UI', 9),
            bg='#252526',
            fg='#cccccc'
        ).pack(anchor='w', padx=10, pady=(10, 5))
        
        self.camera_listbox = tk.Listbox(
            discovery_frame,
            font=('Consolas', 10),
            bg='#3c3c3c',
            fg='#00ff00',
            selectbackground='#0e639c',
            selectforeground='white',
            height=6,
            relief='flat',
            bd=5
        )
        self.camera_listbox.pack(fill='x', padx=10, pady=5)
        self.camera_listbox.bind('<<ListboxSelect>>', self.on_camera_select)
        
        # Connection Section
        connection_frame = tk.LabelFrame(
            left_panel,
            text=" 🔐 Camera Credentials ",
            font=('Segoe UI', 10, 'bold'),
            bg='#252526',
            fg='#ffffff',
            bd=2,
            relief='groove'
        )
        connection_frame.pack(fill='x', padx=10, pady=10)
        
        # Selected Camera
        tk.Label(
            connection_frame,
            text="Selected Camera:",
            font=('Segoe UI', 9),
            bg='#252526',
            fg='#cccccc'
        ).pack(anchor='w', padx=10, pady=(10, 2))
        
        self.selected_camera_label = tk.Label(
            connection_frame,
            text="None",
            font=('Consolas', 11, 'bold'),
            bg='#252526',
            fg='#4ec9b0'
        )
        self.selected_camera_label.pack(anchor='w', padx=10, pady=(0, 10))
        
        # Username
        tk.Label(
            connection_frame,
            text="Username:",
            font=('Segoe UI', 9),
            bg='#252526',
            fg='#cccccc'
        ).pack(anchor='w', padx=10, pady=(5, 2))
        
        username_entry = tk.Entry(
            connection_frame,
            textvariable=self.username,
            font=('Segoe UI', 10),
            bg='#3c3c3c',
            fg='#ffffff',
            insertbackground='white',
            relief='flat',
            bd=5
        )
        username_entry.pack(fill='x', padx=10, pady=5)
        
        # Password
        tk.Label(
            connection_frame,
            text="Password:",
            font=('Segoe UI', 9),
            bg='#252526',
            fg='#cccccc'
        ).pack(anchor='w', padx=10, pady=(5, 2))
        
        password_entry = tk.Entry(
            connection_frame,
            textvariable=self.password,
            font=('Segoe UI', 10),
            bg='#3c3c3c',
            fg='#ffffff',
            insertbackground='white',
            relief='flat',
            bd=5,
            show='●'
        )
        password_entry.pack(fill='x', padx=10, pady=5)
        
        # Connect Button
        self.connect_btn = tk.Button(
            connection_frame,
            text="🔌 Connect to Camera",
            font=('Segoe UI', 11, 'bold'),
            bg='#16825d',
            fg='white',
            activebackground='#1a9e6f',
            activeforeground='white',
            cursor='hand2',
            command=self.connect_camera,
            height=2,
            state='disabled'
        )
        self.connect_btn.pack(fill='x', padx=10, pady=10)
        
        # Stream Control Section
        stream_frame = tk.LabelFrame(
            left_panel,
            text=" 📡 Stream Control ",
            font=('Segoe UI', 10, 'bold'),
            bg='#252526',
            fg='#ffffff',
            bd=2,
            relief='groove'
        )
        stream_frame.pack(fill='x', padx=10, pady=10)
        
        # Start/Stop Stream
        self.stream_btn = tk.Button(
            stream_frame,
            text="▶ Start Streaming",
            font=('Segoe UI', 11, 'bold'),
            bg='#0e639c',
            fg='white',
            activebackground='#1177bb',
            activeforeground='white',
            cursor='hand2',
            command=self.toggle_stream,
            height=2,
            state='disabled'
        )
        self.stream_btn.pack(fill='x', padx=10, pady=10)
        
        # Statistics
        stats_frame = tk.Frame(stream_frame, bg='#252526')
        stats_frame.pack(fill='x', padx=10, pady=5)
        
        tk.Label(
            stats_frame,
            text="📊 Statistics:",
            font=('Segoe UI', 9, 'bold'),
            bg='#252526',
            fg='#cccccc'
        ).pack(anchor='w', pady=(0, 5))
        
        self.stats_label = tk.Label(
            stats_frame,
            text="Frames: 0\nUploaded: 0\nUptime: 00:00:00",
            font=('Consolas', 9),
            bg='#252526',
            fg='#4ec9b0',
            justify='left'
        )
        self.stats_label.pack(anchor='w')
        
        # === RIGHT PANEL CONTENT ===
        
        # Video Preview
        video_frame = tk.LabelFrame(
            right_panel,
            text=" 🎬 Live Preview ",
            font=('Segoe UI', 10, 'bold'),
            bg='#252526',
            fg='#ffffff',
            bd=2,
            relief='groove'
        )
        video_frame.pack(fill='both', expand=True, padx=0, pady=(0, 10))
        
        self.video_label = tk.Label(
            video_frame,
            bg='#000000',
            text="No Camera Connected\n\nPlease scan for cameras and connect",
            font=('Segoe UI', 14),
            fg='#666666'
        )
        self.video_label.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Console Logs
        log_frame = tk.LabelFrame(
            right_panel,
            text=" 📋 Console Logs ",
            font=('Segoe UI', 10, 'bold'),
            bg='#252526',
            fg='#ffffff',
            bd=2,
            relief='groove',
            height=200
        )
        log_frame.pack(fill='x', padx=0, pady=0)
        log_frame.pack_propagate(False)
        
        self.console = scrolledtext.ScrolledText(
            log_frame,
            font=('Consolas', 9),
            bg='#1e1e1e',
            fg='#00ff00',
            insertbackground='white',
            relief='flat',
            bd=5,
            wrap='word',
            height=10
        )
        self.console.pack(fill='both', expand=True, padx=5, pady=5)
        
        # Initial log
        self.log("System initialized successfully", "success")
        self.log("Mock API Server starting...", "info")
        
    def start_mock_api(self):
        """Starts the mock API server in background"""
        from http.server import HTTPServer, BaseHTTPRequestHandler
        
        class MockCloudAPI(BaseHTTPRequestHandler):
            gui = self
            
            def do_POST(handler):
                if handler.path == '/api/recognize':
                    content_length = int(handler.headers['Content-Length'])
                    post_data = handler.rfile.read(content_length)
                    
                    try:
                        data = json.loads(post_data.decode('utf-8'))
                        camera_ip = data.get('camera_ip', 'unknown')
                        image_size = len(data.get('image', '')) // 1024
                        
                        response = {
                            "status": "success",
                            "camera_ip": camera_ip,
                            "recognized_faces": [],
                            "message": f"Frame received ({image_size} KB)"
                        }
                        
                        handler.send_response(200)
                        handler.send_header('Content-type', 'application/json')
                        handler.end_headers()
                        handler.wfile.write(json.dumps(response).encode())
                        
                        # Log to GUI
                        self.log(f"API received frame from {camera_ip} ({image_size} KB)", "success")
                        
                    except Exception as e:
                        handler.send_response(500)
                        handler.end_headers()
                
            def log_message(handler, format, *args):
                pass
        
        def run_server():
            server = HTTPServer(('localhost', MOCK_API_PORT), MockCloudAPI)
            self.mock_server = server
            server.serve_forever()
        
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        self.log(f"Mock API started at http://localhost:{MOCK_API_PORT}/api/recognize", "success")
        
    def log(self, message, level="info"):
        """Adds a log message to the console"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Color coding
        if level == "success":
            prefix = "✓"
            color = "#00ff00"
        elif level == "error":
            prefix = "✗"
            color = "#ff0000"
        elif level == "warn":
            prefix = "⚠"
            color = "#ffff00"
        else:
            prefix = "ℹ"
            color = "#00bfff"
        
        self.console.insert('end', f"[{timestamp}] {prefix} {message}\n")
        self.console.see('end')
        
    def auto_scan(self):
        """Scans network for cameras"""
        self.scan_btn.config(state='disabled', text="🔄 Scanning...")
        self.log("Starting network scan (this may take 10-30 seconds)...", "info")
        self.camera_listbox.delete(0, tk.END)
        
        def scan_thread():
            cameras = scan_network()
            
            self.root.after(0, lambda: self.on_scan_complete(cameras))
        
        threading.Thread(target=scan_thread, daemon=True).start()
        
    def on_scan_complete(self, cameras):
        """Called when network scan completes"""
        self.camera_list = cameras
        self.scan_btn.config(state='normal', text="🌐 Auto-Scan Network")
        
        if cameras:
            self.log(f"Found {len(cameras)} camera(s)", "success")
            for ip in cameras:
                self.camera_listbox.insert('end', f"  📷 {ip}")
        else:
            self.log("No cameras found on network", "warn")
            messagebox.showwarning(
                "No Cameras Found",
                "No RTSP cameras detected on your network.\n\n"
                "Possible reasons:\n"
                "• Camera not on same network\n"
                "• RTSP disabled on camera\n"
                "• Firewall blocking port 554\n\n"
                "Try manual IP entry instead."
            )
    
    def check_manual_ip(self):
        """Checks manually entered IP"""
        ip = self.ip_entry.get().strip()
        if not ip:
            messagebox.showerror("Error", "Please enter an IP address")
            return
        
        self.log(f"Checking {ip}...", "info")
        
        def check_thread():
            result = scan_specific_ip(ip)
            self.root.after(0, lambda: self.on_manual_check_complete(ip, result))
        
        threading.Thread(target=check_thread, daemon=True).start()
        
    def on_manual_check_complete(self, ip, result):
        """Called when manual IP check completes"""
        if result:
            self.log(f"Camera found at {ip}", "success")
            if ip not in self.camera_list:
                self.camera_list.append(ip)
                self.camera_listbox.insert('end', f"  📷 {ip}")
        else:
            self.log(f"No camera at {ip}", "error")
            messagebox.showerror(
                "Camera Not Found",
                f"No RTSP camera found at {ip}\n\n"
                "Please verify:\n"
                "• IP address is correct\n"
                "• Camera is powered on\n"
                "• Same network as this PC"
            )
    
    def on_camera_select(self, event):
        """Called when camera is selected from list"""
        selection = self.camera_listbox.curselection()
        if selection:
            index = selection[0]
            self.target_ip = self.camera_list[index]
            self.selected_camera_label.config(text=self.target_ip)
            self.connect_btn.config(state='normal')
            self.log(f"Camera selected: {self.target_ip}", "info")
            
            # Try to load saved credentials
            config = self.load_config()
            saved = config.get('cameras', {}).get(self.target_ip, {})
            if saved.get('username'):
                self.username.set(saved['username'])
                self.password.set(saved.get('password', ''))
                self.log("Loaded saved credentials", "success")
    
    def connect_camera(self):
        """Connects to selected camera"""
        if not self.target_ip:
            messagebox.showerror("Error", "Please select a camera first")
            return
        
        if not self.password.get():
            messagebox.showerror("Error", "Please enter password")
            return
        
        self.connect_btn.config(state='disabled', text="🔄 Connecting...")
        self.log(f"Connecting to {self.target_ip}...", "info")
        
        def connect_thread():
            config = self.load_config()
            saved = config.get('cameras', {}).get(self.target_ip, {})
            saved_path = saved.get('rtsp_path')
            
            cap, url = self.find_working_stream(
                self.target_ip,
                self.username.get(),
                self.password.get(),
                saved_path
            )
            
            self.root.after(0, lambda: self.on_connect_complete(cap, url))
        
        threading.Thread(target=connect_thread, daemon=True).start()
        
    def find_working_stream(self, ip, user, pwd, saved_path=None):
        """Finds working RTSP stream"""
        builder = RTSPBuilder()
        
        # Try saved path first
        if saved_path:
            self.root.after(0, lambda: self.log(f"Trying saved path: {saved_path}", "info"))
            candidates = builder.build_url(ip, user, pwd, path_override=saved_path)
            
            for url in candidates:
                cap = cv2.VideoCapture(url)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        self.root.after(0, lambda: self.log("Reconnected using saved path!", "success"))
                        return cap, url
                    cap.release()
        
        # Try all paths
        candidates = builder.build_url(ip, user, pwd)
        self.root.after(0, lambda: self.log(f"Testing {len(candidates)} RTSP paths (this may take 1-2 minutes)...", "info"))
        
        for idx, url in enumerate(candidates, 1):
            if idx % 20 == 0:
                self.root.after(0, lambda i=idx: self.log(f"Progress: {i}/{len(candidates)} paths tested...", "info"))
            
            cap = cv2.VideoCapture(url)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    path = builder.extract_path(url)
                    self.root.after(0, lambda p=path, i=idx: self.log(f"Connection found! Path: {p} (tested {i} paths)", "success"))
                    return cap, url
                cap.release()
        
        return None, None
        
    def on_connect_complete(self, cap, url):
        """Called when connection attempt completes"""
        self.connect_btn.config(state='normal', text="🔌 Connect to Camera")
        
        if cap:
            self.cap = cap
            self.working_url = url
            self.log("Camera connected successfully!", "success")
            
            # Save config
            builder = RTSPBuilder()
            path = builder.extract_path(url)
            
            config = self.load_config()
            if 'cameras' not in config:
                config['cameras'] = {}
            
            config['last_ip'] = self.target_ip
            config['cameras'][self.target_ip] = {
                'username': self.username.get(),
                'password': self.password.get(),
                'rtsp_path': path
            }
            self.save_config(config)
            
            self.stream_btn.config(state='normal')
            messagebox.showinfo(
                "Connected!",
                f"Successfully connected to camera at {self.target_ip}\n\n"
                "You can now start streaming."
            )
        else:
            self.log("Failed to connect to camera", "error")
            messagebox.showerror(
                "Connection Failed",
                "Could not connect to camera.\n\n"
                "Possible issues:\n"
                "• Wrong password\n"
                "• RTSP disabled\n"
                "• Network issues\n"
                "• Incompatible camera model"
            )
    
    def toggle_stream(self):
        """Starts or stops streaming"""
        if self.is_streaming:
            self.stop_stream()
        else:
            self.start_stream()
    
    def start_stream(self):
        """Starts video streaming"""
        if not self.cap:
            messagebox.showerror("Error", "No camera connected")
            return
        
        self.is_streaming = True
        self.stream_btn.config(text="⏸ Stop Streaming", bg='#d13438', activebackground='#e13438')
        self.log("Stream started", "success")
        self.frame_count = 0
        self.upload_count = 0
        self.stream_start_time = time.time()
        
        self.update_video()
        
    def stop_stream(self):
        """Stops video streaming"""
        self.is_streaming = False
        self.stream_btn.config(text="▶ Start Streaming", bg='#0e639c', activebackground='#1177bb')
        self.log("Stream stopped", "warn")
        
        # Show black screen
        self.video_label.config(
            image='',
            text="Stream Stopped\n\nClick 'Start Streaming' to resume",
            bg='#000000'
        )
    
    def update_video(self):
        """Updates video frame"""
        if not self.is_streaming:
            return
        
        ret, frame = self.cap.read()
        if not ret:
            self.log("Lost connection, attempting reconnect...", "warn")
            self.cap.release()
            self.cap = cv2.VideoCapture(self.working_url)
            self.root.after(100, self.update_video)
            return
        
        self.frame_count += 1
        
        # Resize for display
        display_frame = cv2.resize(frame, (720, 480))
        
        # Add overlays
        cv2.putText(display_frame, f"Camera: {self.target_ip}", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(display_frame, f"Frame: {self.frame_count}", (10, 60),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(display_frame, f"Uploaded: {self.upload_count}", (10, 90),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        uptime = int(time.time() - self.stream_start_time)
        uptime_str = f"{uptime//3600:02d}:{(uptime%3600)//60:02d}:{uptime%60:02d}"
        cv2.putText(display_frame, f"Uptime: {uptime_str}", (10, 120),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        # Convert to PhotoImage
        cv2image = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(cv2image)
        imgtk = ImageTk.PhotoImage(image=img)
        
        self.video_label.imgtk = imgtk
        self.video_label.config(image=imgtk, text='', bg='#000000')
        
        # Upload frame
        now = time.time()
        if now - self.last_upload >= 1.0:
            self.upload_frame(frame)
            self.last_upload = now
        
        # Update stats
        self.stats_label.config(
            text=f"Frames: {self.frame_count}\nUploaded: {self.upload_count}\nUptime: {uptime_str}"
        )
        
        # Schedule next frame
        self.root.after(30, self.update_video)
    
    def upload_frame(self, frame):
        """Uploads frame to cloud API"""
        def upload_thread():
            try:
                small_frame = cv2.resize(frame, (640, 360))
                _, buf = cv2.imencode('.jpg', small_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                b64_img = base64.b64encode(buf).decode('utf-8')
                
                payload = {
                    "camera_ip": self.target_ip,
                    "timestamp": time.time(),
                    "image": b64_img
                }
                
                import requests
                resp = requests.post(
                    f"http://localhost:{MOCK_API_PORT}/api/recognize",
                    json=payload,
                    timeout=2
                )
                
                if resp.status_code == 200:
                    self.upload_count += 1
                    
            except Exception as e:
                self.root.after(0, lambda: self.log(f"Upload error: {str(e)[:50]}", "error"))
        
        threading.Thread(target=upload_thread, daemon=True).start()
    
    def load_config(self):
        """Loads config file"""
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        return {}
    
    def save_config(self, data):
        """Saves config file"""
        os.makedirs("config", exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=2)

def main():
    root = tk.Tk()
    app = SmartEntryGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
