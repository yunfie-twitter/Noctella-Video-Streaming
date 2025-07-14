import os
import sys
import uuid
import json
import shutil
import sqlite3
import subprocess
import re
import hashlib
import time
import threading
import yt_dlp
from functools import lru_cache
from datetime import datetime
from flask import Flask, request, render_template, send_from_directory, redirect, abort, jsonify
from urllib.parse import urlparse, parse_qs, unquote, urlencode

app = Flask(__name__)

HLS_DIR = 'hls_temp'
DATABASE_PATH = 'video_cache.db'

os.makedirs(HLS_DIR, exist_ok=True)

if sys.platform.startswith('win'):
    ytdlp_cmd = 'yt-dlp.exe'
elif sys.platform.startswith('linux'):
    ytdlp_cmd = 'yt-dlp'
else:
    ytdlp_cmd = 'yt-dlp'

# ユーザーIPアドレス取得関数
def get_client_ip():
    """クライアントの実際のIPアドレスを取得"""
    # プロキシ経由の場合のヘッダーをチェック
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    elif request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    elif request.headers.get('CF-Connecting-IP'):  # Cloudflare
        return request.headers.get('CF-Connecting-IP')
    else:
        return request.remote_addr


# 検索関連の関数を追加
def search_videos(query, max_results=20):
    """YouTube動画検索"""
    if not query or not query.strip():
        return []
    
    try:
        options = {
            'quiet': True,
            'extract_flat': True,
            'skip_download': True,
            'forcejson': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(options) as ydl:
            # ytsearchN: で検索件数を指定
            search_query = f"ytsearch{max_results}:{query.strip()}"
            result = ydl.extract_info(search_query, download=False)
            
            videos = []
            if result and 'entries' in result:
                for entry in result['entries']:
                    if entry:  # Noneチェック
                        video_info = {
                            'id': entry.get('id', ''),
                            'title': entry.get('title', 'タイトル不明'),
                            'url': entry.get('url', ''),
                            'thumbnail': entry.get('thumbnail', ''),
                            'duration': entry.get('duration', 0),
                            'view_count': entry.get('view_count', 0),
                            'uploader': entry.get('uploader', '不明'),
                            'upload_date': entry.get('upload_date', ''),
                            'description': entry.get('description', '')[:200] + '...' if entry.get('description') else ''
                        }
                        videos.append(video_info)
            
            return videos
            
    except Exception as e:
        print(f"検索エラー: {e}")
        return []

def save_search_history(query, results_count):
    """検索履歴をデータベースに保存"""
    if not query.strip():
        return
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # search_historyテーブルが存在しない場合は作成
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            results_count INTEGER DEFAULT 0,
            ip_address TEXT,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    ip_address = get_client_ip()
    user_agent = request.headers.get('User-Agent', '')[:200]
    
    cursor.execute('''
        INSERT INTO search_history (query, results_count, ip_address, user_agent)
        VALUES (?, ?, ?, ?)
    ''', (query.strip(), results_count, ip_address, user_agent))
    
    conn.commit()
    conn.close()

def get_popular_searches(limit=10):
    """人気検索キーワードを取得"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT query, COUNT(*) as search_count, MAX(created_at) as last_search
            FROM search_history 
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY LOWER(query)
            ORDER BY search_count DESC, last_search DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        return [{'query': row[0], 'count': row[1]} for row in rows]
        
    except sqlite3.OperationalError:
        # テーブルが存在しない場合
        return []
    finally:
        conn.close()

# APIエンドポイントを追加
@app.route('/api/search')
def api_search():
    """動画検索API"""
    query = request.args.get('q', '').strip()
    max_results = min(int(request.args.get('limit', 20)), 50)  # 最大50件
    
    if not query:
        return json.dumps({
            'success': False,
            'error': '検索キーワードが必要です'
        }, ensure_ascii=False), 400
    
    try:
        # 検索実行
        videos = search_videos(query, max_results)
        
        # 検索履歴に保存
        save_search_history(query, len(videos))
        
        return json.dumps({
            'success': True,
            'query': query,
            'results': videos,
            'count': len(videos)
        }, ensure_ascii=False, default=str)
        
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': f'検索に失敗しました: {str(e)}'
        }, ensure_ascii=False), 500

@app.route('/api/search/suggestions')
def api_search_suggestions():
    """検索候補API"""
    try:
        popular = get_popular_searches(10)
        return json.dumps({
            'success': True,
            'suggestions': popular
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False), 500

@app.route('/search')
def search_page():
    """検索ページ"""
    query = request.args.get('q', '')
    return render_template('search.html', query=query)

# セッションIDの生成と検証
def generate_session_id():
    """一意なセッションIDを生成"""
    timestamp = str(int(time.time()))
    random_str = str(uuid.uuid4())
    raw_id = f"{timestamp}-{random_str}"
    return hashlib.sha256(raw_id.encode()).hexdigest()[:32]

def validate_session_id(session_id):
    """セッションIDの形式を検証"""
    if not session_id or len(session_id) != 32:
        return False
    try:
        int(session_id, 16)  # 16進数かチェック
        return True
    except ValueError:
        return False

# データベース初期化（拡張版）
def init_database():
    """データベースが存在しない場合は作成し、テーブルを初期化"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # videosテーブルを作成
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            original_url TEXT,
            cache_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # commentsテーブルを作成（拡張版）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            session_id TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (video_id) REFERENCES videos (video_id)
        )
    ''')
    
    # 既存テーブルにカラムを追加（マイグレーション）
    try:
        cursor.execute('ALTER TABLE comments ADD COLUMN session_id TEXT')
    except sqlite3.OperationalError:
        pass  # カラムが既に存在する場合
    
    try:
        cursor.execute('ALTER TABLE comments ADD COLUMN ip_address TEXT')
    except sqlite3.OperationalError:
        pass
    
    try:
        cursor.execute('ALTER TABLE comments ADD COLUMN user_agent TEXT')
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    conn.close()

# YouTube動画IDを抽出する関数
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

# データベース操作関数
def get_video_info(video_id):
    """動画情報を取得"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT video_id, title, description, original_url, cache_path, created_at
        FROM videos WHERE video_id = ?
    ''', (video_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'video_id': row[0],
            'title': row[1],
            'description': row[2],
            'original_url': row[3],
            'cache_path': row[4],
            'created_at': row[5]
        }
    return None

