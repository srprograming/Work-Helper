from flask import Flask, request, redirect, session, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta # timedelta ইম্পোর্ট করা হয়েছে
import requests
import os
import uuid # ইউনিক ফাইলের নামের জন্য

app = Flask(__name__)

# --- Configuration ---
# এনভায়রনমেন্ট ভেরিয়েবল থেকে লোড করা হবে, লোকাল টেস্টিংয়ের জন্য সরাসরি বসানো যেতে পারে
app.secret_key = os.environ.get('SECRET_KEY', 'your_local_secret_key_123')
APP_ID = os.environ.get('APP_ID', 'YOUR_LOCAL_APP_ID')
APP_SECRET = os.environ.get('APP_SECRET', 'YOUR_LOCAL_APP_SECRET')
REDIRECT_URI = os.environ.get('REDIRECT_URI', 'http://localhost:5000/callback')

# Database Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///posts.db'
db = SQLAlchemy(app)


# --- Database Model ---
class ScheduledPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=True)
    post_time = db.Column(db.DateTime, nullable=False)
    page_id = db.Column(db.String(100), nullable=False)
    page_access_token = db.Column(db.Text, nullable=False)
    media_path = db.Column(db.String(300), nullable=True)
    media_type = db.Column(db.String(50), nullable=True) 
    status = db.Column(db.String(50), default='pending')

    def __repr__(self):
        return f'<Post {self.id}>'


# --- Routes ---
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
    pages_url = "https://graph.facebook.com/me/accounts"
    params = {'access_token': user_access_token, 'fields': 'name,id,access_token'}
    response = requests.get(pages_url, params=params)
    pages_data = response.json()
    pages = pages_data.get('data', [])
    return render_template('profile.html', pages=pages)

@app.route('/schedule_post', methods=['POST'])
def schedule_post():
    if 'access_token' not in session: 
        return jsonify({'status': 'error', 'message': 'User not logged in.'}), 401
    
    # ফর্ম থেকে ডেটা গ্রহণ
    message = request.form.get('message')
    selected_pages = request.form.getlist('selected_pages')
    media_files = request.files.getlist('media_files') # একাধিক ফাইল গ্রহণ
    start_time_str = request.form.get('start_time')
    interval_minutes = int(request.form.get('interval_minutes', 60))

    if not start_time_str or not selected_pages:
        return jsonify({'status': 'error', 'message': 'Please select pages and a start time.'}), 400
    if not message and not media_files:
        return jsonify({'status': 'error', 'message': 'Please provide a message or upload at least one file.'}), 400

    start_time = datetime.fromisoformat(start_time_str)
    interval = timedelta(minutes=interval_minutes)
    current_schedule_time = start_time

    # একাধিক ফাইল আপলোডের জন্য লুপ
    if media_files and media_files[0].filename != '':
        for media_file in media_files:
            # ফাইলের জন্য একটি ইউনিক নাম তৈরি করা
            filename = str(uuid.uuid4()) + "_" + media_file.filename
            uploads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
            if not os.path.exists(uploads_dir):
                os.makedirs(uploads_dir)
            media_path = os.path.join(uploads_dir, filename)
            media_file.save(media_path)
            
            content_type = media_file.content_type
            media_type = 'video' if 'video' in content_type else ('photo' if 'image' in content_type else None)

            # প্রতিটি পেজের জন্য এই পোস্টটি শিডিউল করা
            for page in selected_pages:
                page_id, page_access_token = page.split('|')
                new_post = ScheduledPost(
                    message=message, post_time=current_schedule_time, page_id=page_id,
                    page_access_token=page_access_token, media_path=media_path,
                    media_type=media_type, status='pending'
                )
                db.session.add(new_post)
            
            current_schedule_time += interval
    # শুধু টেক্সট পোস্ট শিডিউল করার জন্য
    else:
        for page in selected_pages:
            page_id, page_access_token = page.split('|')
            new_post = ScheduledPost(
                message=message, post_time=start_time, page_id=page_id,
                page_access_token=page_access_token, status='pending'
            )
            db.session.add(new_post)

    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Your posts have been scheduled successfully!'})


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)