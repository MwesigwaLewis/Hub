import secrets
import hashlib
from flask import Blueprint, request, jsonify, make_response
from db.database import get_db

auth_bp = Blueprint('auth', __name__, url_prefix='/api')

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def generate_invite_code():
    return secrets.token_hex(4).upper()

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
        # Check if phone already registered
        existing = db.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
        if existing:
            return jsonify({'ok': False, 'error': 'Phone already registered'})

        # Resolve referrer
        invited_by = None
        if invite_code:
            ref = db.execute("SELECT id FROM users WHERE invite_code=?", (invite_code,)).fetchone()
            if ref:
                invited_by = ref['id']

        # Create user
        my_invite = generate_invite_code()
        db.execute(
            """INSERT INTO users (phone, password, invite_code, invited_by)
               VALUES (?, ?, ?, ?)""",
            (phone, hash_password(password), my_invite, invited_by)
        )

        # Credit referrer's team count
        if invited_by:
            db.execute("UPDATE users SET invite_count=invite_count+1, team_count=team_count+1 WHERE id=?",
                       (invited_by,))

        db.commit()

        # Auto-login after registration
        user = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        token = secrets.token_hex(32)
        db.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user['id']))
        db.commit()

        resp = make_response(jsonify({'ok': True}))
        resp.set_cookie('session_token', token, httponly=True, samesite='Lax', max_age=60*60*24*30)
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
        user = db.execute("SELECT * FROM users WHERE phone=? AND password=?",
                          (phone, hash_password(password))).fetchone()
        if not user:
            return jsonify({'ok': False, 'error': 'Invalid phone or password'})

        token = secrets.token_hex(32)
        db.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user['id']))
        db.commit()

        resp = make_response(jsonify({'ok': True}))
        resp.set_cookie('session_token', token, httponly=True, samesite='Lax', max_age=60*60*24*30)
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
