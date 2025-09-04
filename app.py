import os
import uuid
import time
import random
from datetime import datetime, timedelta
from flask import Flask, request, redirect, session, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
import requests

# --- App & DB Configuration ---
app = Flask(__name__)
# Render-এর জন্য Production-ready কনফিগারেশন
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///posts.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Load Secure Configuration from Environment Variables ---
app.secret_key = os.environ.get('SECRET_KEY')
APP_ID = os.environ.get('APP_ID')
APP_SECRET = os.environ.get('APP_SECRET')
REDIRECT_URI = os.environ.get('REDIRECT_URI')

# আপলোড ফোল্ডার তৈরি
UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
if not os.path.exists(UPLOADS_DIR):
    os.makedirs(UPLOADS_DIR)

# --- Database Model ---
class ScheduledPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    facebook_post_id = db.Column(db.String(100), nullable=True)
    message = db.Column(db.Text, nullable=True)
    post_time = db.Column(db.DateTime, nullable=False)
    delete_time = db.Column(db.DateTime, nullable=True)
    page_id = db.Column(db.String(100), nullable=False)
    page_access_token = db.Column(db.Text, nullable=False)
    media_path = db.Column(db.String(300), nullable=True)
    media_type = db.Column(db.String(50), nullable=True) 
    status = db.Column(db.String(50), default='pending')

# --- Helper Functions for Posting ---
def post_photo_to_facebook(page_id, page_access_token, file_path, caption):
    post_url = f"https://graph.facebook.com/v19.0/{page_id}/photos"
    with open(file_path, 'rb') as f:
        files = {'source': f}
        params = {'caption': caption, 'access_token': page_access_token}
        response = requests.post(post_url, files=files, params=params)
    response.raise_for_status()
    return response.json()

def upload_video_to_facebook(page_id, page_access_token, video_path, description, post_type='video'):
    try:
        endpoint = 'video_reels' if post_type == 'reel' else 'videos'
        init_url = f"https://graph.facebook.com/v19.0/{page_id}/{endpoint}"
        init_params = {'access_token': page_access_token, 'upload_phase': 'start', 'file_size': os.path.getsize(video_path)}
        init_response = requests.post(init_url, params=init_params).json()
        upload_session_id = init_response['upload_session_id']
        video_id = init_response['video_id']
        transfer_url = f"https://graph-video.facebook.com/{video_id}"
        headers = {'Authorization': f'OAuth {page_access_token}'}
        with open(video_path, 'rb') as f:
            files = {'video_file': f}
            requests.post(transfer_url, headers=headers, files=files)
        finish_params = {'access_token': page_access_token, 'upload_phase': 'finish', 'upload_session_id': upload_session_id, 'description': description}
        finish_response = requests.post(init_url, params=finish_params).json()
        return finish_response
    except Exception as e:
        print(f"Video/Reel upload failed: {e}")
        raise e

def post_text_to_facebook(page_id, page_access_token, message):
    post_url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
    params = {'message': message, 'access_token': page_access_token}
    response = requests.post(post_url, params=params)
    response.raise_for_status()
    return response.json()

# --- Standard Routes ---
@app.route('/')
def home():
    scope = 'pages_show_list,pages_manage_posts'
    login_url = f"https://www.facebook.com/v19.0/dialog/oauth?client_id={APP_ID}&redirect_uri={REDIRECT_URI}&scope={scope}"
    return f'<a href="{login_url}">Login with Facebook</a>'

@app.route('/callback')
def callback():
    auth_code = request.args.get('code')
    if not auth_code: return "Login Failed!", 400
    token_url = "https://graph.facebook.com/v19.0/oauth/access_token"
    params = {'client_id': APP_ID, 'redirect_uri': REDIRECT_URI, 'client_secret': APP_SECRET, 'code': auth_code}
    response = requests.get(token_url, params=params)
    token_data = response.json()
    if 'error' in token_data: return f"Error: {token_data['error']['message']}"
    session['access_token'] = token_data.get('access_token')
    return redirect('/profile')

@app.route('/profile')
def profile():
    if 'access_token' not in session: return redirect('/')
    user_access_token = session['access_token']
    pages_url = f"https://graph.facebook.com/me/accounts?access_token={user_access_token}&fields=name,id,access_token"
    response = requests.get(pages_url)
    pages_data = response.json()
    pages = pages_data.get('data', [])
    return render_template('profile.html', pages=pages)