def save_video_info(video_id, title, description, original_url, cache_path):
    """動画情報を保存または更新"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO videos 
        (video_id, title, description, original_url, cache_path, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (video_id, title, description, original_url, cache_path))
    
    conn.commit()
    conn.close()

def get_all_videos():
    """すべての動画情報を取得"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT video_id, title, description, created_at
        FROM videos ORDER BY created_at DESC
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            'video_id': row[0],
            'title': row[1],
            'description': row[2],
            'created_at': row[3]
        }
        for row in rows
    ]

# GPUエンコーダー確認関数
def check_encoder(name):
    try:
        result = subprocess.run(['ffmpeg', '-encoders'], capture_output=True, text=True, timeout=5)
        return name in result.stdout
    except Exception:
        return False

def select_video_codec():
    if check_encoder('h264_nvenc'):
        print("NVIDIA GPUエンコードを使用します")
        return 'h264_nvenc', ['-preset', 'fast']
    elif check_encoder('h264_amf'):
        print("AMD GPUエンコードを使用します")
        return 'h264_amf', []
    elif check_encoder('h264_qsv'):
        print("Intel QuickSync GPUエンコードを使用します")
        return 'h264_qsv', []
    else:
        print("GPUエンコーダが見つからないためCPUエンコード(libx264)を使用します")
        return 'libx264', ['-preset', 'veryfast']

