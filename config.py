import os

# データベース設定
DATABASE_PATH = 'video_cache.db'
HLS_DIR = 'hls_temp'

# ジョブ設定
MAX_WORKERS = 2
JOB_TIMEOUT = 3600  # 1時間

# API設定
MAX_SEARCH_RESULTS = 50
MAX_COMMENT_LENGTH = 500
MAX_AUTHOR_LENGTH = 50

# ffmpeg設定
FFMPEG_TIMEOUT = 3600
DEFAULT_AUDIO_BITRATE = '128k'
HLS_SEGMENT_TIME = 4

# セキュリティ設定
SESSION_ID_LENGTH = 32
