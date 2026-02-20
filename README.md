# SmartEntry Desktop Agent

**Production-Grade Multi-Vendor Camera Streaming Agent**

A robust desktop application that automatically discovers RTSP cameras on your network, connects to them regardless of vendor (CP Plus, Hikvision, Dahua, etc.), and streams frames to a cloud API for facial recognition processing.

---

## ✨ Key Features

- **🔍 Auto-Discovery**: Scans your network for RTSP cameras automatically
- **🔐 Smart Authentication**: Handles special characters in passwords (like `@` symbols)
- **🎯 Dynamic Channel Detection**: Tests all possible channels (1-16) and stream types
- **📡 Multi-Vendor Support**: Works with CP Plus, Hikvision, Dahua, and generic ONVIF cameras
- **💾 Config Memory**: Remembers working configurations for instant reconnection
- **🧪 Built-in Mock API**: Includes local testing server (no cloud needed for testing)
- **🎨 Live Preview**: Real-time video preview with status overlay
- **⚡ Efficient Upload**: Compresses frames before upload to save bandwidth

---

## 📁 Project Structure

```
SmartEntry_Agent/
├── config/
│   └── settings.json        # Auto-generated (stores camera configs)
├── modules/
│   ├── discovery.py         # Network scanning & IP detection
│   ├── rtsp_builder.py      # Dynamic RTSP URL generation
│   └── logger.py            # Colored console logging
├── gui_app.py               # GUI Desktop Application (NEW!)
├── main.py                  # CLI Application
├── requirements.txt         # Python dependencies
└── README.md               # This file
```

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

**Required Packages:**
- `opencv-python` - Video capture and processing
- `requests` - HTTP communication with cloud API
- `colorama` - Colored terminal output
- `urllib3` - URL encoding utilities
- `Pillow` - Image processing for GUI

### 2. Run the Application

**Option A: GUI Mode (Recommended for Users)**
```bash
python gui_app.py
```

**Option B: CLI Mode (For Advanced Users)**
```bash
python main.py
```

### 3. Using the GUI Application

The GUI provides a professional desktop interface with:

**🔍 Camera Discovery Panel:**
- Click "Auto-Scan Network" to find all cameras automatically
- Or enter IP manually and click "Check"
- Select camera from the list

**🔐 Credentials Panel:**
- Enter username (default: `admin`)
- Enter password (e.g., `admin@123`)
- Click "Connect to Camera"

**📡 Stream Control:**
- Click "Start Streaming" to begin
- View live preview with real-time statistics
- Monitor console logs for all events
- Click "Stop Streaming" when done

**Features:**
- ✅ Dark modern UI theme
- ✅ Real-time video preview (720x480)
- ✅ Live statistics display
- ✅ Color-coded console logs
- ✅ Progress indicators
- ✅ Error notifications
- ✅ Configuration auto-save

### 4. Using CLI Mode (Alternative)

When you run the agent for the first time:

1. **Select Discovery Mode:**
   - `[1]` Auto-Scan Network (Recommended) - Scans your entire subnet
   - `[2]` Manual IP Entry - If you know the camera's IP
   - `[3]` Use Last Camera - Quick reconnect to saved camera

2. **Select Your Camera:**
   - If auto-scanning, it will list all found cameras
   - Choose the number corresponding to your camera

3. **Enter Credentials:**
   - Username (default: `admin`)
   - Password (e.g., `admin@123`)
   - The agent will automatically URL-encode special characters

4. **Wait for Connection:**
   - The agent will test all possible RTSP paths
   - This may take 30-60 seconds on first connection
   - Once found, the path is saved for instant future connections

5. **Stream Active:**
   - Live preview window opens
   - Frames are uploaded to the API every second
   - Press `Q` to quit

---

## 🔧 Configuration

### Saved Settings

Camera configurations are automatically saved to `config/settings.json`:

```json
{
  "last_ip": "192.168.100.11",
  "cameras": {
    "192.168.100.11": {
      "username": "admin",
      "password": "admin@123",
      "rtsp_path": "/cam/realmonitor?channel=2&subtype=0"
    }
  }
}
```

