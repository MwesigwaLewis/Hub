from functools import wraps
from flask import request, jsonify, g
from db.database import get_db
from db.accrual import accrue_user_earnings


def _get_token():
    return (
        request.cookies.get('session_token') or
        request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    )


def get_current_user():
    """
    Validate session token, run accrual if cooldown allows, return user row.

    KEY CHANGE: Previously this opened its own DB connection, did the session
    lookup + accrual + user re-fetch, closed the connection — then every route
    using @login_required opened a SECOND connection for the actual work.
    That was 2 pool slots per request on a pool capped at max_size=2.

    Now we store the validated user on Flask's 'g' object so the same
    connection (or at least the same result) is reused within a request.
    The route itself still gets its own connection via get_db(), but the
    middleware no longer holds one open while the route runs.
    """
    # Return cached result within the same request context
    if hasattr(g, '_current_user'):
        return g._current_user

    token = _get_token()
    if not token:
        g._current_user = None
        return None

    db = get_db()
    try:
        user = db.execute(
            """SELECT u.* FROM users u
               JOIN sessions s ON s.user_id = u.id
               WHERE s.token = ?""",
            (token,)
        ).fetchone()

        if user:
            # accrue_user_earnings returns False (no DB work) when the
            # in-process cooldown is active — so this is effectively free
            # on the vast majority of requests.
            did_accrue = accrue_user_earnings(db, user['id'])
            if did_accrue:
                # Re-fetch the user row only when accrual actually wrote data
                user = db.execute(
                    "SELECT * FROM users WHERE id=?", (user['id'],)
                ).fetchone()
    finally:
        db.close()

    g._current_user = user
    return user


def login_required(f):
    """Decorator — returns {ok:false, error:...} 401 if not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
        return f(user, *args, **kwargs)
    return decorated
