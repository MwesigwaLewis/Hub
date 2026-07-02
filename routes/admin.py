import secrets
from flask import Blueprint, request, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from db.database import get_db
from db.vip import recompute_vip_level
from middleware.admin_auth import admin_login_required, ADMIN_COOKIE_NAME

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Whitelisted user columns a manager is allowed to edit directly. Deliberately
# excludes 'password' (needs hashing — see reset-password) and 'id'/'invited_by'
# (structural, not "data" a manager should freely rewrite).
USER_EDITABLE_FIELDS = {
    'phone', 'nick', 'avatar_url', 'email', 'vip_level',
    'balance', 'wallet', 'total_deposit', 'total_withdraw',
    'ai_income', 'today_earnings', 'team_income',
    'invite_count', 'team_count', 'raffle_ready',
    'last_salary', 'this_salary', 'depositing_invites',
}
NUMERIC_USER_FIELDS = {
    'vip_level', 'balance', 'wallet', 'total_deposit', 'total_withdraw',
    'ai_income', 'today_earnings', 'team_income', 'invite_count',
    'team_count', 'raffle_ready', 'last_salary', 'this_salary', 'depositing_invites',
}

MACHINE_EDITABLE_FIELDS = {'series', 'price', 'income', 'lock', 'image_url', 'sold'}
NUMERIC_MACHINE_FIELDS = {'price', 'income', 'lock', 'sold'}

USER_MACHINE_EDITABLE_FIELDS = {'daily_income', 'total_income', 'earned', 'status', 'expires_at'}


# ══════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════

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
        resp.set_cookie(ADMIN_COOKIE_NAME, token, httponly=True, samesite='Lax', max_age=SESSION_MAX_AGE)
        return resp
    finally:
        db.close()

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

@admin_bp.route('/me', methods=['GET'])
@admin_login_required
def admin_me(current_admin):
    return jsonify({'ok': True, 'id': current_admin['id'], 'username': current_admin['username'], 'name': current_admin['name']})

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


# ══════════════════════════════════════════════════════════════════════════
# MANAGERS — any logged-in manager can create/list/remove other managers.
# There's no separate "super-admin" tier: whoever has the seeded/first
# account can bootstrap the rest, then it's peer-to-peer from there.
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/admins', methods=['GET'])
@admin_login_required
def list_admins(current_admin):
    db = get_db()
    try:
        rows = db.execute("SELECT id, username, name, created_at FROM admins ORDER BY created_at ASC").fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d') if r['created_at'] else None
            r['is_you'] = (r['id'] == current_admin['id'])
        return jsonify({'ok': True, 'admins': rows})
    finally:
        db.close()