**Benefits:**
- Instant reconnection (no re-scanning)
- Multi-camera support (saves configs for all cameras you've used)
- Credential storage (optional - you can delete saved passwords manually)

### Cloud API Integration

**For Testing (Default):**
The agent includes a built-in mock API server running at `http://localhost:5000/api/recognize`.

**For Production:**
Edit `main.py` line 333-338:

```python
# Change this:
resp = requests.post(
    f"http://localhost:{MOCK_API_PORT}/api/recognize", 
    json=payload,
    timeout=2
)

# To your real cloud URL:
resp = requests.post(
    "https://your-cloud-api.com/api/recognize",
    json=payload,
    timeout=5,
    headers={"Authorization": "Bearer YOUR_TOKEN"}  # Add if needed
)
```

**Expected Payload Format:**
```json
{
  "camera_ip": "192.168.100.11",
  "timestamp": 1736188800.123,
  "image": "base64_encoded_jpeg_string_here..."
}
```

---

## 🎯 How It Works

### 1. Discovery Phase
- Scans subnet (e.g., `192.168.100.0/24`) for port 554 (RTSP)
- Uses multi-threaded scanning for speed (100 concurrent checks)
- Identifies all devices with RTSP service running

### 2. Connection Phase
- Generates RTSP URLs dynamically using templates:
  - CP Plus/Dahua: `/cam/realmonitor?channel=X&subtype=Y`
  - Hikvision: `/Streaming/Channels/X01`
  - Generic: `/live/chX`, `/h264_stream`, etc.
- Tests channels 1-16 with both main and sub-streams
- Validates each URL by attempting to read a frame
- Saves the working path for future use

### 3. Streaming Phase
- Captures frames at camera's native FPS
- Resizes to 640x360 for efficient upload
- Compresses to JPEG (quality 70%)
- Base64 encodes for JSON transmission
- Uploads to cloud API every 1 second
- Displays live preview with status overlay

### 4. Error Handling
- Auto-reconnects on stream loss
- Retries failed uploads
- Logs all errors with timestamps
- Graceful shutdown on Ctrl+C or 'Q' key

---

## 🛠️ Troubleshooting

### "No Cameras Found"

**Possible Causes:**
- Camera and PC not on same network
- Firewall blocking port 554
- Camera RTSP disabled

**Solutions:**
1. Check network connection: `ping [camera_ip]`
2. Verify camera's RTSP is enabled (check camera web interface)
3. Try manual IP entry mode
4. Disable Windows Firewall temporarily to test

### "Could Not Connect to Camera"

**Possible Causes:**
- Wrong password
- Special characters in password not handled by camera
- Camera using non-standard RTSP port

**Solutions:**
1. Double-check password (case-sensitive)
2. Try logging into camera's web interface with same credentials
3. Check if camera uses port other than 554
4. Try different username (some cameras use `Administrator` instead of `admin`)

### "Upload Failed"

**Possible Causes:**
- Mock API server not running
- Real cloud API unreachable
- Network connectivity issues

**Solutions:**
1. Check console for Mock API startup message
2. Test cloud API separately: `curl -X POST http://your-api.com/test`
3. Verify payload size isn't too large (check API limits)

### "Lost Stream. Reconnecting..."

**Possible Causes:**
- Network congestion
- Camera rebooted
- WiFi signal weak

**Solutions:**
- Check network stability
- Use wired connection if possible
- Reduce upload frequency (increase `UPLOAD_INTERVAL`)

---

## 🔒 Security Notes

### Credential Storage
- Passwords are stored in plain text in `config/settings.json`
- For production, consider encrypting this file
- Or remove password storage and require manual entry each time

### Network Security
- RTSP credentials are transmitted in the URL (encoded but not encrypted)
- Use VPN or secure network for production deployment
- Consider implementing RTSP over TLS if camera supports it

### API Communication
- Mock API uses HTTP (unencrypted)
- Production should use HTTPS
- Add authentication headers/tokens for cloud API

---

## 📊 Performance

**Typical Metrics:**
- Network scan: 10-30 seconds (254 IPs)
- Connection test: 30-60 seconds first time, <2 seconds with saved config
- Frame capture: 15-30 FPS (depends on camera)
- Upload rate: 1 frame/second (adjustable)
- Bandwidth usage: ~50-100 KB/sec per camera

**Optimization Tips:**
- Increase `UPLOAD_INTERVAL` to reduce uploads (e.g., 2.0 = every 2 seconds)
- Decrease JPEG quality for smaller file size (line 328: change 70 to 50)
- Use sub-stream instead of main stream (already prioritized in code)

---

## 🔄 Future Enhancements

Planned features for v2.0:
- Multi-camera simultaneous streaming
- Motion detection (upload only on movement)
- Offline frame buffering (upload when connection restored)
- Web-based dashboard for monitoring
- Encrypted credential storage
- Docker containerization
- GPU-accelerated frame processing

---

## 📝 License

This is a production-ready tool for SmartEntry project.
Use and modify as needed for your deployment.

---

## 🆘 Support

For issues or questions:
1. Check the troubleshooting section above
2. Review console logs (color-coded for easy debugging)
3. Test with Mock API first before switching to production cloud
4. Verify camera is accessible via web browser before using agent

---

**Built with ❤️ for robust, vendor-agnostic camera integration**
