# jsonify ইম্পোর্ট করা হয়েছে
from flask import Flask, request, redirect, session, render_template, jsonify
import requests
import os

app = Flask(__name__)
app.secret_key = 'your_random_secret_key'

APP_ID = '4430394673855131'
APP_SECRET = '9c4c2e9020156eb1f285791d1afd593a'
REDIRECT_URI = 'http://localhost:5000/callback'

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

def upload_video_to_facebook(page_id, page_access_token, video_file, description, post_type='video'):
    try:
        endpoint = 'video_reels' if post_type == 'reel' else 'videos'
        init_url = f"https://graph.facebook.com/{page_id}/{endpoint}"
        
        init_params = {
            'access_token': page_access_token,
            'upload_phase': 'start',
            'file_size': os.fstat(video_file.fileno()).st_size
        }
        init_response = requests.post(init_url, params=init_params).json()
        
        upload_session_id = init_response['upload_session_id']
        transfer_url = init_response.get('upload_url')
        if not transfer_url:
            video_id = init_response['video_id']
            transfer_url = f"https://graph-video.facebook.com/{video_id}"

        headers = {'Authorization': f'OAuth {page_access_token}'}
        files = {'video_file': video_file}
        requests.post(transfer_url, headers=headers, files=files)
        
        finish_params = {
            'access_token': page_access_token,
            'upload_phase': 'finish',
            'upload_session_id': upload_session_id,
            'description': description
        }
        finish_response = requests.post(init_url, params=finish_params).json()

        return finish_response.get('success', False)
    except Exception as e:
        print(f"Video/Reel upload failed: {e}")
        return False

@app.route('/post_to_pages', methods=['POST'])
def post_to_pages():
    if 'access_token' not in session: 
        return jsonify({'status': 'error', 'message': 'User not logged in.'}), 401
    
    message = request.form.get('message')
    selected_pages = request.form.getlist('selected_pages')
    media_file = request.files.get('media_file')
    post_type = request.form.get('post_type', 'video')
    
    if not selected_pages:
        return jsonify({'status': 'error', 'message': 'Please select at least one page.'}), 400
    if not message and not (media_file and media_file.filename != ''):
        return jsonify({'status': 'error', 'message': 'Please write a message or upload a file.'}), 400
    
    post_count = 0
    total_selected = len(selected_pages)
    
    for page in selected_pages:
        page_id, page_access_token = page.split('|')
        
        is_video = media_file and media_file.content_type and media_file.content_type.startswith('video/')
        is_photo = media_file and media_file.content_type and media_file.content_type.startswith('image/')
        
        try:
            if is_video:
                success = upload_video_to_facebook(page_id, page_access_token, media_file, message, post_type)
                if success: post_count += 1
                media_file.seek(0)
            elif is_photo:
                post_url = f"https://graph.facebook.com/{page_id}/photos"
                files = {'source': (media_file.filename, media_file.read(), media_file.content_type)}
                params = {'caption': message, 'access_token': page_access_token}
                response = requests.post(post_url, files=files, params=params)
                response.raise_for_status()
                post_count += 1
                media_file.seek(0)
            else: # Text-only post
                post_url = f"https://graph.facebook.com/{page_id}/feed"
                params = {'message': message, 'access_token': page_access_token}
                response = requests.post(post_url, params=params)
                response.raise_for_status()
                post_count += 1
            
            print(f"Successfully initiated post for page ID: {page_id}")
            
        except requests.exceptions.RequestException as e:
            print(f"Failed to post to page ID: {page_id}. Error: {e.response.text if e.response else e}")
            
    if post_count > 0:
        return jsonify({
            'status': 'success',
            'message': f'Successfully initiated posts for {post_count} out of {total_selected} pages!'
        })
    else:
        return jsonify({
            'status': 'error',
            'message': 'Failed to post to any of the selected pages. Please check terminal for errors.'
        }), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)