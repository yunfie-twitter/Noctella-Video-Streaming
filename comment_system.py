from database import db
from utils import get_client_ip, validate_session_id
from flask import request
from config import MAX_COMMENT_LENGTH, MAX_AUTHOR_LENGTH

class CommentSystem:
    def __init__(self):
        self.db = db
    
    def get_comments(self, video_id):
        """動画のコメントを取得"""
        query = '''
            SELECT id, author, content, session_id, ip_address, created_at
            FROM comments
            WHERE video_id = ?
            ORDER BY created_at DESC
        '''
        results = self.db.execute_query(query, (video_id,))
        return [
            {
                'id': row[0],
                'author': row[1],
                'content': row[2],
                'session_id': row[3],
                'ip_address': row[4],
                'created_at': row[5]
            }
            for row in results
        ]
    
    def add_comment(self, video_id, author, content, session_id):
        """コメントを追加"""
        if not validate_session_id(session_id):
            raise ValueError("Invalid session ID")
        
        if len(author) > MAX_AUTHOR_LENGTH:
            raise ValueError(f"Author name too long (max {MAX_AUTHOR_LENGTH})")
        
        if len(content) > MAX_COMMENT_LENGTH:
            raise ValueError(f"Comment too long (max {MAX_COMMENT_LENGTH})")
        
        ip_address = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')[:200]
        
        query = '''
            INSERT INTO comments (video_id, author, content, session_id, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
        '''
        return self.db.execute_insert(query, (video_id, author, content, session_id, ip_address, user_agent))
    
    def can_delete_comment(self, comment_id, session_id):
        """コメント削除権限を確認"""
        if not validate_session_id(session_id):
            return False
        
        query = '''
            SELECT session_id, ip_address
            FROM comments
            WHERE id = ?
        '''
        result = self.db.execute_query(query, (comment_id,))
        if not result:
            return False
        
        stored_session_id, stored_ip = result[0]
        current_ip = get_client_ip()
        
        return (stored_session_id == session_id and stored_ip == current_ip)
    
    def delete_comment(self, comment_id, session_id):
        """コメントを削除"""
        if not self.can_delete_comment(comment_id, session_id):
            return False
        
        query = 'DELETE FROM comments WHERE id = ?'
        self.db.execute_query(query, (comment_id,))
        return True

# シングルトンインスタンス
comment_system = CommentSystem()
