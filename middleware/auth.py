from functools import wraps
from flask import request, jsonify
from db.database import get_db
from db.accrual import accrue_user_earnings

def get_current_user():
    """Read session token from cookie or Authorization header, return user row or None."""
    token = request.cookies.get('session_token') or \
            request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    if not token:
        return None
    db = get_db()
    try:
        user = db.execute(
            """SELECT u.* FROM users u
               JOIN sessions s ON s.user_id = u.id
               WHERE s.token = ?""", (token,)
        ).fetchone()

        if user:
            # Bring machine earnings up to date on every authenticated
            # request so income starts counting immediately after purchase
            # instead of waiting for some page-specific refresh.
            accrue_user_earnings(db, user['id'])
            user = db.execute("SELECT * FROM users WHERE id=?", (user['id'],)).fetchone()

        return user
    finally:
        db.close()

def login_required(f):
    """Decorator — returns {ok:false} with 401 if not logged in."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
        return f(user, *args, **kwargs)
    return decorated
        
