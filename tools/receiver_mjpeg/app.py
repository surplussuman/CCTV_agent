from flask import Flask, request, Response, render_template, send_from_directory
import os
import threading
import time
from datetime import datetime

app = Flask(__name__, static_folder='static', template_folder='static')

# Directory to save incoming frames (optional)
OUT_DIR = '/opt/face-receiver/received' if os.name != 'nt' else os.path.join(os.getcwd(), 'received')
os.makedirs(OUT_DIR, exist_ok=True)

# Shared latest frame state
latest_frame = None
frame_lock = threading.Lock()
frame_event = threading.Event()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/recognize', methods=['POST'])
def recognize():
    global latest_frame
    f = request.files.get('frame')
    camera_name = request.form.get('camera_name')
    camera_ip = request.form.get('camera_ip')
    timestamp = request.form.get('timestamp') or datetime.utcnow().isoformat()

    saved_filename = None
    if f:
        data = f.read()
        # update latest frame
        with frame_lock:
            latest_frame = data
            frame_event.set()
            frame_event.clear()
        # optionally save to disk
        ts = datetime.utcnow().strftime('%Y%m%dT%H%M%S%fZ')
        saved_filename = f"{camera_ip or 'unknown'}_{ts}.jpg"
        try:
            with open(os.path.join(OUT_DIR, saved_filename), 'wb') as wf:
                wf.write(data)
        except Exception:
            pass

    # simple response
    return {'status': 'ok', 'saved': saved_filename}, 200


def mjpeg_stream():
    boundary = '--frame'
    while True:
        # wait for a new frame
        frame_event.wait(timeout=5)
        with frame_lock:
            if latest_frame is None:
                # send a tiny blank jpg to keep connection alive
                time.sleep(0.1)
                continue
            data = latest_frame
        yield (b"\r\n" + boundary.encode() + b"\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + data + b"\r\n")


@app.route('/stream.mjpg')
def stream_mjpg():
    return Response(mjpeg_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')


if __name__ == '__main__':
    # For testing only. In production use gunicorn or systemd.
    app.run(host='0.0.0.0', port=5000, threaded=True)
