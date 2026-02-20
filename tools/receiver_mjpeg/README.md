Receiver (MJPEG) — Quick Install & Test

This lightweight receiver accepts `multipart/form-data` POSTs with a `frame` file and exposes an MJPEG endpoint for live viewing.

Server setup (Ubuntu):

1. Create folder and virtualenv

```bash
sudo mkdir -p /opt/face-receiver
sudo chown $USER:$USER /opt/face-receiver
cd /opt/face-receiver
python3 -m venv venv
source venv/bin/activate
pip install flask
```

2. Copy the files from this repo's `tools/receiver_mjpeg` into `/opt/face-receiver` (or clone the repo)

3. Run for testing

```bash
source venv/bin/activate
python app.py
```

Open in your browser:

http://YOUR_SERVER_IP:5000/

It will show the live MJPEG stream at `/stream.mjpg`.

Agent configuration

Point your agent's upload URL to:

```
http://103.65.21.239:5000/api/recognize
```

Test with curl from any machine:

```bash
curl -v -F "frame=@test.jpg" \
  -F "camera_name=TestCam" \
  -F "camera_ip=192.168.100.11" \
  -F "timestamp=$(date -Iseconds)" \
  http://103.65.21.239:5000/api/recognize
```

Production notes

- Use Gunicorn + systemd or Docker for production.
- Put Nginx in front for TLS (Let's Encrypt) and to serve the static index page if desired.
- If you need lower latency / more robust streaming at scale, consider using an RTMP server (nginx-rtmp) and pushing an RTMP stream from the agent with ffmpeg; then serve HLS or DASH to browsers using `hls.js`.

Optional: Start with gunicorn

```bash
pip install gunicorn
gunicorn -b 0.0.0.0:5000 app:app
```

Systemd unit (example):

```
[Unit]
Description=Face Receiver
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/face-receiver
Environment="PATH=/opt/face-receiver/venv/bin"
ExecStart=/opt/face-receiver/venv/bin/gunicorn -b 0.0.0.0:5000 app:app

[Install]
WantedBy=multi-user.target
```

Security

- Add `X-API-KEY` header and validate it in `app.py` for basic auth.
- Use TLS via nginx reverse proxy.
- Rotate API keys and limit access by firewall rules (ufw).
