import os
import shutil
from flask import Flask, request, render_template, send_from_directory, jsonify, abort
from yt_dlp import YoutubeDL
import yt_dlp
# モジュールインポート
from config import HLS_DIR, DATABASE_PATH
from database import db
from comment_system import comment_system
from search_system import search_system
from job_manager import job_manager
from utils import (
    get_client_ip, extract_real_url, extract_youtube_video_id,
    generate_session_id, validate_session_id
)

# Flask アプリケーション初期化
app = Flask(__name__)
app.config['SECRET_KEY'] = 'noctella-secret-key'

# HLS ディレクトリ作成
os.makedirs(HLS_DIR, exist_ok=True)

# ==============================
# ルーティング - メインページ
# ==============================

@app.route('/')
def index():
    """メインページ"""
    return render_template('index.html')

@app.route('/history')
def history():
    """動画履歴ページ"""
    videos = db.get_all_videos()
    return render_template('history.html', videos=videos)

@app.route('/search')
def search_page():
    """検索ページ"""
    query = request.args.get('q', '')
    return render_template('search.html', query=query)

# ==============================
# ルーティング - 動画再生
# ==============================

@app.route('/watch')
def watch():
    """動画再生ページ"""
    video_id = request.args.get('v')
    if not video_id:
        return "動画IDが必要です", 400
    
    # キャッシュ済みかチェック
    video_info = db.get_video_info(video_id)
    if video_info and video_info['cache_path']:
        master_path = os.path.join(video_info['cache_path'], 'master.m3u8')
        if os.path.exists(master_path):
            stream_url = f'/hls/{video_id}/master.m3u8'
            return render_template('player.html',
                                 stream_url=stream_url,
                                 title=video_info['title'],
                                 description=video_info['description'])
    
    # 新しい動画の場合、ジョブを投入
    url = request.args.get('url') or f"https://www.youtube.com/watch?v={video_id}"
    url = extract_real_url(url)
    
    extracted_id = extract_youtube_video_id(url)
    if extracted_id != video_id:
        return "動画IDとURLが一致しません", 400
    
    job_id = job_manager.enqueue_job(video_id, url)
    return render_template('waiting.html', job_id=job_id)

@app.route('/hls/<video_id>/<path:filename>')
def hls(video_id, filename):
    """HLS配信"""
    video_info = db.get_video_info(video_id)
    if not video_info:
        abort(404)
    
    hls_path = video_info['cache_path']
    file_path = os.path.join(hls_path, filename)
    
    if not os.path.exists(file_path):
        abort(404)
    
    return send_from_directory(hls_path, filename)

@app.route('/admin')
def admin_dashboard():
    # 必要なら認証: 例→ session やIPホワイトリスト後に
    return render_template("admin.html")

# ==============================
# API - ジョブ管理
# ==============================

@app.route('/api/job/<int:job_id>')
def api_job_status(job_id):
    """ジョブステータス取得"""
    status = job_manager.get_job_status(job_id)
    if not status:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    
    return jsonify({
        'success': True,
        'job_id': job_id,
        'status': status['status'],
        'progress': status['progress']
    })

# ==============================
# API - 検索機能
# ==============================

@app.route('/api/search')
def api_search():
    """動画検索API"""
    query = request.args.get('q', '').strip()
    max_results = min(int(request.args.get('limit', 20)), 50)
    
    if not query:
        return jsonify({
            'success': False,
            'error': '検索キーワードが必要です'
        }), 400
    
    try:
        videos = search_system.search_videos(query, max_results)
        search_system.save_search_history(query, len(videos))
        
        return jsonify({
            'success': True,
            'query': query,
            'results': videos,
            'count': len(videos)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'検索に失敗しました: {str(e)}'
        }), 500

