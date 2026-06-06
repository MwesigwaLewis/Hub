from flask import Blueprint, request, jsonify
from db.database import get_db
from middleware.auth import login_required

user_bp = Blueprint('user', __name__, url_prefix='/api')

# ── GET /api/me ───────────────────────────────────────────────────────────────
@user_bp.route('/me', methods=['GET'])
@login_required
def me(current_user):
    return jsonify({'ok': True, 'user': {
        'id':            current_user['id'],
        'phone':         current_user['phone'],
        'nick':          current_user['nick'],
        'avatar_url':    current_user['avatar_url'],
        'email':         current_user['email'],
        'vip_level':     current_user['vip_level'],
        'balance':       current_user['balance'],
        'wallet':        current_user['wallet'],
        'total_deposit': current_user['total_deposit'],
        'total_withdraw':current_user['total_withdraw'],
        'ai_income':     current_user['ai_income'],
        'today_earnings':current_user['today_earnings'],
        'team_income':   current_user['team_income'],
        'invite_count':  current_user['invite_count'],
        'team_count':    current_user['team_count'],
        'invite_code':   current_user['invite_code'],
        'raffle_ready':  current_user['raffle_ready'],
        'last_salary':   current_user['last_salary'],
        'this_salary':   current_user['this_salary'],
    }})

# ── POST /api/profile/nick ────────────────────────────────────────────────────
@user_bp.route('/profile/nick', methods=['POST'])
@login_required
def update_nick(current_user):
    data = request.get_json() or {}
    nick = (data.get('nick') or '').strip()
    if not nick or len(nick) > 30:
        return jsonify({'ok': False, 'error': 'Invalid nickname'})
    db = get_db()
    try:
        db.execute("UPDATE users SET nick=? WHERE id=?", (nick, current_user['id']))
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

# ── GET /api/messages ─────────────────────────────────────────────────────────
@user_bp.route('/messages', methods=['GET'])
@login_required
def messages(current_user):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT text FROM messages ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        return jsonify({'ok': True, 'messages': [dict(r) for r in rows]})
    finally:
        db.close()

# ── POST /api/salary/claim ────────────────────────────────────────────────────
@user_bp.route('/salary/claim', methods=['POST'])
@login_required
def claim_salary(current_user):
    db = get_db()
    try:
        amount = current_user['last_salary']
        if not amount or amount <= 0:
            return jsonify({'ok': False, 'error': 'No salary available to claim'})

        db.execute("""
            UPDATE users
            SET balance      = balance + ?,
                last_salary  = 0,
                this_salary  = this_salary + ?
            WHERE id = ?
        """, (amount, amount, current_user['id']))

        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?,?,?,?)",
            (current_user['id'], 'salary', amount, 'Monthly salary claim')
        )
        db.commit()
        return jsonify({'ok': True, 'amount': amount})
    finally:
        db.close()
