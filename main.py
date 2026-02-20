import cv2
import time
import json
import os
import base64
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from modules.discovery import scan_network, scan_specific_ip
from modules.rtsp_builder import RTSPBuilder
from modules.logger import *

# --- CONFIGURATION ---
CONFIG_FILE = "config/settings.json"
MOCK_API_PORT = 5000

# --- MOCK CLOUD API (For Testing) ---
class MockCloudAPI(BaseHTTPRequestHandler):
    """
    A simple local HTTP server that simulates your cloud API.
    This lets you test the agent without needing actual cloud infrastructure.
    """
    
    def do_POST(self):
        if self.path == '/api/recognize':
            # Read the incoming data
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                camera_ip = data.get('camera_ip', 'unknown')
                timestamp = data.get('timestamp', 0)
                image_size = len(data.get('image', '')) // 1024  # KB
                
                # Simulate processing
                response = {
                    "status": "success",
                    "camera_ip": camera_ip,
                    "timestamp": timestamp,
                    "recognized_faces": [],  # Empty for now
                    "message": f"Frame received ({image_size} KB)"
                }
                
                # Send response
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
                
                # Log to console
                log_success(f"[MOCK API] Received frame from {camera_ip} ({image_size} KB)")
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                error_response = {"status": "error", "message": str(e)}
                self.wfile.write(json.dumps(error_response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress default HTTP server logs
        pass

def start_mock_api_server():
    """
    Starts the mock API server in a background thread.
    This simulates your cloud endpoint for testing.
    """
    server = HTTPServer(('localhost', MOCK_API_PORT), MockCloudAPI)
    log_info(f"Mock Cloud API started at http://localhost:{MOCK_API_PORT}/api/recognize")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

# --- CONFIG MANAGEMENT ---
def load_config():
    """Loads saved camera configuration"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_config(data):
    """Saves camera configuration for faster reconnection"""
    os.makedirs("config", exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# --- RTSP CONNECTION ---
def find_working_stream(ip, user, pwd, saved_path=None):
    """
    Tries all vendor paths until one opens.
    If saved_path is provided, tries that first.
    
    Returns: 
        tuple: (cv2.VideoCapture object, working URL string)
    """
    builder = RTSPBuilder()
    
    # If we have a saved working path, try it first
    if saved_path:
        log_info(f"Trying saved path: {saved_path}")
        candidates = builder.build_url(ip, user, pwd, path_override=saved_path)
        
        for url in candidates:
            cap = cv2.VideoCapture(url)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    log_success(f"Reconnected using saved path!")
                    return cap, url
                cap.release()
        
        log_warn("Saved path failed. Scanning all possibilities...")
    
    # Try all possible paths
    candidates = builder.build_url(ip, user, pwd)
    
    log_info(f"Testing up to {len(candidates)} possible RTSP paths for {ip}...")
    log_info("This may take a minute - testing all channels and stream types...")
    
    for idx, url in enumerate(candidates, 1):
        # Mask credentials in logs
        masked_url = url.replace(user, "***").replace(pwd.replace('@', '%40'), "***")
        
        # Show progress every 10 attempts
        if idx % 10 == 0:
            log_info(f"Progress: {idx}/{len(candidates)} paths tested...")
        
        cap = cv2.VideoCapture(url)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                # Extract the path for saving
                path = builder.extract_path(url)
                log_success(f"✓ Connection Established!")
                log_info(f"Working Path: {path}")
                log_info(f"Tested {idx} paths before finding match")
                return cap, url
            cap.release()
    
    return None, None

# --- MAIN AGENT ---
def start_agent():
    """Main application loop"""
    print("\n" + "="*50)
    print("   SMART ENTRY - DESKTOP AGENT v1.0")
    print("   Production-Grade Multi-Vendor Camera Support")
    print("="*50 + "\n")

    # Start Mock API Server for testing
    log_info("Starting Mock Cloud API (for testing)...")
    mock_server = start_mock_api_server()
    time.sleep(1)  # Let server initialize
    
    print("\n" + "-"*50)
    log_info("Camera Discovery Mode")
    print("-"*50)
    print("\nOptions:")
    print("  [1] Auto-Scan Network (Recommended)")
    print("  [2] Manual IP Entry")
    print("  [3] Use Last Connected Camera")
    
    # 1. DISCOVERY
    try:
        mode = input("\nSelect Mode (1-3): ").strip()
    except:
        log_error("Invalid input.")
        return

    cameras = []
    target_ip = None
    
    if mode == "3":
        # Try to use saved config
        config = load_config()
        if config.get('last_ip'):
            target_ip = config['last_ip']
            log_info(f"Using saved camera: {target_ip}")
            if not scan_specific_ip(target_ip):
                log_error("Saved camera is not reachable. Try another mode.")
                return
        else:
            log_error("No saved camera found. Please use mode 1 or 2 first.")
            return
    
    elif mode == "2":
        # Manual IP entry
        target_ip = input("Enter Camera IP: ").strip()
        if not scan_specific_ip(target_ip):
            log_error("Camera not found at that IP.")
            return
    
    elif mode == "1":
        # Auto-scan
        subnet = input("Enter subnet to scan (or press Enter for auto-detect): ").strip()
        cameras = scan_network(subnet if subnet else None)
        
        if not cameras:
            log_error("No Cameras found. Ensure PC and Camera are on same network.")
            return

        print("\n" + "="*50)
        print("Available Cameras:")
        print("="*50)
        for idx, ip in enumerate(cameras, 1):
            print(f"  [{idx}] {ip}")
        
        # Selection
        try:
            choice = int(input("\nSelect Camera Number: ")) - 1
            target_ip = cameras[choice]
        except:
            log_error("Invalid selection.")
            return
    
    else:
        log_error("Invalid mode.")
        return

    # 2. AUTHENTICATION
    print("\n" + "-"*50)
    log_info(f"Configure Camera: {target_ip}")
    print("-"*50)
    
    # Check if we have saved credentials
    config = load_config()
    saved_creds = config.get('cameras', {}).get(target_ip, {})
    
    if saved_creds.get('username'):
        print(f"\nSaved credentials found for {target_ip}")
        use_saved = input("Use saved credentials? (y/n): ").strip().lower()
        if use_saved == 'y':
            user = saved_creds['username']
            pwd = saved_creds['password']
            log_info("Using saved credentials")
        else:
            user = input("Username (default: admin): ") or "admin"
            pwd = input("Password: ")
    else:
        user = input("Username (default: admin): ") or "admin"
        pwd = input("Password: ")

    # 3. CONNECTION TEST
    print("\n" + "-"*50)
    log_info("Testing RTSP Connection...")
    print("-"*50)
    
    saved_path = saved_creds.get('rtsp_path') if saved_creds else None
    cap, working_url = find_working_stream(target_ip, user, pwd, saved_path)
    
    if not cap:
        log_error("❌ Could not connect to camera.")
        log_error("Possible issues:")
        log_error("  • Wrong password")
        log_error("  • Camera not on same network")
        log_error("  • RTSP disabled on camera")
        log_error("  • Firewall blocking port 554")
        return

    # Save working config
    builder = RTSPBuilder()
    working_path = builder.extract_path(working_url)
    
    config = load_config()
    if 'cameras' not in config:
        config['cameras'] = {}
    
    config['last_ip'] = target_ip
    config['cameras'][target_ip] = {
        'username': user,
        'password': pwd,
        'rtsp_path': working_path
    }
    save_config(config)
    log_success("Configuration saved for next time!")

    # 4. STREAMING LOOP
    print("\n" + "="*50)
    log_success("✓ Camera Connected Successfully!")
    print("="*50)
    log_info("Starting Live Stream...")
    log_info("Press 'Q' in the preview window to stop")
    print("-"*50 + "\n")
    
    frame_count = 0
    last_upload = 0
    UPLOAD_INTERVAL = 1.0  # Upload every 1 second
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                log_warn("Lost Stream. Reconnecting...")
                cap.release()
                cap = cv2.VideoCapture(working_url)
                time.sleep(2)
                continue

            now = time.time()
            frame_count += 1
            
            # Show Local Preview
            preview = cv2.resize(frame, (640, 360))
            cv2.putText(preview, f"Camera: {target_ip}", (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.putText(preview, f"Frames: {frame_count}", (10, 60), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.putText(preview, "Press 'Q' to quit", (10, 340), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
            cv2.imshow("SmartEntry Agent - Live Preview", preview)
            
            # Upload Logic (Once per second)
            if now - last_upload >= UPLOAD_INTERVAL:
                # Resize to save bandwidth (640x360 is enough for face recognition)
                small_frame = cv2.resize(frame, (640, 360))
                
                # Encode as JPEG
                _, buf = cv2.imencode('.jpg', small_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                b64_img = base64.b64encode(buf).decode('utf-8')
                
                # Prepare payload
                payload = {
                    "camera_ip": target_ip,
                    "timestamp": now,
                    "image": b64_img
                }
                
                # Send to mock API (in production, change to your real cloud URL)
                try:
                    import requests
                    resp = requests.post(
                        f"http://localhost:{MOCK_API_PORT}/api/recognize", 
                        json=payload,
                        timeout=2
                    )
                    
                    if resp.status_code == 200:
                        log_stream(target_ip, f"Frame Uploaded ({len(b64_img)//1024} KB) ✓")
                    else:
                        log_stream(target_ip, f"Upload Failed (HTTP {resp.status_code})")
                        
                except Exception as e:
                    log_stream(target_ip, f"Upload Error: {str(e)[:30]}")
                
                last_upload = now

            # Check for quit
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
    except KeyboardInterrupt:
        log_info("\nStopping Agent...")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        log_success("Agent stopped. Configuration saved.")

if __name__ == "__main__":
    start_agent()