@admin_bp.route('/admins', methods=['POST'])
@admin_login_required
def create_admin(current_admin):
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    name     = (data.get('name') or 'Manager').strip()

    if not username or not password:
        return jsonify({'ok': False, 'error': 'Username and password are required'})
    if len(password) < 8:
        return jsonify({'ok': False, 'error': 'Password must be at least 8 characters'})

    db = get_db()
    try:
        existing = db.execute("SELECT id FROM admins WHERE username=?", (username,)).fetchone()
        if existing:
            return jsonify({'ok': False, 'error': 'That username is already taken'})

        db.execute(
            "INSERT INTO admins (username, password, name) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), name)
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

@admin_bp.route('/admins/<int:admin_id>', methods=['DELETE'])
@admin_login_required
def delete_admin(current_admin, admin_id):
    if admin_id == current_admin['id']:
        return jsonify({'ok': False, 'error': "You can't remove your own account while logged in as it"})

    db = get_db()
    try:
        count = db.execute("SELECT COUNT(*) AS c FROM admins").fetchone()['c']
        if count <= 1:
            return jsonify({'ok': False, 'error': 'Cannot remove the last remaining manager account'})

        db.execute("DELETE FROM admin_sessions WHERE admin_id=?", (admin_id,))
        result = db.execute("DELETE FROM admins WHERE id=?", (admin_id,))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Manager not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# DASHBOARD STATS
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/stats', methods=['GET'])
@admin_login_required
def admin_stats(current_admin):
    db = get_db()
    try:
        users_count       = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()['c']
        total_deposits     = db.execute("SELECT COALESCE(SUM(amount),0) AS s FROM deposit_transactions WHERE status='successful'").fetchone()['s']
        total_withdrawn    = db.execute("SELECT COALESCE(SUM(amount),0) AS s FROM withdraw_requests WHERE status='approved'").fetchone()['s']
        pending_withdraws  = db.execute("SELECT COUNT(*) AS c FROM withdraw_requests WHERE status='pending'").fetchone()['c']
        unread_chats       = db.execute("""
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


# ══════════════════════════════════════════════════════════════════════════
# USERS — search/list, full detail (+ their machines/transactions), generic
# field edit, balance adjustment (ledgered), password reset, delete.
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/users', methods=['GET'])
@admin_login_required
def admin_users(current_admin):
    search = (request.args.get('search') or '').strip()
    limit  = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    db = get_db()
    try:
        base = """SELECT id, phone, nick, balance, wallet, total_deposit, total_withdraw,
                          ai_income, invite_count, vip_level, created_at
                   FROM users"""
        if search:
            rows = db.execute(
                base + " WHERE phone ILIKE ? OR nick ILIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (f'%{search}%', f'%{search}%', limit, offset)
            ).fetchall()
        else:
            rows = db.execute(base + " ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()

        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d') if r['created_at'] else None
        return jsonify({'ok': True, 'users': rows})
    finally:
        db.close()

@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@admin_login_required
def admin_user_detail(current_admin, user_id):
    db = get_db()
    try:
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return jsonify({'ok': False, 'error': 'User not found'})
        user['created_at'] = user['created_at'].strftime('%Y-%m-%d %H:%M') if user['created_at'] else None

        machines = db.execute("""
            SELECT um.*, m.series FROM user_machines um
            JOIN machines m ON m.id = um.machine_id
            WHERE um.user_id=? ORDER BY um.bought_at DESC
        """, (user_id,)).fetchall()
        for m in machines:
            m['bought_at']  = m['bought_at'].strftime('%Y-%m-%d %H:%M') if m['bought_at'] else None
            m['expires_at'] = m['expires_at'].strftime('%Y-%m-%d %H:%M') if m['expires_at'] else None

        transactions = db.execute("""
            SELECT id, type, amount, note, created_at FROM transactions
            WHERE user_id=? ORDER BY created_at DESC LIMIT 50
        """, (user_id,)).fetchall()
        for t in transactions:
            t['created_at'] = t['created_at'].strftime('%Y-%m-%d %H:%M') if t['created_at'] else None

        return jsonify({'ok': True, 'user': user, 'machines': machines, 'transactions': transactions})
    finally:
        db.close()

@admin_bp.route('/users/<int:user_id>', methods=['PATCH'])
@admin_login_required
def admin_update_user(current_admin, user_id):
    """Generic field editor covering every directly-editable users column."""
    data = request.get_json() or {}
    updates = {k: v for k, v in data.items() if k in USER_EDITABLE_FIELDS}
    if not updates:
        return jsonify({'ok': False, 'error': 'No editable fields provided'})

    for k in updates:
        if k in NUMERIC_USER_FIELDS:
            try:
                updates[k] = float(updates[k])
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'error': f'"{k}" must be a number'})

    set_clause = ', '.join(f"{k}=?" for k in updates)
    params = list(updates.values()) + [user_id]

    db = get_db()
    try:
        try:
            result = db.execute(f"UPDATE users SET {set_clause} WHERE id=?", tuple(params))
        except Exception as e:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Update failed — check for a duplicate phone number'})
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'User not found'})

        # Keep vip_level consistent with depositing_invites unless the
        # manager explicitly overrode vip_level in this same request.
        if 'depositing_invites' in updates and 'vip_level' not in updates:
            recompute_vip_level(db, user_id)

        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

@admin_bp.route('/users/<int:user_id>/adjust-balance', methods=['POST'])
@admin_login_required
def admin_adjust_balance(current_admin, user_id):
    """
    Preferred way to change a user's balance: goes through the ledger
    (transactions table) so there's a record of who adjusted what and why,
    instead of silently overwriting the number via the generic PATCH above.
    amount can be positive (credit) or negative (debit).
    """
    data = request.get_json() or {}
    try:
        amount = float(data.get('amount'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Enter a valid amount (use a negative number to debit)'})
    note = (data.get('note') or '').strip() or f'Manual adjustment by {current_admin["username"]}'

    if amount == 0:
        return jsonify({'ok': False, 'error': 'Amount cannot be zero'})

    db = get_db()
    try:
        result = db.execute("UPDATE users SET balance=balance+? WHERE id=?", (amount, user_id))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'User not found'})
        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?, 'admin_adjustment', ?, ?)",
            (user_id, amount, note)
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

@admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@admin_login_required
def admin_reset_password(current_admin, user_id):
    data = request.get_json() or {}
    new_password = (data.get('new_password') or '').strip()
    if len(new_password) < 6:
        return jsonify({'ok': False, 'error': 'Password must be at least 6 characters'})

    db = get_db()
    try:
        result = db.execute("UPDATE users SET password=? WHERE id=?",
                             (generate_password_hash(new_password), user_id))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'User not found'})
        # Log out all of this user's existing sessions so the new password
        # takes effect immediately rather than only on their next login.
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@admin_login_required
def admin_delete_user(current_admin, user_id):
    """
    Full delete, cascading manually since the schema doesn't use
    ON DELETE CASCADE. Destructive and irreversible — the frontend should
    make the manager confirm this explicitly before calling it.
    """
    db = get_db()
    try:
        user = db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return jsonify({'ok': False, 'error': 'User not found'})

        # Anyone this user referred keeps existing, just loses the referral link.
        db.execute("UPDATE users SET invited_by=NULL WHERE invited_by=?", (user_id,))

        for table in ['sessions', 'chat_messages', 'transactions', 'user_machines',
                      'raffle_records', 'deposit_transactions', 'withdraw_requests']:
            db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))

        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        db.commit()
        return jsonify({'ok': True})
    except Exception:
        db.rollback()
        return jsonify({'ok': False, 'error': 'Delete failed — user may still be referenced elsewhere'})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# MACHINES CATALOGUE — the products users buy, not individual purchases.
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/machines', methods=['GET'])
@admin_login_required
def admin_list_machines(current_admin):
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM machines ORDER BY series, price").fetchall()
        return jsonify({'ok': True, 'machines': rows})
    finally:
        db.close()

@admin_bp.route('/machines', methods=['POST'])
@admin_login_required
def admin_create_machine(current_admin):
    data = request.get_json() or {}
    machine_id = (data.get('id') or '').strip()
    if not machine_id:
        return jsonify({'ok': False, 'error': 'Machine ID is required (e.g. "A4")'})
    try:
        price  = float(data.get('price', 0))
        income = float(data.get('income', 0))
        lock   = int(data.get('lock', 30))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Price, income, and lock must be numbers'})

    db = get_db()
    try:
        try:
            db.execute(
                "INSERT INTO machines (id, series, price, income, lock, image_url, sold) VALUES (?,?,?,?,?,?,?)",
                (machine_id, data.get('series', 'A'), price, income, lock,
                 data.get('image_url'), int(bool(data.get('sold', False))))
            )
        except Exception:
            db.rollback()
            return jsonify({'ok': False, 'error': f'Machine ID "{machine_id}" already exists'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

@admin_bp.route('/machines/<machine_id>', methods=['PATCH'])
@admin_login_required
def admin_update_machine(current_admin, machine_id):
    data = request.get_json() or {}
    updates = {k: v for k, v in data.items() if k in MACHINE_EDITABLE_FIELDS}
    if not updates:
        return jsonify({'ok': False, 'error': 'No editable fields provided'})

    for k in updates:
        if k in NUMERIC_MACHINE_FIELDS:
            try:
                updates[k] = float(updates[k]) if k != 'lock' and k != 'sold' else int(float(updates[k]))
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'error': f'"{k}" must be a number'})

    set_clause = ', '.join(f"{k}=?" for k in updates)
    params = list(updates.values()) + [machine_id]

    db = get_db()
    try:
        result = db.execute(f"UPDATE machines SET {set_clause} WHERE id=?", tuple(params))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Machine not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

@admin_bp.route('/machines/<machine_id>', methods=['DELETE'])
@admin_login_required
def admin_delete_machine(current_admin, machine_id):
    db = get_db()
    try:
        try:
            result = db.execute("DELETE FROM machines WHERE id=?", (machine_id,))
        except Exception:
            db.rollback()
            return jsonify({'ok': False, 'error':
                'Cannot delete — users already own this machine. Set "Sold" instead to retire it from the shop.'})
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Machine not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# USER-OWNED MACHINES — an individual purchase (edit its accrual state).
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/user-machines/<int:um_id>', methods=['PATCH'])
@admin_login_required
def admin_update_user_machine(current_admin, um_id):
    data = request.get_json() or {}
    updates = {k: v for k, v in data.items() if k in USER_MACHINE_EDITABLE_FIELDS}
    if not updates:
        return jsonify({'ok': False, 'error': 'No editable fields provided'})

    for k in ('daily_income', 'total_income', 'earned'):
        if k in updates:
            try:
                updates[k] = float(updates[k])
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'error': f'"{k}" must be a number'})
    if 'status' in updates and updates['status'] not in ('running', 'expired'):
        return jsonify({'ok': False, 'error': 'Status must be "running" or "expired"'})

    set_clause = ', '.join(f"{k}=?" for k in updates)
    params = list(updates.values()) + [um_id]

    db = get_db()
    try:
        result = db.execute(f"UPDATE user_machines SET {set_clause} WHERE id=?", tuple(params))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Purchase not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# WITHDRAWALS
# ══════════════════════════════════════════════════════════════════════════

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

@admin_bp.route('/withdrawals/<int:withdraw_id>/reject', methods=['POST'])
@admin_login_required
def reject_withdrawal(current_admin, withdraw_id):
    db = get_db()
    try:
        w = db.execute("SELECT * FROM withdraw_requests WHERE id=? AND status='pending'", (withdraw_id,)).fetchone()
        if not w:
            return jsonify({'ok': False, 'error': 'Withdrawal not found or already processed'})

        db.execute("UPDATE withdraw_requests SET status='rejected', processed_at=NOW() WHERE id=?", (withdraw_id,))
        db.execute("UPDATE users SET balance=balance+?, total_withdraw=total_withdraw-? WHERE id=?",
                   (w['amount'], w['amount'], w['user_id']))
        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?,?,?,?)",
            (w['user_id'], 'withdraw_refund', w['amount'], 'Withdrawal rejected by manager — refunded')
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# DEPOSITS — view + status correction. Amount is intentionally read-only
# here (editing it after the fact wouldn't retroactively fix the balance
# that was already credited); use adjust-balance on the user for that.
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/deposits', methods=['GET'])
@admin_login_required
def admin_deposits(current_admin):
    status = request.args.get('status', '')
    db = get_db()
    try:
        if status:
            rows = db.execute("""
                SELECT d.*, u.phone, u.nick FROM deposit_transactions d
                JOIN users u ON u.id = d.user_id
                WHERE d.status=? ORDER BY d.created_at DESC LIMIT 100
            """, (status,)).fetchall()
        else:
            rows = db.execute("""
                SELECT d.*, u.phone, u.nick FROM deposit_transactions d
                JOIN users u ON u.id = d.user_id
                ORDER BY d.created_at DESC LIMIT 100
            """).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
            r['verified_at'] = r['verified_at'].strftime('%Y-%m-%d %H:%M') if r['verified_at'] else None
        return jsonify({'ok': True, 'deposits': rows})
    finally:
        db.close()

@admin_bp.route('/deposits/<int:deposit_id>/status', methods=['PATCH'])
@admin_login_required
def admin_update_deposit_status(current_admin, deposit_id):
    data = request.get_json() or {}
    status = data.get('status')
    if status not in ('pending', 'successful', 'failed'):
        return jsonify({'ok': False, 'error': 'Status must be pending, successful, or failed'})

    db = get_db()
    try:
        result = db.execute("UPDATE deposit_transactions SET status=? WHERE id=?", (status, deposit_id))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Deposit not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# ANNOUNCEMENTS (the 'messages' table — site-wide banners, not chat)
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/announcements', methods=['GET'])
@admin_login_required
def admin_list_announcements(current_admin):
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM messages ORDER BY created_at DESC").fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
        return jsonify({'ok': True, 'announcements': rows})
    finally:
        db.close()

@admin_bp.route('/announcements', methods=['POST'])
@admin_login_required
def admin_create_announcement(current_admin):
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'ok': False, 'error': 'Announcement text is required'})

    db = get_db()
    try:
        db.execute("INSERT INTO messages (text) VALUES (?)", (text,))
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

@admin_bp.route('/announcements/<int:msg_id>', methods=['PATCH'])
@admin_login_required
def admin_update_announcement(current_admin, msg_id):
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'ok': False, 'error': 'Announcement text is required'})

    db = get_db()
    try:
        result = db.execute("UPDATE messages SET text=? WHERE id=?", (text, msg_id))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Announcement not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

@admin_bp.route('/announcements/<int:msg_id>', methods=['DELETE'])
@admin_login_required
def admin_delete_announcement(current_admin, msg_id):
    db = get_db()
    try:
        result = db.execute("DELETE FROM messages WHERE id=?", (msg_id,))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Announcement not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# REWARD CODES — manager-created, redeemable by any number of different
# users (once each), only within the validity window set at creation.
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/reward-codes', methods=['GET'])
@admin_login_required
def admin_list_reward_codes(current_admin):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT rc.*, COUNT(rr.id) AS redemption_count
            FROM reward_codes rc
            LEFT JOIN reward_redemptions rr ON rr.code_id = rc.id
            GROUP BY rc.id
            ORDER BY rc.created_at DESC
        """).fetchall()
        for r in rows:
            r['valid_from']  = r['valid_from'].strftime('%Y-%m-%dT%H:%M') if r['valid_from'] else None
            r['valid_until'] = r['valid_until'].strftime('%Y-%m-%dT%H:%M') if r['valid_until'] else None
            r['created_at']  = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
        return jsonify({'ok': True, 'reward_codes': rows})
    finally:
        db.close()

@admin_bp.route('/reward-codes', methods=['POST'])
@admin_login_required
def admin_create_reward_code(current_admin):
    import secrets as _secrets
    data = request.get_json() or {}
    code = (data.get('code') or '').strip().upper() or _secrets.token_hex(4).upper()

    try:
        amount = float(data.get('amount'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Enter a valid reward amount'})
    if amount <= 0:
        return jsonify({'ok': False, 'error': 'Amount must be greater than zero'})

    valid_from  = data.get('valid_from')
    valid_until = data.get('valid_until')
    if not valid_from or not valid_until:
        return jsonify({'ok': False, 'error': 'Set both a start and end time for this code'})

    db = get_db()
    try:
        try:
            db.execute("""
                INSERT INTO reward_codes (code, amount, description, valid_from, valid_until, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (code, amount, (data.get('description') or '').strip(), valid_from, valid_until, current_admin['id']))
        except Exception:
            db.rollback()
            return jsonify({'ok': False, 'error': f'Code "{code}" already exists — try another'})
        db.commit()
        return jsonify({'ok': True, 'code': code})
    finally:
        db.close()

@admin_bp.route('/reward-codes/<int:code_id>', methods=['PATCH'])
@admin_login_required
def admin_update_reward_code(current_admin, code_id):
    data = request.get_json() or {}
    fields = {}
    if 'amount' in data:
        try:
            fields['amount'] = float(data['amount'])
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Amount must be a number'})
    if 'description' in data:
        fields['description'] = (data.get('description') or '').strip()
    if 'valid_from' in data:
        fields['valid_from'] = data['valid_from']
    if 'valid_until' in data:
        fields['valid_until'] = data['valid_until']
    if 'active' in data:
        fields['active'] = bool(data['active'])

    if not fields:
        return jsonify({'ok': False, 'error': 'No fields to update'})

    set_clause = ', '.join(f"{k}=?" for k in fields)
    params = list(fields.values()) + [code_id]

    db = get_db()
    try:
        result = db.execute(f"UPDATE reward_codes SET {set_clause} WHERE id=?", tuple(params))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Reward code not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()

@admin_bp.route('/reward-codes/<int:code_id>', methods=['DELETE'])
@admin_login_required
def admin_delete_reward_code(current_admin, code_id):
    db = get_db()
    try:
        db.execute("DELETE FROM reward_redemptions WHERE code_id=?", (code_id,))
        result = db.execute("DELETE FROM reward_codes WHERE id=?", (code_id,))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Reward code not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()



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

        db.execute("UPDATE chat_messages SET read_by_admin=TRUE WHERE user_id=? AND sender='user'", (user_id,))
        db.commit()

        return jsonify({'ok': True, 'messages': rows})
    finally:
        db.close()

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
