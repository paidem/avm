import os
import subprocess
import time
import datetime
import tzlocal 
import hashlib
import secrets
import uuid
import json
import re
import mimetypes

from functools import wraps
from flask import Flask, render_template, jsonify, request, abort, send_file, Response, session, redirect, url_for, \
    make_response, stream_with_context

app = Flask(__name__, static_folder='static')
# Set a secret key for session management
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Get base_dir from environment variable or use default
base_dir = os.environ.get('MEDIA_BASE_DIR', '/media')
thumbnails_base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static/thumbnails')

# Admin password from environment variable
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')

# Session expiry - 30 days (in seconds)
SESSION_EXPIRY = 60 * 60 * 24 * 30

# Files to filter out (hidden files and system files) from env var
default_filtered = ['.DS_Store', '.Thumbs.db', 'Thumbs.db', '._.Trashes', '.Spotlight-V100',
                    '.fseventsd', '.Trashes', '@eaDir', 'desktop.ini', 'thumbs.db']
filtered_from_env = os.environ.get('FILTERED_FILES', '')
if filtered_from_env:
    FILTERED_FILES = filtered_from_env.split(',')
else:
    FILTERED_FILES = default_filtered

# File extensions to hide from env var
hidden_from_env = os.environ.get('HIDDEN_EXTENSIONS', '')
if hidden_from_env:
    HIDDEN_EXTENSIONS = hidden_from_env.split(',')
else:
    HIDDEN_EXTENSIONS = ['source','srt']

# Ensure thumbnails directory exists
os.makedirs(thumbnails_base_dir, exist_ok=True)


redis_host = os.environ.get('REDIS_HOST', '')
if redis_host:
    from modules.session.session_redis import setup_login_routes, check_auth
else:
    # If Redis is not enabled, use memory-based sessions
    from modules.session.session_memory import setup_login_routes, check_auth

setup_login_routes(app,ADMIN_PASSWORD)

def auth_required(f):
    """Decorator to require authentication for a route"""
    @wraps(f)  # Import wraps from functools
    def decorated(*args, **kwargs):
        if not check_auth():
            return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def root():
    # Redirect to browse
    return redirect(url_for('browse'))