# --- Feature Routes ---
@app.route('/submit_post', methods=['POST'])
def submit_post():
    if 'access_token' not in session: 
        return jsonify({'status': 'error', 'message': 'User not logged in.'}), 401
    
    message = request.form.get('message')
    selected_pages = request.form.getlist('selected_pages')
    media_files = request.files.getlist('media_files')
    post_type = request.form.get('post_type')
    schedule_time_str = request.form.get('start_time')
    auto_delete_enabled = request.form.get('enable_auto_delete') == 'on'
    delete_after_days = int(request.form.get('delete_after_days', 7))

    if not selected_pages: return jsonify({'status': 'error', 'message': 'Please select at least one page.'}), 400
    if not message and not (media_files and media_files[0].filename): return jsonify({'status': 'error', 'message': 'Please provide a message or upload at least one file.'}), 400

    if schedule_time_str:
        interval_minutes = int(request.form.get('interval_minutes', 60))
        batch_size = int(request.form.get('batch_size', 1))
        time_jitter = int(request.form.get('time_jitter', 0))
        start_time = datetime.fromisoformat(schedule_time_str)
        interval = timedelta(minutes=interval_minutes)
        current_schedule_time = start_time
        batch_counter = 0

        if media_files and media_files[0].filename:
            for media_file in media_files:
                batch_counter += 1
                filename = str(uuid.uuid4()) + "_" + media_file.filename
                media_path = os.path.join(UPLOADS_DIR, filename)
                media_file.save(media_path)
                
                jitter_amount = random.uniform(-time_jitter, time_jitter)
                final_post_time = current_schedule_time + timedelta(minutes=jitter_amount)
                delete_time = final_post_time + timedelta(days=delete_after_days) if auto_delete_enabled else None
                
                for page in selected_pages:
                    page_id, page_access_token = page.split('|')
                    new_post = ScheduledPost(
                        message=message, post_time=final_post_time, delete_time=delete_time, page_id=page_id,
                        page_access_token=page_access_token, media_path=media_path,
                        media_type=post_type, status='pending')
                    db.session.add(new_post)
                
                if batch_counter >= batch_size:
                    current_schedule_time += interval
                    batch_counter = 0
        else:
             final_post_time = start_time
             delete_time = final_post_time + timedelta(days=delete_after_days) if auto_delete_enabled else None
             for page in selected_pages:
                page_id, page_access_token = page.split('|')
                new_post = ScheduledPost(message=message, post_time=final_post_time, delete_time=delete_time, page_id=page_id, page_access_token=page_access_token, status='pending')
                db.session.add(new_post)
        
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Posts scheduled successfully!'})
    else:
        post_count = 0
        total_to_make = (len(media_files) * len(selected_pages)) if (media_files and media_files[0].filename) else len(selected_pages)

        if media_files and media_files[0].filename:
            for media_file in media_files:
                filename = str(uuid.uuid4()) + "_" + media_file.filename
                media_path = os.path.join(UPLOADS_DIR, filename)
                media_file.save(media_path)
                
                for page in selected_pages:
                    page_id, page_access_token = page.split('|')
                    try:
                        response_data = {}
                        if post_type == 'photo':
                            response_data = post_photo_to_facebook(page_id, page_access_token, media_path, message)
                        elif post_type in ['video', 'reel']:
                            response_data = upload_video_to_facebook(page_id, page_access_token, media_path, message, post_type)
                        
                        post_count += 1
                        if auto_delete_enabled:
                            post_time = datetime.utcnow()
                            delete_time = post_time + timedelta(days=delete_after_days)
                            post_id = response_data.get('id') or response_data.get('post_id')
                            if post_id:
                                new_entry = ScheduledPost(
                                    facebook_post_id=post_id, message=message, post_time=post_time, 
                                    delete_time=delete_time, page_id=page_id, page_access_token=page_access_token, status='posted')
                                db.session.add(new_entry)
                                db.session.commit()
                        print(f"Successfully posted to page {page_id}")
                    except Exception as e:
                        print(f"Failed to post media to {page_id}. Error: {e}")
                
                os.remove(media_path)
                if post_count < total_to_make:
                    delay = random.uniform(15, 45)
                    print(f"Waiting for {delay:.2f} seconds...")
                    time.sleep(delay)
        else: 
            for page in selected_pages:
                page_id, page_access_token = page.split('|')
                try:
                    response_data = post_text_to_facebook(page_id, page_access_token, message)
                    post_count += 1
                    if auto_delete_enabled:
                         post_time = datetime.utcnow()
                         delete_time = post_time + timedelta(days=delete_after_days)
                         new_entry = ScheduledPost(
                            facebook_post_id=response_data.get('id'), message=message, post_time=post_time, 
                            delete_time=delete_time, page_id=page_id, page_access_token=page_access_token, status='posted')
                         db.session.add(new_entry)
                         db.session.commit()
                except Exception as e:
                    print(f"Failed to post text to {page_id}. Error: {e}")
        
        return jsonify({'status': 'success', 'message': f'Published {post_count} posts instantly!'})

@app.route('/delete_content', methods=['POST'])
def delete_content():
    if 'access_token' not in session: 
        return jsonify({'status': 'error', 'message': 'User not logged in.'}), 401
    
    selected_pages_data = request.json.get('pages')
    if not selected_pages_data:
        return jsonify({'status': 'error', 'message': 'No pages selected.'}), 400
    
    deleted_count = 0
    for page_data in selected_pages_data:
        page_id, page_access_token = page_data.split('|')
        feed_url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
        params = {'access_token': page_access_token, 'limit': 25}
        
        while feed_url:
            response = requests.get(feed_url, params=params).json()
            feed_items = response.get('data', [])
            if not feed_items: break
            
            for item in feed_items:
                item_id = item['id']
                try:
                    delete_url = f"https://graph.facebook.com/{item_id}"
                    delete_params = {'access_token': page_access_token}
                    del_response = requests.delete(delete_url, params=delete_params)
                    del_response.raise_for_status()
                    deleted_count += 1
                    print(f"Deleted item {item_id} from page {page_id}")
                    time.sleep(1) 
                except requests.exceptions.RequestException as e:
                    print(f"Could not delete item {item_id}. Reason: {e}")
            feed_url = response.get('paging', {}).get('next')
            params = {}
    
    return jsonify({'status': 'success', 'message': f'Deletion process completed. {deleted_count} items were deleted.'})

# --- Main Run Block ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # This block is for production servers like Render with Gunicorn
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)