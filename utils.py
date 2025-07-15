import re
import hashlib
import uuid
import time
from urllib.parse import urlparse, parse_qs, unquote
from flask import request

def get_client_ip():
    """クライアントのIPアドレスを取得"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    elif request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    elif request.headers.get('CF-Connecting-IP'):
        return request.headers.get('CF-Connecting-IP')
    else:
        return request.remote_addr

def extract_real_url(possibly_wrapped_url):
    """Googleリダイレクトから元URLを抽出"""
    parsed = urlparse(possibly_wrapped_url)
    if parsed.netloc.endswith("google.com") and parsed.path.startswith("/url"):
        query = parse_qs(parsed.query)
        real_url = query.get("q", [None])[0]
        if real_url:
            return unquote(real_url)
    return possibly_wrapped_url

def extract_youtube_video_id(url):
    """YouTube URLから動画IDを抽出"""
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([^&\n?#]+)',
        r'youtube\.com/v/([^&\n?#]+)',
        r'youtube\.com/watch\?.*v=([^&\n?#]+)',
        r'music\.youtube\.com/watch\?.*v=([^&\n?#]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def generate_session_id():
    """セッションID生成"""
    timestamp = str(int(time.time()))
    random_str = str(uuid.uuid4())
    raw_id = f"{timestamp}-{random_str}"
    return hashlib.sha256(raw_id.encode()).hexdigest()[:32]

def validate_session_id(session_id):
    """セッションIDの形式を検証"""
    if not session_id or len(session_id) != 32:
        return False
    try:
        int(session_id, 16)
        return True
    except ValueError:
        return False

def escape_html(text):
    """HTMLエスケープ"""
    import html
    return html.escape(text)
