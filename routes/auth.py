import secrets
import hashlib
from flask import Blueprint, request, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from db.database import get_db

auth_bp = Blueprint('auth', __name__, url_prefix='/api')

SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days — matches the cookie's max_age

def hash_password(pw):
    """New passwords get a salted, slow hash instead of bare SHA-256."""
    return generate_password_hash(pw)

def verify_password(pw, stored_hash):
    """
    Accept either the new salted hash or a legacy unsalted-SHA256 hash left
    over from before this fix, so existing accounts keep working.
    """
    if stored_hash.startswith(('scrypt:', 'pbkdf2:')):
        return check_password_hash(stored_hash, pw)
    return hashlib.sha256(pw.encode()).hexdigest() == stored_hash

def generate_invite_code():
    return secrets.token_hex(4).upper()

def create_session(db, user_id):
    token = secrets.token_hex(32)
    db.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user_id))
    return token

# ── POST /api/register ────────────────────────────────────────────────────────
@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    phone       = (data.get('phone') or '').strip()
    password    = (data.get('password') or '').strip()
    invite_code = (data.get('invite_code') or '').strip().upper()

    if not phone or not password:
        return jsonify({'ok': False, 'error': 'Phone and password are required'})

    db = get_db()
    try:
        existing = db.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
        if existing:
            return jsonify({'ok': False, 'error': 'Phone already registered'})

        invited_by = None
        assigned_manager_id = None
        if invite_code:
            ref = db.execute("SELECT id, assigned_manager_id FROM users WHERE invite_code=?", (invite_code,)).fetchone()
            if ref:
                invited_by = ref['id']
                # Rule: an invitee always gets the SAME manager as whoever
                # invited them. (Should never be NULL in practice — every
                # user is guaranteed a manager — but the fallback chain
                # below still applies if it somehow were.)
                assigned_manager_id = ref['assigned_manager_id']

        if assigned_manager_id is None:
            # No referrer, or the referrer's manager came back empty —
            # either a manager's own direct signup link (?mgr=CODE on
            # index.html), or a fully organic signup.
            manager_code = (data.get('manager_code') or '').strip().upper()
            if manager_code:
                mgr = db.execute("SELECT id FROM admins WHERE manager_code=?", (manager_code,)).fetchone()
                if mgr:
                    assigned_manager_id = mgr['id']

        if assigned_manager_id is None:
            # Round-robin fallback so organic signups still land with
            # someone: whichever manager currently has the fewest
            # assigned users. 'super' (the main manager) is excluded from
            # this pool — they're for oversight, not holding a batch.
            least_loaded = db.execute("""
                SELECT a.id
                FROM admins a
                LEFT JOIN users u ON u.assigned_manager_id = a.id
                WHERE a.role = 'manager'
                GROUP BY a.id
                ORDER BY COUNT(u.id) ASC, a.id ASC
                LIMIT 1
            """).fetchone()
            if least_loaded:
                assigned_manager_id = least_loaded['id']

        if assigned_manager_id is None:
            # No 'manager' accounts exist at all yet (e.g. brand new
            # deployment with only the main manager) — every user must
            # still be assigned to someone, so fall back to the main
            # manager rather than leaving this NULL.
            main_mgr = db.execute("SELECT id FROM admins WHERE role='super' ORDER BY created_at ASC LIMIT 1").fetchone()
            if main_mgr:
                assigned_manager_id = main_mgr['id']

        # The check above has a race: two identical registrations can both
        # pass it before either INSERT commits. The UNIQUE constraint on
        # phone is the real guard — if we lose that race, catch it here and
        # return a clean error instead of an unhandled 500.
        my_invite = generate_invite_code()
        try:
            db.execute(
                """INSERT INTO users (phone, password, invite_code, invited_by, assigned_manager_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (phone, hash_password(password), my_invite, invited_by, assigned_manager_id)
            )
        except Exception:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Phone already registered'})

        if invited_by:
            db.execute("UPDATE users SET invite_count=invite_count+1, team_count=team_count+1 WHERE id=?",
                       (invited_by,))

        db.commit()

        user = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        token = create_session(db, user['id'])
        db.commit()

        resp = make_response(jsonify({'ok': True}))
        resp.set_cookie('session_token', token, httponly=True, samesite='Lax', max_age=SESSION_MAX_AGE)
        return resp

    finally:
        db.close()

# ── POST /api/login ───────────────────────────────────────────────────────────
@auth_bp.route('/login', methods=['POST'])
def login():
    data     = request.get_json() or {}
    phone    = (data.get('phone') or '').strip()
    password = (data.get('password') or '').strip()

    if not phone or not password:
        return jsonify({'ok': False, 'error': 'Phone and password are required'})

    db = get_db()
    try:
        user = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        if not user or not verify_password(password, user['password']):
            return jsonify({'ok': False, 'error': 'Invalid phone or password'})

        # Transparently upgrade legacy sha256 hashes now that we know the
        # plaintext password was correct.
        if not user['password'].startswith(('scrypt:', 'pbkdf2:')):
            db.execute("UPDATE users SET password=? WHERE id=?", (hash_password(password), user['id']))

        token = create_session(db, user['id'])
        db.commit()

        resp = make_response(jsonify({'ok': True}))
        resp.set_cookie('session_token', token, httponly=True, samesite='Lax', max_age=SESSION_MAX_AGE)
        return resp
    finally:
        db.close()

# ── POST /api/logout ──────────────────────────────────────────────────────────
@auth_bp.route('/logout', methods=['POST'])
def logout():
    token = request.cookies.get('session_token')
    if token:
        db = get_db()
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
        db.commit()
        db.close()
    resp = make_response(jsonify({'ok': True}))
    resp.delete_cookie('session_token')
    return resp
