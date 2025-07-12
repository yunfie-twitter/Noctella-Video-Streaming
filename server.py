import os
import uuid
import json
import shutil
import subprocess
from flask import Flask, request, render_template, send_from_directory, redirect, abort
from urllib.parse import urlparse, parse_qs, unquote, urlencode

app = Flask(__name__)
HLS_DIR = 'hls_temp'
os.makedirs(HLS_DIR, exist_ok=True)

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
        raise RuntimeError("GPUエンコードがサポートされていません")

# Googleリダイレクトから元URLを抽出
def extract_real_url(possibly_wrapped_url: str):
    parsed = urlparse(possibly_wrapped_url)
    if parsed.netloc.endswith("google.com") and parsed.path.startswith("/url"):
        query = parse_qs(parsed.query)
        real_url = query.get("q", [None])[0]
        if real_url:
            return unquote(real_url)
    return possibly_wrapped_url

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/history')
def history():
    return render_template('history.html')

@app.route('/share/<session_id>')
def share_page(session_id):
    output_dir = os.path.join(HLS_DIR, session_id)
    master_path = os.path.join(output_dir, 'master.m3u8')
    if not os.path.exists(master_path):
        return render_template('waiting.html', session_id=session_id)

    title = request.args.get('title', 'タイトルなし')
    description = request.args.get('description', '概要なし')
    stream_url = f'/hls/{session_id}/master.m3u8'
    return render_template('player.html', stream_url=stream_url, title=title, description=description)

@app.route('/player')
def player():
    url = request.args.get('url')
    if not url:
        return "URLが必要です", 400

    # Google経由URLなら本来のURLを取得
    url = extract_real_url(url)

    session_id = str(uuid.uuid4())
    output_dir = os.path.join(HLS_DIR, session_id)
    os.makedirs(output_dir, exist_ok=True)

    # m3u8_extractorで動画/音声URL取得
    extractor = subprocess.run(
        ['m3u8_extractor.exe', url, '--json'],
        capture_output=True, text=True
    )
    print("===== m3u8_extractor stdout =====")
    print(extractor.stdout)
    print("==================================")

    try:
        data = json.loads(extractor.stdout)
        info = data.get(url)
        video_url = info.get("video")
        audio_url = info.get("audio")
    except Exception as e:
        print("JSONパースエラー:", e)
        return "URL抽出に失敗しました", 500

    if not video_url or not audio_url:
        return "音声または映像の取得に失敗しました", 500

    # yt-dlpでタイトルと説明取得
    title_proc = subprocess.run(['yt-dlp.exe', '--get-title', url], capture_output=True, text=True)
    title = title_proc.stdout.strip() or "タイトル不明"

    desc_proc = subprocess.run(['yt-dlp.exe', '--get-description', url], capture_output=True, text=True)
    description = desc_proc.stdout.strip() or "概要なし"

    # GPUエンコーダー選択
    try:
        video_codec, preset_opts = select_video_codec()
    except RuntimeError as e:
        return str(e), 500

    # ffmpegでHLS変換
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
        '-hls_playlist_type', 'event',
        '-hls_segment_filename', os.path.join(output_dir, 'segment_%03d.ts'),
        os.path.join(output_dir, 'master.m3u8')
    ]
    subprocess.Popen(ffmpeg_cmd)

    # タイトルと説明はクエリに載せて渡す（セッションが必要ならFlask-Session推奨）
    params = urlencode({'title': title, 'description': description})
    return redirect(f'/watch/{session_id}?{params}')

@app.route('/watch/<session_id>')
def watch(session_id):
    output_dir = os.path.join(HLS_DIR, session_id)
    master_path = os.path.join(output_dir, 'master.m3u8')
    if not os.path.exists(master_path):
        return render_template('waiting.html', session_id=session_id)

    title = request.args.get('title', 'タイトルなし')
    description = request.args.get('description', '概要なし')
    stream_url = f'/hls/{session_id}/master.m3u8'
    return render_template('player.html', stream_url=stream_url, title=title, description=description)

@app.route('/sm-watch/<session_id>')
def mobile_watch(session_id):
    output_dir = os.path.join(HLS_DIR, session_id)
    master_path = os.path.join(output_dir, 'master.m3u8')
    if not os.path.exists(master_path):
        return render_template('waiting.html', session_id=session_id)

    title = request.args.get('title', 'タイトルなし')
    description = request.args.get('description', '概要なし')
    stream_url = f'/hls/{session_id}/master.m3u8'
    return render_template('smartphone_player.html', stream_url=stream_url, title=title, description=description)


@app.route('/hls/<session_id>/<path:filename>')
def hls(session_id, filename):
    hls_path = os.path.join(HLS_DIR, session_id)
    file_path = os.path.join(hls_path, filename)
    if not os.path.exists(file_path):
        abort(404)
    return send_from_directory(hls_path, filename)

@app.route('/cleanup')
def cleanup():
    # 認証必須
    if os.path.exists(HLS_DIR):
        shutil.rmtree(HLS_DIR)
    os.makedirs(HLS_DIR, exist_ok=True)
    return "HLSディレクトリを初期化しました"

if __name__ == '__main__':
    app.run(debug=True, threaded=True)