def get_video_metadata(file_path):
    """Get video metadata like duration, codec and framerate using ffprobe"""
    try:
        # Use ffprobe to get video information
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format', '-show_streams',
            file_path
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(result.stdout)

        metadata = {
            'duration': None,
            'codec': None,
            'framerate': None,
            'creation_time': None
        }

        # Find video stream
        video_stream = None
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video':
                video_stream = stream
                break

        if video_stream:
            # Get duration
            if 'duration' in video_stream:
                duration_secs = float(video_stream['duration'])
                minutes = int(duration_secs // 60)
                seconds = int(duration_secs % 60)
                metadata['duration'] = f"{minutes}:{seconds:02d}"
            elif 'duration' in data.get('format', {}):
                duration_secs = float(data['format']['duration'])
                minutes = int(duration_secs // 60)
                seconds = int(duration_secs % 60)
                metadata['duration'] = f"{minutes}:{seconds:02d}"

            # Get codec
            if 'codec_name' in video_stream:
                metadata['codec'] = video_stream['codec_name']

            # Get framerate
            if 'avg_frame_rate' in video_stream:
                framerate = video_stream['avg_frame_rate']
                if framerate and framerate != '0/0':
                    try:
                        num, den = map(int, framerate.split('/'))
                        if den != 0:  # Avoid division by zero
                            fps = round(num / den, 2)
                            metadata['framerate'] = f"{fps} fps"
                    except (ValueError, ZeroDivisionError):
                        pass
            
            if 'tags' in video_stream:
                if 'creation_time' in video_stream['tags']:
                    date_str = video_stream['tags']['creation_time']
                    dt_utc = datetime.datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    local_tz = tzlocal.get_localzone()
                    dt_local = dt_utc.astimezone(local_tz)
                    tz_offset_hours = dt_local.utcoffset().total_seconds() / 3600
                    tz_sign = '+' if tz_offset_hours >= 0 else '-'
                    tz_hours = abs(int(tz_offset_hours))
                    formatted_date = dt_local.strftime("%Y-%m-%d %H:%M") + f" (GMT{tz_sign}{tz_hours})"
                    metadata['creation_time'] = formatted_date

        return metadata

    except Exception as e:
        print(f"Error getting video metadata: {e}")
        return {
            'duration': None,
            'codec': None,
            'framerate': None,
            'creation_time': None
        }


def get_file_info(full_path):
    """Get size and modification date of a file"""
    stats = os.stat(full_path)
    size = stats.st_size

    # Format size for display
    if size < 1024:
        size_str = f"{size} bytes"
    elif size < 1024 * 1024:
        size_str = f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        size_str = f"{size / (1024 * 1024):.1f} MB"
    else:
        size_str = f"{size / (1024 * 1024 * 1024):.1f} GB"

    modified = datetime.datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')

    return {
        'size': size_str,
        'modified': modified,
        'raw_size': size
    }


def get_source_info(file_path):
    """Get source info for merged files"""
    source_path = file_path + '.source'
    if os.path.exists(source_path):
        try:
            with open(source_path, 'r') as f:
                sources = f.read().splitlines()
            return sources
        except Exception as e:
            print(f"Error reading source file: {e}")
    return None


def get_thumbnail_path(file_path, abs_path, is_video=True):
    """Generate a thumbnail path for a video or image file"""
    file_hash = hashlib.md5(file_path.encode()).hexdigest()
    thumbnail_dir = os.path.join(thumbnails_base_dir, file_hash[:2])
    os.makedirs(thumbnail_dir, exist_ok=True)
    thumbnail_path = os.path.join(thumbnail_dir, f"{file_hash}.jpg")

    # Generate thumbnail if it doesn't exist
    if not os.path.exists(thumbnail_path):
        try:
            if is_video:
                cmd = [
                    'ffmpeg', '-i', abs_path,
                    '-ss', '00:00:00.000', '-vframes', '1',
                    '-vf', 'scale=200:-1',
                    thumbnail_path
                ]
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            elif is_video is False:  # For images
                cmd = [
                    'ffmpeg', '-i', abs_path,
                    '-vf', 'scale=200:-1',
                    thumbnail_path
                ]
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Audio has no thumbnail generation - will use default icon
        except Exception as e:
            print(f"Error generating thumbnail: {e}")
            return None

    return f"/static/thumbnails/{file_hash[:2]}/{file_hash}.jpg"


@app.route('/browse/')
@app.route('/browse/<path:subpath>')
def browse(subpath=''):
    full_path = os.path.join(base_dir, subpath)

    # Security check
    if not os.path.realpath(full_path).startswith(os.path.realpath(base_dir)):
        abort(403)

    # Check if user is authenticated
    is_authenticated = check_auth()

    items = []
    try:
        for name in os.listdir(full_path):
            # Skip filtered files
            if name in FILTERED_FILES or name.startswith('.'):
                continue

            # Check extension filtering
            ext = os.path.splitext(name)[1].lower()
            if ext and ext[1:] in HIDDEN_EXTENSIONS:  # Skip the dot in extension
                continue

            item_path = os.path.join(subpath, name) if subpath else name
            abs_path = os.path.join(full_path, name)
            is_dir = os.path.isdir(abs_path)

            item = {
                'name': name,
                'path': item_path,
                'is_dir': is_dir,
                'thumbnail': None,
                'is_video': False,
                'is_image': False,
                'is_audio': False,
                'source_files': None,
                'video_metadata': {
                    'duration': None,
                    'codec': None,
                    'framerate': None,
                    'creation_time': None
                }
            }

            # Get file info if it's not a directory
            if not is_dir:
                item.update(get_file_info(abs_path))

                # Check file type
                extension = os.path.splitext(name)[1].lower()
                if extension in ['.mp4', '.mov', '.avi', '.mkv', '.webm']:
                    item['is_video'] = True
                    item['thumbnail'] = get_thumbnail_path(item_path, abs_path, is_video=True)
                    item['video_metadata'] = get_video_metadata(abs_path)

                    # Check if it's a merged file
                    if 'joined' in name or 'merged' in name:
                        item['source_files'] = get_source_info(abs_path)

                elif extension in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']:
                    item['is_image'] = True
                    item['thumbnail'] = get_thumbnail_path(item_path, abs_path, is_video=False)
                elif extension in ['.mp3', '.wav', '.ogg', '.aac', '.flac']:
                    item['is_audio'] = True
                    # Audio files don't get thumbnails but use a special icon

            items.append(item)
    except PermissionError:
        return render_template('error.html', message="Permission denied: Cannot access directory"), 403
    except FileNotFoundError:
        return render_template('error.html', message="Directory not found"), 404
    except Exception as e:
        return render_template('error.html', message=f"Error: {str(e)}"), 500

    # Sort: directories first, then by name
    items.sort(key=lambda item: (not item['is_dir'], item['name'].lower()))

    # Prepare breadcrumbs
    breadcrumbs = []
    current = ''
    parts = subpath.split('/') if subpath else []

    breadcrumbs.append({'name': 'Home', 'path': ''})
    for part in parts:
        if part:
            current = os.path.join(current, part) if current else part
            breadcrumbs.append({'name': part, 'path': current})

    return render_template('browser.html',
                           items=items,
                           current_path=subpath,
                           breadcrumbs=breadcrumbs,
                           is_authenticated=is_authenticated)


@app.route('/download/<path:file_path>')
def download_file(file_path):
    full_path = os.path.join(base_dir, file_path)

    # Security check
    if not os.path.realpath(full_path).startswith(os.path.realpath(base_dir)):
        abort(403)

    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        abort(404)

    return send_file(full_path, as_attachment=True)


@app.route('/view/<path:file_path>')
def view_file(file_path):
    full_path = os.path.join(base_dir, file_path)

    # Security check
    if not os.path.realpath(full_path).startswith(os.path.realpath(base_dir)):
        abort(403)

    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        abort(404)

    # Get file size
    file_size = os.path.getsize(full_path)
    
    # Handle range request
    range_header = request.headers.get('Range', None)
    
    # Default to sending the full file
    byte_start = 0
    byte_end = file_size - 1
    status_code = 200
    
    # Parse range header if it exists
    if range_header:
        match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if match:
            groups = match.groups()
            byte_start = int(groups[0])
            byte_end = int(groups[1]) if groups[1] else file_size - 1
            
            if byte_start > file_size or byte_end >= file_size or byte_start > byte_end:
                return Response(status=416)  # Range Not Satisfiable
                
            status_code = 206  # Partial Content
    
    # Calculate content length
    content_length = byte_end - byte_start + 1
    
    # Determine content type
    mime_type, _ = mimetypes.guess_type(full_path)
    if not mime_type:
        mime_type = 'application/octet-stream'
    
    # Create the response headers
    headers = {
        'Content-Type': mime_type,
        'Accept-Ranges': 'bytes',
        'Content-Length': content_length,
    }
    
    # Add Content-Range header for partial content responses
    if status_code == 206:
        headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
    
    def generate():
        with open(full_path, 'rb') as f:
            f.seek(byte_start)
            remaining = content_length
            chunk_size = min(1024 * 1024, remaining)  # 1MB chunks or smaller if file is smaller
            
            while remaining > 0:
                chunk_size = min(chunk_size, remaining)
                data = f.read(chunk_size)
                if not data:
                    break
                remaining -= len(data)
                yield data
    
    return Response(
        generate(),
        status=status_code,
        headers=headers
    )

@app.route('/api/execute', methods=['POST'])
@auth_required
def execute_command():
    data = request.json
    try:
        # Validate all file paths
        full_paths = []
        for rel_path in data['files']:
            abs_path = os.path.abspath(os.path.join(base_dir, rel_path))

            # Security check
            if not abs_path.startswith(base_dir):
                return jsonify({'status': 'error', 'message': 'Invalid path'}), 403

            full_paths.append(abs_path)

        if data['command'] == 'merge':
            if not full_paths:
                return jsonify({'status': 'error', 'message': 'No files selected'}), 400

            # Get first file's directory and name components
            first_file = full_paths[0]
            output_dir = os.path.dirname(first_file)
            original_name = os.path.basename(first_file)
            base_name, extension = os.path.splitext(original_name)

            # Create new filename pattern: FILE1_3_joined.MP4
            new_name = f"{base_name}_{len(full_paths)}_joined{extension}"
            output_file = os.path.join(output_dir, new_name)

            # Ensure unique filename
            counter = 1
            while os.path.exists(output_file):
                new_name = f"{base_name}_{len(full_paths)}_joined_{counter}{extension}"
                output_file = os.path.join(output_dir, new_name)
                counter += 1

            # Build merge command (modify with your actual merge command)
            cmd = ['merge_mp4', '-o', output_file] + full_paths

            # Create source tracking file
            source_file = output_file + '.source'
            with open(source_file, 'w') as f:
                for path in full_paths:
                    f.write(os.path.basename(path) + '\n')

            subprocess.Popen(cmd)
            return jsonify({
                'status': 'success',
                'message': f'Merge started. Output will be: {os.path.basename(output_file)}'
            })

        elif data['command'] == 'delete':
            for path in full_paths:
                if os.path.isfile(path):
                    # Check if .source file exists and delete it too
                    source_file = path + '.source'
                    if os.path.exists(source_file):
                        os.remove(source_file)
                    # Delete the actual file
                    os.remove(path)
                elif os.path.isdir(path):
                    os.rmdir(path)
            return jsonify({'status': 'success', 'message': 'Files deleted'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)