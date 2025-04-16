import datetime
import threading
from flask import Flask, request, jsonify, Response, render_template, flash, redirect, send_from_directory, url_for
import requests
from flask_sqlalchemy import SQLAlchemy
import cv2
import numpy as np
from PIL import Image
import io
import logging
import os
import time  
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SECRET_KEY'] = 'test1234nkksdvkksv'
app.config['UPLOAD_FOLDER'] = 'static/recordings'
db = SQLAlchemy(app)



if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Camera Model
class Camera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    rtsp_url = db.Column(db.String(300), nullable=False)
    #recording = db.Column(db.Boolean, default=False)

AI_SERVER_URL = "http://192.168.101.66:5054"

# Add logging configuration at the top of the file
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Email Configuration
EMAIL_ADDRESS = "shubhamjma07@gmail.com"  # Replace with your Gmail address
EMAIL_PASSWORD = "hwnlamsxswbpxvhn"  # Replace with your Gmail app password
NOTIFICATION_EMAIL = "shubhamjma07@gmail.com"  # Replace with recipient's email address

# Dictionary to track the last email sent time for each camera and label
last_email_sent = {}
EMAIL_COOLDOWN = 100  # Cooldown time in seconds

recording_status = {}

def send_email_notification(camera_name, label, score, frame):
    """Send email notification with an image when a specific object is detected."""
    current_time = time.time()
    key = (camera_name, label)

    # Check if the cooldown period has passed
    if key in last_email_sent and current_time - last_email_sent[key] < EMAIL_COOLDOWN:
        logger.info(f"Cooldown active. Skipping email for {label} on {camera_name}.")
        return

    # Update the last email sent time
    last_email_sent[key] = current_time

    def email_task():
        try:
            subject = f"{label.capitalize()} Detected on Camera: {camera_name}"
            body = f"A {label} was detected on camera '{camera_name}' with confidence {score:.2f}.\n\nLabel: {label}"

            # Create email message
            msg = MIMEMultipart()
            msg['From'] = EMAIL_ADDRESS
            msg['To'] = NOTIFICATION_EMAIL
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            # Encode the frame as an image
            _, img_encoded = cv2.imencode('.jpg', frame)
            img_bytes = img_encoded.tobytes()

            # Attach the image to the email
            image_attachment = MIMEText(img_bytes, 'base64', 'utf-8')
            image_attachment.add_header('Content-Disposition', 'attachment', filename=f'detected_{label}.jpg')
            image_attachment.add_header('Content-Type', 'image/jpeg')
            msg.attach(image_attachment)

            # Send email
            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.send_message(msg)

            logger.info(f"Notification email with image sent to {NOTIFICATION_EMAIL}")
        except Exception as e:
            logger.error(f"Failed to send email notification: {str(e)}")

    # Run the email task in a separate thread
    threading.Thread(target=email_task).start()

@app.route('/')
def index():
    """Home page showing all cameras."""
    cameras = Camera.query.all()  # Fetch all cameras from the database
    return render_template('index.html', cameras=cameras)


@app.route('/add', methods=['GET', 'POST'])
def add_camera():
    """Add camera"""
    if request.method == 'POST':
        name = request.form['name']
        rtsp_url = request.form['rtsp_url']
        new_camera = Camera(name=name, rtsp_url=rtsp_url)
        db.session.add(new_camera)
        db.session.commit()
        flash('Camera Added Successfully!', 'success')
        return redirect(url_for('index'))
    return render_template('add_camera.html')


@app.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit_camera(id):
    """Edit camera """
    camera = Camera.query.get_or_404(id)
    if request.method == 'POST':
        camera.name = request.form['name']
        camera.rtsp_url = request.form['rtsp_url']
        db.session.commit()
        flash('Camera Updated Successfully!', 'success')
        return redirect(url_for('index'))
    return render_template('edit_camera.html', camera=camera)


@app.route('/delete/<int:id>')
def delete_camera(id):
    """Delete Camera"""
    camera = Camera.query.get_or_404(id)
    db.session.delete(camera)
    db.session.commit()
    flash('Camera Deleted Successfully!', 'danger')
    return redirect(url_for('index'))

# Added a check to ensure the video file is properly written and closed
# Improved error handling and logging for video recording

def record_video(camera_id):
    camera = Camera.query.get(camera_id)
    if not camera:
        logger.error(f"Camera with ID {camera_id} not found.")
        return

    cap = cv2.VideoCapture(camera.rtsp_url)
    if not cap.isOpened():
        logger.error(f"Failed to open RTSP stream for camera: {camera.name}")
        return

    filename = os.path.join(app.config['UPLOAD_FOLDER'], f"{camera.name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4")
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(filename, fourcc, 20.0, (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))))

    if not out.isOpened():
        logger.error(f"Failed to initialize video writer for file: {filename}")
        cap.release()
        return

    recording_status[camera_id] = True  # Track recording state

    try:
        while recording_status.get(camera_id, False):
            success, frame = cap.read()
            if not success:
                logger.warning(f"Failed to read frame from camera: {camera.name}")
                break
            out.write(frame)

    except Exception as e:
        logger.error(f"Error during video recording for camera {camera.name}: {str(e)}")

    finally:
        cap.release()
        out.release()
        recording_status[camera_id] = False
        logger.info(f"Recording stopped and file saved: {filename}")

