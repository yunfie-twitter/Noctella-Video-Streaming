import yt_dlp
from database import db
from utils import get_client_ip
from flask import request
from config import MAX_SEARCH_RESULTS

class SearchSystem:
    def __init__(self):
        self.db = db
    
    def search_videos(self, query, max_results=20):
        """YouTube動画検索"""
        if not query or not query.strip():
            return []
        
        max_results = min(max_results, MAX_SEARCH_RESULTS)
        
        try:
            options = {
                'quiet': True,
                'extract_flat': True,
                'skip_download': True,
                'forcejson': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(options) as ydl:
                search_query = f"ytsearch{max_results}:{query.strip()}"
                result = ydl.extract_info(search_query, download=False)
                
                videos = []
                if result and 'entries' in result:
                    for entry in result['entries']:
                        if entry:
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
    
    def search_related_videos(self, video_id, title, max_results=8):
        """関連動画を検索"""
        if not title:
            return []
        
        # タイトルをベースに関連動画を検索
        query = f"{title} 関連"
        videos = self.search_videos(query, max_results + 1)
        
        # 現在の動画を除外
        related_videos = [v for v in videos if v['id'] != video_id]
        
        return related_videos[:max_results]
    
    def save_search_history(self, query, results_count):
        """検索履歴を保存"""
        if not query.strip():
            return
        
        ip_address = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')[:200]
        
        query_sql = '''
            INSERT INTO search_history (query, results_count, ip_address, user_agent)
            VALUES (?, ?, ?, ?)
        '''
        self.db.execute_insert(query_sql, (query.strip(), results_count, ip_address, user_agent))
    
    def get_popular_searches(self, limit=10):
        """人気検索キーワードを取得"""
        query = '''
            SELECT query, COUNT(*) as search_count, MAX(created_at) as last_search
            FROM search_history
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY LOWER(query)
            ORDER BY search_count DESC, last_search DESC
            LIMIT ?
        '''
        results = self.db.execute_query(query, (limit,))
        return [{'query': row[0], 'count': row[1]} for row in results]

# シングルトンインスタンス
search_system = SearchSystem()
