from functools import wraps
from flask import request, jsonify
from db.database import get_db

def get_current_user():
    """Read session token from cookie or Authorization header, return user row or None."""
    token = request.cookies.get('session_token') or \
            request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    if not token:
        return None
    db = get_db()
    user = db.execute(
        """SELECT u.* FROM users u
           JOIN sessions s ON s.user_id = u.id
           WHERE s.token = ?""", (token,)
    ).fetchone()
    db.close()
    return user

def login_required(f):
    """Decorator — returns {ok:false} with 401 if not logged in."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
        return f(user, *args, **kwargs)
    return decorated