@app.route('/api/search/suggestions')
def api_search_suggestions():
    """検索候補API"""
    try:
        popular = search_system.get_popular_searches(5)
        return jsonify({
            'success': True,
            'suggestions': popular
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


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


# ==============================
# API - セッション管理
# ==============================

@app.route('/api/session', methods=['POST'])
def api_create_session():
    """セッション作成API"""
    try:
        session_id = generate_session_id()
        return jsonify({
            'success': True,
            'session_id': session_id
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ==============================
# API - コメント機能
# ==============================

@app.route('/api/comments/<video_id>')
def api_get_comments(video_id):
    """コメント取得API"""
    try:
        comments = comment_system.get_comments(video_id)
        current_session_id = request.headers.get('X-Session-ID', '')
        current_ip = get_client_ip()
        
        # 削除権限チェック
        for comment in comments:
            comment['can_delete'] = (
                comment['session_id'] == current_session_id and 
                comment['ip_address'] == current_ip
            )
            # セキュリティのため削除
            del comment['ip_address']
            del comment['session_id']
        
        return jsonify({
            'success': True,
            'comments': comments
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/comments', methods=['POST'])
def api_add_comment():
    """コメント投稿API"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'JSONデータが必要です'
            }), 400
        
        video_id = data.get('video_id')
        author = data.get('author', '匿名').strip()
        content = data.get('content', '').strip()
        session_id = data.get('session_id', '').strip()
        
        if not video_id or not content:
            return jsonify({
                'success': False,
                'error': '動画IDとコメント内容は必須です'
            }), 400
        
        comment_id = comment_system.add_comment(video_id, author, content, session_id)
        
        return jsonify({
            'success': True,
            'comment_id': comment_id,
            'message': 'コメントを投稿しました'
        })
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/comments/<int:comment_id>', methods=['DELETE'])
def api_delete_comment(comment_id):
    """コメント削除API"""
    try:
        session_id = request.headers.get('X-Session-ID', '')
        
        if not validate_session_id(session_id):
            return jsonify({
                'success': False,
                'error': '無効なセッションIDです'
            }), 401
        
        success = comment_system.delete_comment(comment_id, session_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'コメントを削除しました'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'コメントを削除する権限がありません'
            }), 403
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ==============================
# API - その他
# ==============================

@app.route('/api/videos')
def api_videos():
    """動画一覧API"""
    videos = db.get_all_videos()
    return jsonify(videos)

@app.route('/cleanup')
def cleanup():
    """データベースとキャッシュの初期化"""
    # データベースクリーンアップ
    db.execute_query('DELETE FROM videos')
    db.execute_query('DELETE FROM comments')
    db.execute_query('DELETE FROM search_history')
    
    # HLSディレクトリクリーンアップ
    if os.path.exists(HLS_DIR):
        shutil.rmtree(HLS_DIR)
    os.makedirs(HLS_DIR, exist_ok=True)
    
    return "データベースとHLSディレクトリを初期化しました"



@app.route('/api/comments_count')
def api_comments_count():
    import sqlite3
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM comments")
    count = c.fetchone()[0]
    conn.close()
    return jsonify({"count": count})

@app.route('/api/admin/delete_video', methods=['POST'])
def api_admin_delete_video():
    video_id = request.json.get('video_id')
    if not video_id: return jsonify(False)
    import sqlite3, os, shutil
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM videos WHERE video_id=?", (video_id,))
    c.execute("DELETE FROM comments WHERE video_id=?", (video_id,))
    c.execute("DELETE FROM processing_jobs WHERE video_id=?", (video_id,))
    conn.commit()
    conn.close()
    # キャッシュディレクトリ削除
    cache_dir = os.path.join(HLS_DIR, video_id)
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)
    return jsonify(True)

@app.route('/api/admin/delete_job', methods=['POST'])
def api_admin_delete_job():
    job_id = request.json.get('job_id')
    import sqlite3
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM processing_jobs WHERE id=?", (job_id,))
    conn.commit()
    conn.close()
    # メモリDBの場合もcleanu-upが必要なら併用対応
    return jsonify(True)

# ==============================
# アプリケーション起動
# ==============================

if __name__ == '__main__':
    print("✔ Noctella Video Streaming サーバーを開始します...")
    print(f"✔ データベース: {DATABASE_PATH}")
    print(f"✔ HLSディレクトリ: {HLS_DIR}")
    print(f"✔ ワーカー数: {job_manager.max_workers}")
    app.run(debug=True, threaded=True, host='0.0.0.0', port=5000)