# Googleリダイレクトから元URLを抽出
def extract_real_url(possibly_wrapped_url: str):
    parsed = urlparse(possibly_wrapped_url)
    if parsed.netloc.endswith("google.com") and parsed.path.startswith("/url"):
        query = parse_qs(parsed.query)
        real_url = query.get("q", [None])[0]
        if real_url:
            return unquote(real_url)
    return possibly_wrapped_url

def process_video(video_id, original_url):
    """動画を処理してキャッシュ"""
    cache_dir = os.path.join(HLS_DIR, video_id)
    os.makedirs(cache_dir, exist_ok=True)
    
    # m3u8_extractor.pyを使用してストリーミングURLを取得
    extractor = subprocess.run(
        ['python3', 'm3u8_extractor.py', original_url, '--json'],
        capture_output=True, text=True
    )
    
    print("===== m3u8_extractor stdout =====")
    print(extractor.stdout)
    print("==================================")
    
    try:
        data = json.loads(extractor.stdout)
        info = data.get(original_url)
        video_url = info.get("video")
        audio_url = info.get("audio")
    except Exception as e:
        print("JSONパースエラー:", e)
        return None, None, None
    
    if not video_url or not audio_url:
        return None, None, None
    
    # yt-dlpでタイトルと説明取得
    title_proc = subprocess.run([ytdlp_cmd, '--get-title', original_url], capture_output=True, text=True)
    title = title_proc.stdout.strip() or "タイトル不明"
    
    desc_proc = subprocess.run([ytdlp_cmd, '--get-description', original_url], capture_output=True, text=True)
    description = desc_proc.stdout.strip() or "概要なし"
    
    # GPUエンコーダー選択
    try:
        video_codec, preset_opts = select_video_codec()
    except RuntimeError as e:
        print(f"エンコーダー選択エラー: {e}")
        return None, None, None
    
    # ffmpegでHLS変換
    master_path = os.path.join(cache_dir, 'master.m3u8')
    ffmpeg_cmd = [
        'ffmpeg', '-y',
        '-i', video_url,
        '-i', audio_url,
        '-c:v', video_codec,
        *preset_opts,
        '-c:a', 'aac',
        '-b:a', '128k',
        '-f', 'hls',
        '-hls_time', '4',
        '-loglevel', 'info',
        '-hls_playlist_type', 'event',
        '-hls_segment_filename', os.path.join(cache_dir, 'segment_%03d.ts'),
        master_path
    ]
    
    subprocess.Popen(ffmpeg_cmd)
    
    # データベースに保存
    save_video_info(video_id, title, description, original_url, cache_dir)
    
    return title, description, cache_dir

