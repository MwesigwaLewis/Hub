import secrets
from flask import Blueprint, request, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from db.database import get_db
from middleware.admin_auth import admin_login_required, ADMIN_COOKIE_NAME

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# ── POST /api/admin/login ─────────────────────────────────────────────────────
@admin_bp.route('/login', methods=['POST'])
def admin_login():
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not password:
        return jsonify({'ok': False, 'error': 'Username and password are required'})

    db = get_db()
    try:
        admin = db.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
        if not admin or not check_password_hash(admin['password'], password):
            return jsonify({'ok': False, 'error': 'Invalid username or password'})

        token = secrets.token_hex(32)
        db.execute("INSERT INTO admin_sessions (token, admin_id) VALUES (?,?)", (token, admin['id']))
        db.commit()

        resp = make_response(jsonify({'ok': True, 'name': admin['name']}))
        # Deliberately a different cookie name/path scoping mindset from the
        # customer session — this cookie should only ever be sent to /admin/*.
        resp.set_cookie(ADMIN_COOKIE_NAME, token, httponly=True, samesite='Lax', max_age=SESSION_MAX_AGE)
        return resp
    finally:
        db.close()

# ── POST /api/admin/logout ────────────────────────────────────────────────────
@admin_bp.route('/logout', methods=['POST'])
def admin_logout():
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if token:
        db = get_db()
        db.execute("DELETE FROM admin_sessions WHERE token=?", (token,))
        db.commit()
        db.close()
    resp = make_response(jsonify({'ok': True}))
    resp.delete_cookie(ADMIN_COOKIE_NAME)
    return resp

# ── GET /api/admin/me ──────────────────────────────────────────────────────────
@admin_bp.route('/me', methods=['GET'])
@admin_login_required
def admin_me(current_admin):
    return jsonify({'ok': True, 'username': current_admin['username'], 'name': current_admin['name']})

# ── POST /api/admin/change-password ───────────────────────────────────────────
@admin_bp.route('/change-password', methods=['POST'])
@admin_login_required
def admin_change_password(current_admin):
    data = request.get_json() or {}
    old_password = (data.get('old_password') or '').strip()
    new_password = (data.get('new_password') or '').strip()

    if not check_password_hash(current_admin['password'], old_password):
        return jsonify({'ok': False, 'error': 'Current password is incorrect'})
    if len(new_password) < 8:
        return jsonify({'ok': False, 'error': 'New password must be at least 8 characters'})

    db = get_db()
    try:
        db.execute("UPDATE admins SET password=? WHERE id=?",
                   (generate_password_hash(new_password), current_admin['id']))
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

# ── GET /api/admin/stats ───────────────────────────────────────────────────────
@admin_bp.route('/stats', methods=['GET'])
@admin_login_required
def admin_stats(current_admin):
    db = get_db()
    try:
        users_count      = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()['c']
        total_deposits    = db.execute("SELECT COALESCE(SUM(amount),0) AS s FROM deposit_transactions WHERE status='successful'").fetchone()['s']
        total_withdrawn   = db.execute("SELECT COALESCE(SUM(amount),0) AS s FROM withdraw_requests WHERE status='approved'").fetchone()['s']
        pending_withdraws = db.execute("SELECT COUNT(*) AS c FROM withdraw_requests WHERE status='pending'").fetchone()['c']
        unread_chats      = db.execute("""
            SELECT COUNT(DISTINCT user_id) AS c FROM chat_messages
            WHERE sender='user' AND read_by_admin=FALSE
        """).fetchone()['c']

        return jsonify({
            'ok': True,
            'users_count': users_count,
            'total_deposits': total_deposits,
            'total_withdrawn': total_withdrawn,
            'pending_withdraws': pending_withdraws,
            'unread_chats': unread_chats,
        })
    finally:
        db.close()

