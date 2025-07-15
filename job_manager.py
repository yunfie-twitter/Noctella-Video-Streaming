import threading
import queue
import time
import sqlite3
import json
import subprocess
import os
import shutil
from datetime import datetime
from database import db
from video_utils import video_utils
from config import MAX_WORKERS, JOB_TIMEOUT, HLS_DIR

class JobManager:
    def __init__(self):
        # インメモリDB（ジョブ管理専用）
        self.job_db = sqlite3.connect(':memory:', check_same_thread=False)
        self.job_db.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress REAL NOT NULL DEFAULT 0,
                retries INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.job_db.commit()
        
        self.job_lock = threading.Lock()
        self.max_workers = MAX_WORKERS
        self.job_queue = []
        self.queue_lock = threading.Lock()
        self.workers = []
        
        # ワーカー起動
        self.start_worker_loop()
    
    def start_worker_loop(self):
        """ワーカーループを開始"""
        threading.Thread(target=self.worker_loop, daemon=True).start()
    
    def worker_loop(self):
        """ワーカーのメインループ"""
        while True:
            time.sleep(1)
            with self.queue_lock:
                if not self.job_queue:
                    continue
                    
                running_workers = sum(1 for w in self.workers if w.is_alive())
                if running_workers >= self.max_workers:
                    continue
                
                # 動画時間でソート（短い動画優先）
                sorted_jobs = self.sort_jobs_by_duration()
                if not sorted_jobs:
                    continue
                
                job_id = sorted_jobs[0]
                self.job_queue.remove(job_id)
                
                # ワーカースレッド起動
                worker = threading.Thread(
                    target=self.process_job, 
                    args=(job_id,), 
                    daemon=True
                )
                worker.start()
                self.workers.append(worker)
    
    def sort_jobs_by_duration(self):
        """ジョブを動画時間でソート"""
        job_durations = []
        for job_id in self.job_queue:
            with self.job_lock:
                cursor = self.job_db.cursor()
                cursor.execute("SELECT url FROM jobs WHERE id = ?", (job_id,))
                row = cursor.fetchone()
                if row:
                    url = row[0]
                    metadata = video_utils.get_video_metadata(url)
                    duration = metadata.get('duration', 0) if metadata else 0
                    job_durations.append((job_id, duration))
        
        # 短い動画を優先
        job_durations.sort(key=lambda x: x[1])
        return [job_id for job_id, _ in job_durations]
    
    def enqueue_job(self, video_id, url):
        """ジョブをキューに追加"""
        with self.job_lock:
            cursor = self.job_db.cursor()
            cursor.execute(
                "INSERT INTO jobs (video_id, url) VALUES (?, ?)",
                (video_id, url)
            )
            job_id = cursor.lastrowid
            self.job_db.commit()
        
        with self.queue_lock:
            self.job_queue.append(job_id)
        
        return job_id
    
    def process_job(self, job_id):
        """ジョブを処理"""
        try:
            # ジョブ情報取得
            with self.job_lock:
                cursor = self.job_db.cursor()
                cursor.execute(
                    "SELECT video_id, url, retries FROM jobs WHERE id = ?",
                    (job_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return
                
                video_id, url, retries = row
                cursor.execute(
                    "UPDATE jobs SET status = 'processing' WHERE id = ?",
                    (job_id,)
                )
                self.job_db.commit()
            
            self.update_progress(job_id, 0)
            
            # m3u8抽出
            result = subprocess.run(
                ['python3', 'm3u8_extractor.py', url, '--json'],
                capture_output=True,
                text=True,
                timeout=120
            )
            
            try:
                data = json.loads(result.stdout)
                info = data.get(url, {})
                video_url = info.get('video')
                audio_url = info.get('audio')
            except (json.JSONDecodeError, KeyError):
                self.mark_failed(job_id)
                return
            
            if not video_url or not audio_url:
                self.mark_failed(job_id)
                return
            
            self.update_progress(job_id, 30)
            
            # キャッシュディレクトリ作成
            cache_dir = os.path.join(HLS_DIR, video_id)
            os.makedirs(cache_dir, exist_ok=True)
            
            # FFmpeg変換
            ffmpeg_result = video_utils.run_ffmpeg_conversion(
                video_url, audio_url, cache_dir
            )
            
            if not ffmpeg_result['success']:
                shutil.rmtree(cache_dir, ignore_errors=True)
                self.handle_job_error(job_id, retries)
                return
            
            self.update_progress(job_id, 100)
            
            # 動画情報保存
            metadata = video_utils.get_video_metadata(url)
            if metadata:
                db.save_video_info(
                    video_id,
                    metadata['title'],
                    metadata['description'],
                    url,
                    cache_dir
                )
            
            # ジョブ完了
            with self.job_lock:
                cursor = self.job_db.cursor()
                cursor.execute(
                    "UPDATE jobs SET status = 'completed' WHERE id = ?",
                    (job_id,)
                )
                self.job_db.commit()
            
        except Exception as e:
            print(f"ジョブ処理エラー: {e}")
            self.mark_failed(job_id)
    
    def handle_job_error(self, job_id, retries):
        """ジョブエラーを処理"""
        max_retries = 2
        if retries < max_retries:
            # リトライ
            with self.job_lock:
                cursor = self.job_db.cursor()
                cursor.execute(
                    "UPDATE jobs SET status = 'pending', retries = retries + 1 WHERE id = ?",
                    (job_id,)
                )
                self.job_db.commit()
            
            with self.queue_lock:
                self.job_queue.append(job_id)
        else:
            self.mark_failed(job_id)
    
    def update_progress(self, job_id, progress):
        """進捗を更新"""
        with self.job_lock:
            cursor = self.job_db.cursor()
            cursor.execute(
                "UPDATE jobs SET progress = ? WHERE id = ?",
                (progress, job_id)
            )
            self.job_db.commit()
    
    def mark_failed(self, job_id):
        """ジョブを失敗としてマーク"""
        with self.job_lock:
            cursor = self.job_db.cursor()
            cursor.execute(
                "UPDATE jobs SET status = 'failed' WHERE id = ?",
                (job_id,)
            )
            self.job_db.commit()
    
    def get_job_status(self, job_id):
        """ジョブステータスを取得"""
        with self.job_lock:
            cursor = self.job_db.cursor()
            cursor.execute(
                "SELECT status, progress FROM jobs WHERE id = ?",
                (job_id,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    'status': row[0],
                    'progress': row[1]
                }
            return None

# シングルトンインスタンス
job_manager = JobManager()
