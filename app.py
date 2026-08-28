import os
import tempfile
from flask import Flask, render_template, request, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Configure the database (Uses Render's DATABASE_URL if available, otherwise local SQLite)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///music_library.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
# Use Render's secret path if it exists, otherwise look in the local folder
COOKIE_PATH = '/etc/secrets/cookies.txt' if os.path.exists('/etc/secrets/cookies.txt') else 'cookies.txt'


# --- DATABASE MODEL ---
class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.String(20), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    channel = db.Column(db.String(100))
    file_path = db.Column(db.String(255), nullable=False)

# Ensure the library directory exists
LIBRARY_DIR = 'library'
os.makedirs(LIBRARY_DIR, exist_ok=True)

# Create database tables
with app.app_context():
    db.create_all()

def format_duration(seconds):
    if not seconds:
        return "Unknown"
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}:{secs:02d}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/search', methods=['GET'])
def search_youtube():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': 'Search query is required'}), 400

    ydl_opts = {
        'format': 'bestaudio/best',
        'cookiefile': COOKIE_PATH, # <--- ADD THIS LINE
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.join(LIBRARY_DIR, '%(id)s_%(title)s.%(ext)s'),
        'quiet': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = ydl.extract_info(f"ytsearch5:{query}", download=False)
            
            items = []
            for entry in search_results.get('entries', []):
                video_id = entry.get('id')
                thumbnail = entry.get('thumbnail') or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                is_saved = Song.query.filter_by(video_id=video_id).first() is not None
                
                items.append({
                    'id': video_id,
                    'title': entry.get('title', 'Unknown Title'),
                    'duration': format_duration(entry.get('duration')),
                    'thumbnail': thumbnail,
                    'channel': entry.get('uploader') or entry.get('channel', 'YouTube'),
                    'in_library': is_saved
                })
                
            return jsonify({'results': items})
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/preview', methods=['GET'])
def preview_audio():
    video_id = request.args.get('id')
    if not video_id:
        return "Missing video ID", 400

    url = f"https://www.youtube.com/watch?v={video_id}"
    temp_dir = tempfile.mkdtemp()

    ydl_opts = {
        'format': 'bestaudio/best',
        'cookiefile': COOKIE_PATH,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'postprocessor_args': [
            '-ss', '00:00:10',
            '-t', '15'
        ],
        'outtmpl': os.path.join(temp_dir, 'preview_%(id)s.%(ext)s'),
        'quiet': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            raw_filename = ydl.prepare_filename(info)
            mp3_path = raw_filename.rsplit('.', 1)[0] + '.mp3'

        return send_file(mp3_path, mimetype='audio/mpeg')

    except Exception as e:
        return f"Preview failed: {str(e)}", 500

@app.route('/api/download', methods=['GET'])
def download_audio():
    video_id = request.args.get('id')
    if not video_id:
        return "Missing video ID", 400

    existing_song = Song.query.filter_by(video_id=video_id).first()
    
    if existing_song and os.path.exists(existing_song.file_path):
        return send_file(
            existing_song.file_path,
            as_attachment=True,
            download_name=f"{existing_song.title}.mp3",
            mimetype='audio/mpeg'
        )

    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        'format': 'bestaudio/best',
        'cookiefile': COOKIE_PATH,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.join(LIBRARY_DIR, '%(id)s_%(title)s.%(ext)s'),
        'quiet': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            raw_filename = ydl.prepare_filename(info)
            mp3_path = raw_filename.rsplit('.', 1)[0] + '.mp3'
            clean_title = info.get('title', 'audio')
            channel = info.get('uploader', 'Unknown')

        if not existing_song:
            new_song = Song(video_id=video_id, title=clean_title, channel=channel, file_path=mp3_path)
            db.session.add(new_song)
            db.session.commit()
        else:
            existing_song.file_path = mp3_path
            db.session.commit()

        return send_file(
            mp3_path,
            as_attachment=True,
            download_name=f"{clean_title}.mp3",
            mimetype='audio/mpeg'
        )

    except Exception as e:
        return f"Download failed: {str(e)}", 500

@app.route('/api/library', methods=['GET'])
def get_library():
    songs = Song.query.all()
    results = []
    for song in songs:
        if os.path.exists(song.file_path):
            results.append({
                'id': song.video_id,
                'title': song.title,
                'channel': song.channel
            })
    return jsonify({'results': results})

@app.route('/api/stream', methods=['GET'])
def stream_audio():
    video_id = request.args.get('id')
    song = Song.query.filter_by(video_id=video_id).first()
    
    if song and os.path.exists(song.file_path):
        return send_file(song.file_path, mimetype='audio/mpeg')
        
    return "Song not found", 404

if __name__ == '__main__':
    app.run(debug=True, port=5000)