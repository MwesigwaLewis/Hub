from functools import wraps
from flask import request, jsonify, g
from db.database import get_db

ADMIN_COOKIE_NAME = 'admin_session_token'

def get_current_admin():
    """Read the admin session cookie (deliberately a different cookie name
    from the customer 'session_token') and return the admin row or None.

    Cached on Flask's 'g' object for the lifetime of the request, mirroring
    the customer-auth optimization in middleware/auth.py — if anything else
    in the same request calls this again, it's free instead of opening
    another DB connection against a pool capped at 2.
    """
    if hasattr(g, '_current_admin'):
        return g._current_admin

    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        g._current_admin = None
        return None

    db = get_db()
    try:
        admin = db.execute(
            """SELECT a.* FROM admins a
               JOIN admin_sessions s ON s.admin_id = a.id
               WHERE s.token = ? AND s.created_at > NOW() - INTERVAL '7 days'""",
            (token,)
        ).fetchone()
    finally:
        db.close()

    g._current_admin = admin
    return admin

def admin_login_required(f):
    """Decorator for every /api/admin/* route. Completely independent of
    login_required — an admin session can never satisfy this, only an
    admin_sessions token can.

    Wrapped in try/except so a transient DB hiccup or query error inside
    get_current_admin() can never escape as an unhandled exception. Flask's
    default handling of an unhandled exception is an HTML 500 page — which
    breaks dashboard.html's api() call (it expects JSON) and was the root
    cause of the "silent crash loop" (login -> dashboard -> instantly back
    to login). Every failure path here now always returns real JSON.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            admin = get_current_admin()
        except Exception:
            return jsonify({'ok': False, 'error': 'Server error — please try again'}), 500
        if not admin:
            return jsonify({'ok': False, 'error': 'Not authenticated as admin'}), 401
        return f(admin, *args, **kwargs)
    return decorated