# ── GET /api/admin/users?search=&limit=&offset= ───────────────────────────────
@admin_bp.route('/users', methods=['GET'])
@admin_login_required
def admin_users(current_admin):
    search = (request.args.get('search') or '').strip()
    limit  = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    db = get_db()
    try:
        if search:
            rows = db.execute("""
                SELECT id, phone, nick, balance, wallet, total_deposit, total_withdraw,
                       ai_income, invite_count, vip_level, created_at
                FROM users WHERE phone ILIKE ? OR nick ILIKE ?
                ORDER BY created_at DESC LIMIT ? OFFSET ?
            """, (f'%{search}%', f'%{search}%', limit, offset)).fetchall()
        else:
            rows = db.execute("""
                SELECT id, phone, nick, balance, wallet, total_deposit, total_withdraw,
                       ai_income, invite_count, vip_level, created_at
                FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()

        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d') if r['created_at'] else None

        return jsonify({'ok': True, 'users': rows})
    finally:
        db.close()

# ── GET /api/admin/withdrawals?status=pending ─────────────────────────────────
@admin_bp.route('/withdrawals', methods=['GET'])
@admin_login_required
def admin_withdrawals(current_admin):
    status = request.args.get('status', 'pending')
    db = get_db()
    try:
        rows = db.execute("""
            SELECT w.id, w.user_id, w.amount, w.status, w.created_at, w.processed_at,
                   u.phone, u.nick
            FROM withdraw_requests w
            JOIN users u ON u.id = w.user_id
            WHERE w.status = ?
            ORDER BY w.created_at ASC
        """, (status,)).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
            r['processed_at'] = r['processed_at'].strftime('%Y-%m-%d %H:%M') if r['processed_at'] else None
        return jsonify({'ok': True, 'withdrawals': rows})
    finally:
        db.close()

# ── POST /api/admin/withdrawals/<id>/approve ──────────────────────────────────
# Approving just marks it paid — actually sending the money (mobile money
# payout, bank transfer, etc.) happens outside this app, same as before.
@admin_bp.route('/withdrawals/<int:withdraw_id>/approve', methods=['POST'])
@admin_login_required
def approve_withdrawal(current_admin, withdraw_id):
    db = get_db()
    try:
        result = db.execute(
            "UPDATE withdraw_requests SET status='approved', processed_at=NOW() WHERE id=? AND status='pending'",
            (withdraw_id,)
        )
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Withdrawal not found or already processed'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

# ── POST /api/admin/withdrawals/<id>/reject ───────────────────────────────────
# The balance was already deducted when the user submitted the request, so
# rejecting has to refund it — otherwise the money just vanishes.
@admin_bp.route('/withdrawals/<int:withdraw_id>/reject', methods=['POST'])
@admin_login_required
def reject_withdrawal(current_admin, withdraw_id):
    db = get_db()
    try:
        w = db.execute("SELECT * FROM withdraw_requests WHERE id=? AND status='pending'", (withdraw_id,)).fetchone()
        if not w:
            return jsonify({'ok': False, 'error': 'Withdrawal not found or already processed'})

        db.execute(
            "UPDATE withdraw_requests SET status='rejected', processed_at=NOW() WHERE id=?",
            (withdraw_id,)
        )
        db.execute(
            "UPDATE users SET balance=balance+?, total_withdraw=total_withdraw-? WHERE id=?",
            (w['amount'], w['amount'], w['user_id'])
        )
        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?,?,?,?)",
            (w['user_id'], 'withdraw_refund', w['amount'], 'Withdrawal rejected by manager — refunded')
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

# ── GET /api/admin/chat/conversations ─────────────────────────────────────────
# One row per user who has ever messaged in, with their last message and
# whether it's unread, newest activity first.
@admin_bp.route('/chat/conversations', methods=['GET'])
@admin_login_required
def admin_chat_conversations(current_admin):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT DISTINCT ON (cm.user_id)
                   cm.user_id, u.phone, u.nick, cm.body AS last_message,
                   cm.sender AS last_sender, cm.created_at AS last_at,
                   EXISTS (
                       SELECT 1 FROM chat_messages x
                       WHERE x.user_id = cm.user_id AND x.sender='user' AND x.read_by_admin=FALSE
                   ) AS unread
            FROM chat_messages cm
            JOIN users u ON u.id = cm.user_id
            ORDER BY cm.user_id, cm.created_at DESC
        """).fetchall()
        rows.sort(key=lambda r: r['last_at'], reverse=True)
        for r in rows:
            r['last_at'] = r['last_at'].strftime('%Y-%m-%d %H:%M') if r['last_at'] else None
        return jsonify({'ok': True, 'conversations': rows})
    finally:
        db.close()

# ── GET /api/admin/chat/<user_id> ─────────────────────────────────────────────
@admin_bp.route('/chat/<int:user_id>', methods=['GET'])
@admin_login_required
def admin_chat_thread(current_admin, user_id):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, sender, body, created_at FROM chat_messages WHERE user_id=? ORDER BY created_at ASC",
            (user_id,)
        ).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None

        # Opening the thread marks the user's messages as read.
        db.execute("UPDATE chat_messages SET read_by_admin=TRUE WHERE user_id=? AND sender='user'", (user_id,))
        db.commit()

        return jsonify({'ok': True, 'messages': rows})
    finally:
        db.close()

# ── POST /api/admin/chat/<user_id>/send ───────────────────────────────────────
@admin_bp.route('/chat/<int:user_id>/send', methods=['POST'])
@admin_login_required
def admin_chat_send(current_admin, user_id):
    data = request.get_json() or {}
    body = (data.get('body') or '').strip()
    if not body:
        return jsonify({'ok': False, 'error': 'Message cannot be empty'})

    db = get_db()
    try:
        user = db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return jsonify({'ok': False, 'error': 'User not found'})

        db.execute(
            "INSERT INTO chat_messages (user_id, sender, body, read_by_admin) VALUES (?, 'admin', ?, TRUE)",
            (user_id, body)
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()
  
