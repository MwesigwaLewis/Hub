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

# ── GET /api/team ─────────────────────────────────────────────────────────────
# Two-level downline: level 1 = people the user invited directly, level 2 =
# people THEY invited. For each member, also list the machines they've
# bought (so the user can see who's actually active).
@user_bp.route('/team', methods=['GET'])
@login_required
def team(current_user):
    db = get_db()
    try:
        level1 = db.execute(
            "SELECT id, phone, nick, created_at FROM users WHERE invited_by=? ORDER BY created_at DESC",
            (current_user['id'],)
        ).fetchall()
        level1_ids = [row['id'] for row in level1]

        level2 = []
        if level1_ids:
            placeholders = ','.join(['?'] * len(level1_ids))
            level2 = db.execute(
                f"""SELECT id, phone, nick, invited_by, created_at
                    FROM users WHERE invited_by IN ({placeholders})
                    ORDER BY created_at DESC""",
                tuple(level1_ids)
            ).fetchall()

        all_ids = level1_ids + [row['id'] for row in level2]
        machines_by_user = {}
        if all_ids:
            placeholders = ','.join(['?'] * len(all_ids))
            rows = db.execute(
                f"""SELECT um.user_id, um.machine_id, um.purchase_price, um.status, um.bought_at
                    FROM user_machines um WHERE um.user_id IN ({placeholders})
                    ORDER BY um.bought_at DESC""",
                tuple(all_ids)
            ).fetchall()
            for r in rows:
                machines_by_user.setdefault(r['user_id'], []).append({
                    'machine_id':     r['machine_id'],
                    'purchase_price': r['purchase_price'],
                    'status':         r['status'],
                    'bought_at':      r['bought_at'].strftime('%Y-%m-%d') if r['bought_at'] else None,
                })

        def fmt_member(row):
            return {
                'phone':    row['phone'],
                'nick':     row['nick'],
                'joined':   row['created_at'].strftime('%Y-%m-%d') if row['created_at'] else None,
                'machines': machines_by_user.get(row['id'], []),
            }

        return jsonify({
            'ok': True,
            'level1': [fmt_member(r) for r in level1],
            'level2': [fmt_member(r) for r in level2],
        })
    finally:
        db.close()

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

# ── POST /api/profile/avatar ──────────────────────────────────────────────────
@user_bp.route('/profile/avatar', methods=['POST'])
@login_required
def update_avatar(current_user):
    data      = request.get_json() or {}
    avatar_url = (data.get('avatar_url') or '').strip()
    # Accept either a data: URI (base64 upload) or a plain https URL
    if not avatar_url:
        return jsonify({'ok': False, 'error': 'No avatar URL provided'})
    if not (avatar_url.startswith('data:image/') or avatar_url.startswith('https://')):
        return jsonify({'ok': False, 'error': 'Invalid image format'})
    db = get_db()
    try:
        db.execute("UPDATE users SET avatar_url=? WHERE id=?", (avatar_url, current_user['id']))
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

# ── GET /api/messages — full announcement history + this user's read state ────
@user_bp.route('/messages', methods=['GET'])
@login_required
def messages(current_user):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT m.id, m.text, m.created_at,
                   (mr.read_at IS NOT NULL) AS read
            FROM messages m
            LEFT JOIN message_reads mr ON mr.message_id = m.id AND mr.user_id = ?
            ORDER BY m.created_at DESC
            LIMIT 200
        """, (current_user['id'],)).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
        return jsonify({'ok': True, 'messages': rows})
    finally:
        db.close()

# ── GET /api/messages/popup ─────────────────────────────────────────────────
# Returns the single newest announcement that should auto-popup on the
# homepage for this user right now, or null if nothing qualifies. Calling
# this ALSO records that it was shown (see comment on message_reads above) —
# so this endpoint should only be called once per homepage load, not polled.
@user_bp.route('/messages/popup', methods=['GET'])
@login_required
def messages_popup(current_user):
    db = get_db()
    try:
        login_count = current_user['login_count'] or 0
        row = db.execute("""
            SELECT m.id, m.text, m.created_at
            FROM messages m
            LEFT JOIN message_reads mr ON mr.message_id = m.id AND mr.user_id = ?
            WHERE COALESCE(mr.popup_count, 0) < 2
              AND (mr.last_popup_login_count IS NULL OR mr.last_popup_login_count <> ?)
            ORDER BY m.created_at DESC
            LIMIT 1
        """, (current_user['id'], login_count)).fetchone()

        if not row:
            return jsonify({'ok': True, 'message': None})

        db.execute("""
            INSERT INTO message_reads (user_id, message_id, popup_count, last_popup_login_count)
            VALUES (?, ?, 1, ?)
            ON CONFLICT (user_id, message_id) DO UPDATE
            SET popup_count = message_reads.popup_count + 1,
                last_popup_login_count = EXCLUDED.last_popup_login_count
        """, (current_user['id'], row['id'], login_count))
        db.commit()

        return jsonify({'ok': True, 'message': {
            'id': row['id'],
            'text': row['text'],
            'created_at': row['created_at'].strftime('%Y-%m-%d %H:%M') if row['created_at'] else None,
        }})
    finally:
        db.close()

# ── POST /api/messages/<id>/read ────────────────────────────────────────────
# Marks an announcement as read for the current user — called whenever the
# single-announcement modal is opened, whether that was triggered by the
# auto-popup or by a manual tap in the tile/inbox. Deliberately does NOT
# touch popup_count / last_popup_login_count — read state and the login-popup
# countdown are independent, per the design.
@user_bp.route('/messages/<int:message_id>/read', methods=['POST'])
@login_required
def mark_message_read(current_user, message_id):
    db = get_db()
    try:
        exists = db.execute("SELECT id FROM messages WHERE id=?", (message_id,)).fetchone()
        if not exists:
            return jsonify({'ok': False, 'error': 'Message not found'})
        db.execute("""
            INSERT INTO message_reads (user_id, message_id, read_at)
            VALUES (?, ?, NOW())
            ON CONFLICT (user_id, message_id) DO UPDATE
            SET read_at = COALESCE(message_reads.read_at, EXCLUDED.read_at)
        """, (current_user['id'], message_id))
        db.commit()
        return jsonify({'ok': True})
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
