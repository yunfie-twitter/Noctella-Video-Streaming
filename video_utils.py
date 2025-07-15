import subprocess
import yt_dlp
import os
from config import FFMPEG_TIMEOUT, DEFAULT_AUDIO_BITRATE, HLS_SEGMENT_TIME

class VideoUtils:
    def __init__(self):
        pass
    
    def get_video_metadata(self, url):
        """動画のメタデータを取得"""
        try:
            options = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'skip_download': True,
            }
            
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    'title': info.get('title', 'Unknown'),
                    'description': info.get('description', ''),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader', 'Unknown')
                }
        except Exception as e:
            print(f"メタデータ取得エラー: {e}")
            return None
    
    def check_encoder(self, name):
        """エンコーダーの存在確認"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-encoders'], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
            return name in result.stdout
        except Exception:
            return False
    
    def select_video_codec(self):
        """最適なビデオコーデックを選択"""
        if self.check_encoder('h264_nvenc'):
            return 'h264_nvenc', ['-preset', 'fast']
        elif self.check_encoder('h264_amf'):
            return 'h264_amf', []
        elif self.check_encoder('h264_qsv'):
            return 'h264_qsv', []
        else:
            return 'libx264', ['-preset', 'veryfast']
    
    def run_ffmpeg_conversion(self, video_url, audio_url, output_dir):
        """FFmpegでHLS変換を実行"""
        try:
            master_path = os.path.join(output_dir, 'master.m3u8')
            video_codec, preset_opts = self.select_video_codec()
            
            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-i', video_url,
                '-i', audio_url,
                '-c:v', video_codec,
                *preset_opts,
                '-c:a', 'aac',
                '-b:a', DEFAULT_AUDIO_BITRATE,
                '-f', 'hls',
                '-hls_time', str(HLS_SEGMENT_TIME),
                '-hls_playlist_type', 'event',
                '-hls_segment_filename', os.path.join(output_dir, 'segment_%03d.ts'),
                master_path
            ]
            
            process = subprocess.run(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=FFMPEG_TIMEOUT
            )
            
            if process.returncode != 0:
                return {
                    'success': False,
                    'error': f'FFmpeg failed: {process.stderr}'
                }
            
            if not os.path.exists(master_path):
                return {
                    'success': False,
                    'error': 'master.m3u8 not created'
                }
            
            return {'success': True, 'master_path': master_path}
            
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': 'FFmpeg timeout'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

# シングルトンインスタンス
video_utils = VideoUtils()
