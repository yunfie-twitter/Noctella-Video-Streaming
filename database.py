import sqlite3
import threading
from datetime import datetime
from config import DATABASE_PATH

class Database:
    def __init__(self):
        self.db_path = DATABASE_PATH
        self.lock = threading.Lock()
        self.init_database()
    
    def init_database(self):
        """データベース初期化"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 動画テーブル
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
        
        # コメントテーブル
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
        
        # 検索履歴テーブル
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
        
        conn.commit()
        conn.close()
    
    def get_connection(self):
        """データベース接続を取得"""
        return sqlite3.connect(self.db_path)
    
    def execute_query(self, query, params=None):
        """クエリ実行"""
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            result = cursor.fetchall()
            conn.commit()
            conn.close()
            return result
    
    def execute_insert(self, query, params):
        """INSERT文実行"""
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            last_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return last_id
    
    def get_video_info(self, video_id):
        """動画情報を取得"""
        query = '''
            SELECT video_id, title, description, original_url, cache_path, created_at
            FROM videos WHERE video_id = ?
        '''
        result = self.execute_query(query, (video_id,))
        if result:
            row = result[0]
            return {
                'video_id': row[0],
                'title': row[1],
                'description': row[2],
                'original_url': row[3],
                'cache_path': row[4],
                'created_at': row[5]
            }
        return None
    
    def save_video_info(self, video_id, title, description, original_url, cache_path):
        """動画情報を保存"""
        query = '''
            INSERT OR REPLACE INTO videos 
            (video_id, title, description, original_url, cache_path, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        '''
        return self.execute_insert(query, (video_id, title, description, original_url, cache_path))
    
    def get_all_videos(self):
        """すべての動画を取得"""
        query = '''
            SELECT video_id, title, description, created_at
            FROM videos ORDER BY created_at DESC
        '''
        results = self.execute_query(query)
        return [
            {
                'video_id': row[0],
                'title': row[1],
                'description': row[2],
                'created_at': row[3]
            }
            for row in results
        ]

# シングルトンインスタンス
db = Database()