# コメント関連の関数（拡張版）
def get_comments(video_id):
    """動画のコメントを取得"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, author, content, session_id, ip_address, created_at
        FROM comments 
        WHERE video_id = ? 
        ORDER BY created_at DESC
    ''', (video_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            'id': row[0],
            'author': row[1],
            'content': row[2],
            'session_id': row[3],
            'ip_address': row[4],
            'created_at': row[5]
        }
        for row in rows
    ]

def add_comment(video_id, author, content, session_id):
    """コメントを追加"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    ip_address = get_client_ip()
    user_agent = request.headers.get('User-Agent', '')[:200]  # 200文字まで
    
    cursor.execute('''
        INSERT INTO comments (video_id, author, content, session_id, ip_address, user_agent)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (video_id, author, content, session_id, ip_address, user_agent))
    
    comment_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return comment_id

def can_delete_comment(comment_id, session_id):
    """コメント削除権限を確認"""
    if not validate_session_id(session_id):
        return False
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT session_id, ip_address
        FROM comments 
        WHERE id = ?
    ''', (comment_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return False
    
    stored_session_id, stored_ip = row
    current_ip = get_client_ip()
    
    # セッションIDが一致 AND IPアドレスが一致
    return (stored_session_id == session_id and stored_ip == current_ip)

def delete_comment(comment_id, session_id):
    """コメントを削除（権限チェック付き）"""
    if not can_delete_comment(comment_id, session_id):
        return False
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
    affected_rows = cursor.rowcount
    conn.commit()
    conn.close()
    
    return affected_rows > 0

# ルーティング
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/history')
def history():
    videos = get_all_videos()
    return render_template('history.html', videos=videos)

@app.route('/watch')
def watch():
    video_id = request.args.get('v')
    if not video_id:
        return "動画IDが必要です", 400
    
    # データベースから動画情報を取得
    video_info = get_video_info(video_id)
    
    if video_info and video_info['cache_path']:
        # キャッシュ済みの場合
        master_path = os.path.join(video_info['cache_path'], 'master.m3u8')
        if os.path.exists(master_path):
            stream_url = f'/hls/{video_id}/master.m3u8'
            return render_template('player.html', 
                                   stream_url=stream_url,
                                   title=video_info['title'],
                                   description=video_info['description'])
        else:
            # キャッシュが削除されている場合、waiting.htmlを表示
            return render_template('waiting.html', video_id=video_id)
    
    # 新しい動画の場合、URLから処理
    url = request.args.get('url')
    if not url:
        # URLが指定されていない場合、YouTube URLを構築
        url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Google経由URLなら本来のURLを取得
    url = extract_real_url(url)
    
    # URLから動画IDを抽出して検証
    extracted_id = extract_youtube_video_id(url)
    if extracted_id != video_id:
        return "動画IDとURLが一致しません", 400
    
    # 動画を処理
    title, description, cache_dir = process_video(video_id, url)
    
    if not title:
        return "動画の処理に失敗しました", 500
    
    # waiting.htmlを表示
    return render_template('waiting.html', video_id=video_id)

@app.route('/player')
def player():
    """従来のplayer エンドポイント（互換性のため残す）"""
    url = request.args.get('url')
    if not url:
        return "URLが必要です", 400
    
    # YouTube動画IDを抽出
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return "有効なYouTube URLではありません", 400
    
    # 新しいwatch エンドポイントにリダイレクト
    return redirect(f'/watch?v={video_id}&url={url}')

@app.route('/sm-watch')
def mobile_watch():
    video_id = request.args.get('v')
    if not video_id:
        return "動画IDが必要です", 400
    
    video_info = get_video_info(video_id)
    if not video_info:
        return "動画が見つかりません", 404
    
    master_path = os.path.join(video_info['cache_path'], 'master.m3u8')
    if not os.path.exists(master_path):
        return render_template('waiting.html', video_id=video_id)
    
    stream_url = f'/hls/{video_id}/master.m3u8'
    return render_template('smartphone_player.html', 
                           stream_url=stream_url,
                           title=video_info['title'],
                           description=video_info['description'])

@app.route('/hls/<video_id>/<filename>')
def hls(video_id, filename):
    video_info = get_video_info(video_id)
    if not video_info:
        abort(404)
    
    hls_path = video_info['cache_path']
    file_path = os.path.join(hls_path, filename)
    
    if not os.path.exists(file_path):
        abort(404)
    
    return send_from_directory(hls_path, filename)

@app.route('/cleanup')
def cleanup():
    # データベースの内容もクリア
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM videos')
    cursor.execute('DELETE FROM comments')
    conn.commit()
    conn.close()
    
    # HLSディレクトリをクリア
    if os.path.exists(HLS_DIR):
        shutil.rmtree(HLS_DIR)
    os.makedirs(HLS_DIR, exist_ok=True)
    
    return "データベースとHLSディレクトリを初期化しました"

@app.route('/api/videos')
def api_videos():
    """動画一覧をJSON形式で返す"""
    videos = get_all_videos()
    return json.dumps(videos, ensure_ascii=False, indent=2)

# セッション生成API
@app.route('/api/session', methods=['POST'])
def api_create_session():
    """新しいセッションIDを生成"""
    try:
        session_id = generate_session_id()
        return json.dumps({
            'success': True,
            'session_id': session_id
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False), 500

# コメント関連API（拡張版）
@app.route('/api/comments/<video_id>')
def api_get_comments(video_id):
    """コメント一覧を取得"""
    try:
        comments = get_comments(video_id)
        current_session_id = request.headers.get('X-Session-ID', '')
        current_ip = get_client_ip()
        
        # 各コメントに削除可能フラグを追加
        for comment in comments:
            comment['can_delete'] = (
                comment['session_id'] == current_session_id and 
                comment['ip_address'] == current_ip
            )
            # セキュリティのためIPアドレスは返さない
            del comment['ip_address']
            del comment['session_id']
        
        return json.dumps({
            'success': True,
            'comments': comments
        }, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False), 500

@app.route('/api/comments', methods=['POST'])
def api_add_comment():
    """コメントを投稿"""
    try:
        data = request.get_json()
        if not data:
            return json.dumps({
                'success': False,
                'error': 'JSONデータが必要です'
            }, ensure_ascii=False), 400
        
        video_id = data.get('video_id')
        author = data.get('author', '匿名').strip()
        content = data.get('content', '').strip()
        session_id = data.get('session_id', '').strip()
        
        if not video_id or not content:
            return json.dumps({
                'success': False,
                'error': '動画IDとコメント内容は必須です'
            }, ensure_ascii=False), 400
        
        if not validate_session_id(session_id):
            return json.dumps({
                'success': False,
                'error': '無効なセッションIDです'
            }, ensure_ascii=False), 400
        
        if len(author) > 50:
            return json.dumps({
                'success': False,
                'error': 'ユーザー名は50文字以内で入力してください'
            }, ensure_ascii=False), 400
        
        if len(content) > 500:
            return json.dumps({
                'success': False,
                'error': 'コメントは500文字以内で入力してください'
            }, ensure_ascii=False), 400
        
        comment_id = add_comment(video_id, author, content, session_id)
        
        return json.dumps({
            'success': True,
            'comment_id': comment_id,
            'message': 'コメントを投稿しました'
        }, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False), 500

@app.route('/api/comments/<int:comment_id>', methods=['DELETE'])
def api_delete_comment(comment_id):
    """コメントを削除（権限チェック付き）"""
    try:
        session_id = request.headers.get('X-Session-ID', '')
        
        if not validate_session_id(session_id):
            return json.dumps({
                'success': False,
                'error': '無効なセッションIDです'
            }, ensure_ascii=False), 401
        
        success = delete_comment(comment_id, session_id)
        
        if success:
            return json.dumps({
                'success': True,
                'message': 'コメントを削除しました'
            }, ensure_ascii=False)
        else:
            return json.dumps({
                'success': False,
                'error': 'コメントを削除する権限がありません'
            }, ensure_ascii=False), 403
            
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': str(e)
        }, ensure_ascii=False), 500

@app.route('/api/related')
def api_related():
    video_id = request.args.get('v', '').strip()
    title = request.args.get('title', '').strip()
    if not title:
        return jsonify({'success': False, 'error': 'タイトルが必要です'}), 400

    try:
        # 検索キーワードはタイトル＋"関連"などで調整
        query = f"{title} 関連"
        options = {
            'quiet': True,
            'extract_flat': True,
            'skip_download': True,
            'forcejson': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.extract_info(f"ytsearch10:{query}", download=False)
            videos = []
            for entry in result['entries']:
                # 今見ている動画は除外
                if entry.get('id') == video_id:
                    continue
                videos.append({
                    'id': entry.get('id', ''),
                    'title': entry.get('title', ''),
                    'thumbnail': f"https://img.youtube.com/vi/{entry.get('id','')}/maxresdefault.jpg",
                    'uploader': entry.get('uploader', ''),
                    'duration': entry.get('duration', 0)
                })
            return jsonify({'success': True, 'videos': videos})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# アプリケーション起動時にデータベースを初期化
if __name__ == '__main__':
    init_database()
    app.run(debug=True, threaded=True)