# Start/Stop Recording
@app.route('/toggle_record/<int:id>')
def toggle_record(id):
    camera = Camera.query.get(id)
    if camera:
        if camera.recording:
            camera.recording = False
            db.session.commit()
            flash(f'Recording Stopped for {camera.name}', 'warning')
        else:
            camera.recording = True
            db.session.commit()
            thread = threading.Thread(target=record_video, args=(id,))
            thread.start()
            flash(f'Recording Started for {camera.name}', 'success')
    
    return redirect(url_for('index'))

# Updated the /recordings route to filter only .mp4 files and log the available videos
@app.route('/recordings')
def recordings():
    try:
        videos = [video for video in os.listdir(app.config['UPLOAD_FOLDER']) if video.endswith('.mp4')]
        logger.info(f"Available recordings: {videos}")
        return render_template('recordings.html', videos=videos)
    except Exception as e:
        logger.error(f"Error listing recordings: {str(e)}")
        flash('Failed to list recordings.', 'danger')
        return redirect(url_for('index'))

# Serve Recorded Videos
@app.route('/recordings/<filename>')
def play_video(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/health', methods=['GET'])
def health():
    response = requests.get(f"{AI_SERVER_URL}/health")
    return jsonify(response.json())

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if request.method == 'GET':
        response = requests.get(f"{AI_SERVER_URL}/setup")
    else:
        response = requests.post(f"{AI_SERVER_URL}/setup", json=request.json)
    return jsonify(response.json())

@app.route('/predict', methods=['POST'])
def predict():
    response = requests.post(f"{AI_SERVER_URL}/predict", json=request.json)
    return jsonify(response.json())

@app.route('/webhook', methods=['POST'])
def webhook():
    response = requests.post(f"{AI_SERVER_URL}/webhook", json=request.json)
    return jsonify(response.json())

@app.route('/image_exists', methods=['POST'])
def image_exists():
    response = requests.post(f"{AI_SERVER_URL}/image_exists", json=request.json)
    return jsonify(response.json())

def generate_frames(rtsp_url, camera_name):
    """Process video frames, detect objects, and check for ROI."""
    cap = cv2.VideoCapture(rtsp_url)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'H264'))
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'

    error_count = 0
    max_errors = 5
    retry_attempts = 3
    retry_delay = 2

    # Define the ROI (Region of Interest) as a rectangle (x1, y1, x2, y2)
    roi_x1, roi_y1, roi_x2, roi_y2 = 100, 100, 400,400  

    # List of objects to detect and notify
    notify_objects = ["person", "chair", "monitor"]

    try:
        while True:
            success, frame = cap.read()
            if not success:
                error_count += 1
                logger.warning(f"Failed to read frame. Error count: {error_count}")
                if error_count >= max_errors:
                    logger.info("Attempting to reconnect to RTSP stream...")
                    cap.release()
                    cap = cv2.VideoCapture(rtsp_url)
                    error_count = 0
                continue

            error_count = 0

            # Draw the ROI rectangle on the frame
            cv2.rectangle(frame, (roi_x1, roi_y1), (roi_x2, roi_y2), (255, 0, 0), 2)

            _, img_encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            files = {'image': ('image.jpg', img_encoded.tobytes(), 'image/jpeg')}

            for attempt in range(retry_attempts):
                try:
                    response = requests.post(f"{AI_SERVER_URL}/predict", files=files, timeout=0.5)
                    if response.status_code == 200:
                        predictions = response.json()
                        for pred in predictions.get('predictions', []):
                            bbox = pred.get('bbox', [])
                            if len(bbox) == 4:
                                x1, y1, x2, y2 = map(int, bbox)
                                label = pred.get('label', 'unknown').lower()
                                score = pred.get('score', 0)

                                # Draw bounding box
                                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                                text = f"{label}: {score:.2f}"
                                cv2.putText(frame, text, (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

                                # Check if the object is in the ROI
                                if label in notify_objects and score >= 0.5:  # Adjust confidence threshold as needed
                                    if (x1 < roi_x2 and x2 > roi_x1 and y1 < roi_y2 and y2 > roi_y1):  # Intersection check
                                        send_email_notification(camera_name, label, score, frame)
                        break
                except requests.exceptions.Timeout:
                    logger.warning(f"AI server prediction timeout (Attempt {attempt + 1}/{retry_attempts})")
                except requests.exceptions.RequestException as e:
                    logger.error(f"AI server connection error: {str(e)} (Attempt {attempt + 1}/{retry_attempts})")
                time.sleep(retry_delay)
            else:
                logger.error("Max retries exceeded for AI server connection")

            # Encode the frame with bounding boxes and yield it
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

    except Exception as e:
        logger.error(f"Error in frame processing: {str(e)}")
    finally:
        cap.release()

# Video Feed Route
@app.route('/video_feed/<int:id>')
def video_feed(id):
    camera = Camera.query.get_or_404(id)
    return Response(generate_frames(camera.rtsp_url, camera.name), mimetype='multipart/x-mixed-replace; boundary=frame')
    

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000)


