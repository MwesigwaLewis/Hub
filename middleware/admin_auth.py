from functools import wraps
from flask import request, jsonify
from db.database import get_db

ADMIN_COOKIE_NAME = 'admin_session_token'

def get_current_admin():
    """Read the admin session cookie (deliberately a different cookie name
    from the customer 'session_token') and return the admin row or None."""
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        return None

    db = get_db()
    try:
        admin = db.execute(
            """SELECT a.* FROM admins a
               JOIN admin_sessions s ON s.admin_id = a.id
               WHERE s.token = ? AND s.created_at > NOW() - INTERVAL '7 days'""",
            (token,)
        ).fetchone()
        return admin
    finally:
        db.close()

def admin_login_required(f):
    """Decorator for every /api/admin/* route. Completely independent of
    login_required — an admin session can never satisfy this, only an
    admin_sessions token can."""
    @wraps(f)
    def decorated(*args, **kwargs):
        admin = get_current_admin()
        if not admin:
            return jsonify({'ok': False, 'error': 'Not authenticated as admin'}), 401
        return f(admin, *args, **kwargs)
    return decorated
