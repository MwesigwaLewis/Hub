import secrets
from flask import Blueprint, request, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from db.database import get_db
from db.vip import recompute_vip_level
from middleware.admin_auth import admin_login_required, ADMIN_COOKIE_NAME

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log_action(db, admin, action, detail=None):
    """Silently record every non-super admin action. No-op for super."""
    if admin['role'] == 'super':
        return
    try:
        db.execute(
            "INSERT INTO admin_activity_log (admin_id, action, detail) VALUES (?,?,?)",
            (admin['id'], action, detail)
        )
    except Exception:
        pass  # never let logging crash the actual request


def _admin_can_access_user(db, current_admin, user_id):
    if current_admin['role'] == 'super':
        return True
    if current_admin.get('can_see_all'):
        return True
    row = db.execute("SELECT assigned_manager_id FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row) and row['assigned_manager_id'] == current_admin['id']


def _scope_denied():
    return jsonify({'ok': False, 'error': "This user isn't in your batch"}), 403


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
        try:
            db.execute("DELETE FROM admin_sessions WHERE token=?", (token,))
            db.commit()
        finally:
            db.close()
    resp = make_response(jsonify({'ok': True}))
    resp.delete_cookie(ADMIN_COOKIE_NAME)
    return resp


@admin_bp.route('/me', methods=['GET'])
@admin_login_required
def admin_me(current_admin):
    return jsonify({
        'ok': True,
        'id': current_admin['id'],
        'username': current_admin['username'],
        'name': current_admin['name'],
        'role': current_admin['role'],
        'avatar_url': current_admin.get('avatar_url'),
        'is_super': current_admin['role'] == 'super',
        'manager_code': current_admin['manager_code'],
        'can_see_all': bool(current_admin.get('can_see_all')),
    })


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
        _log_action(db, current_admin, 'change_password')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/update-profile', methods=['PATCH'])
@admin_login_required
def admin_update_profile(current_admin):
    """Any manager can update their own name and avatar."""
    data = request.get_json() or {}
    fields = {}
    if 'name' in data:
        fields['name'] = (data['name'] or '').strip()
    if 'avatar_url' in data:
        fields['avatar_url'] = (data['avatar_url'] or '').strip() or None
    if not fields:
        return jsonify({'ok': False, 'error': 'Nothing to update'})
    set_clause = ', '.join(f"{k}=?" for k in fields)
    db = get_db()
    try:
        db.execute(f"UPDATE admins SET {set_clause} WHERE id=?",
                   tuple(fields.values()) + (current_admin['id'],))
        _log_action(db, current_admin, 'update_profile')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# MANAGERS — SUPER ONLY
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/admins', methods=['GET'])
@admin_login_required
def list_admins(current_admin):
    """Only super can list all managers."""
    if current_admin['role'] != 'super':
        return jsonify({'ok': False, 'error': 'Only the main manager can view the manager list'}), 403
    db = get_db()
    try:
        rows = db.execute("""
            SELECT a.id, a.username, a.name, a.role, a.manager_code, a.avatar_url,
                   a.can_see_all, a.created_at,
                   (SELECT COUNT(*) FROM users u WHERE u.assigned_manager_id = a.id) AS batch_size
            FROM admins a ORDER BY a.created_at ASC
        """).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d') if r['created_at'] else None
            r['is_you'] = (r['id'] == current_admin['id'])
        return jsonify({'ok': True, 'admins': rows})
    finally:
        db.close()


@admin_bp.route('/admins', methods=['POST'])
@admin_login_required
def create_admin(current_admin):
    """Only super can create managers."""
    if current_admin['role'] != 'super':
        return jsonify({'ok': False, 'error': 'Only the main manager can create managers'}), 403
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    name     = (data.get('name') or 'Manager').strip()
    # Only super can grant super — and even then, it's intentionally blocked
    # to prevent super proliferation; only one super (the seeded one) should exist.
    role = 'manager'
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
            "INSERT INTO admins (username, password, name, role, manager_code) VALUES (?,?,?,?,?)",
            (username, generate_password_hash(password), name, role, secrets.token_hex(4).upper())
        )
        _log_action(db, current_admin, 'create_manager', f'username={username}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/admins/<int:admin_id>', methods=['DELETE'])
@admin_login_required
def delete_admin(current_admin, admin_id):
    """Only super can delete managers."""
    if current_admin['role'] != 'super':
        return jsonify({'ok': False, 'error': 'Only the main manager can remove managers'}), 403
    if admin_id == current_admin['id']:
        return jsonify({'ok': False, 'error': "You can't remove your own account while logged in as it"})
    db = get_db()
    try:
        target = db.execute("SELECT role FROM admins WHERE id=?", (admin_id,)).fetchone()
        if not target:
            return jsonify({'ok': False, 'error': 'Manager not found'})

        # Re-assign orphaned users to the least-loaded remaining manager
        db.execute("""
            UPDATE users SET assigned_manager_id = (
                SELECT a.id FROM admins a
                LEFT JOIN users u ON u.assigned_manager_id = a.id
                WHERE a.id != ? AND a.role = 'manager'
                GROUP BY a.id ORDER BY COUNT(u.id) ASC, a.id ASC LIMIT 1
            )
            WHERE assigned_manager_id = ?
        """, (admin_id, admin_id))

        db.execute("DELETE FROM admin_sessions WHERE admin_id=?", (admin_id,))
        db.execute("UPDATE reward_codes SET created_by=NULL WHERE created_by=?", (admin_id,))
        result = db.execute("DELETE FROM admins WHERE id=?", (admin_id,))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Manager not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/admins/<int:admin_id>/visibility', methods=['PATCH'])
@admin_login_required
def set_manager_visibility(current_admin, admin_id):
    """Super can toggle whether a manager can see all users."""
    if current_admin['role'] != 'super':
        return jsonify({'ok': False, 'error': 'Only the main manager can change visibility settings'}), 403
    data = request.get_json() or {}
    can_see_all = bool(data.get('can_see_all', False))
    db = get_db()
    try:
        result = db.execute("UPDATE admins SET can_see_all=? WHERE id=?", (can_see_all, admin_id))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Manager not found'})
        _log_action(db, current_admin, 'set_manager_visibility', f'admin_id={admin_id} can_see_all={can_see_all}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/admins/visibility/all', methods=['PATCH'])
@admin_login_required
def set_all_managers_visibility(current_admin):
    """Super can turn 'view all members' on/off for every manager at once,
    instead of clicking through each one individually."""
    if current_admin['role'] != 'super':
        return jsonify({'ok': False, 'error': 'Only the main manager can change visibility settings'}), 403
    data = request.get_json() or {}
    can_see_all = bool(data.get('can_see_all', False))
    db = get_db()
    try:
        db.execute("UPDATE admins SET can_see_all=? WHERE role='manager'", (can_see_all,))
        _log_action(db, current_admin, 'set_all_managers_visibility', f'can_see_all={can_see_all}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/activity-log', methods=['GET'])
@admin_login_required
def admin_activity_log(current_admin):
    """Super-only: view all non-super admin actions."""
    if current_admin['role'] != 'super':
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    limit  = min(int(request.args.get('limit', 100)), 500)
    offset = int(request.args.get('offset', 0))
    db = get_db()
    try:
        rows = db.execute("""
            SELECT l.id, l.action, l.detail, l.created_at,
                   a.username, a.name
            FROM admin_activity_log l
            JOIN admins a ON a.id = l.admin_id
            ORDER BY l.created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        total = db.execute("SELECT COUNT(*) AS c FROM admin_activity_log").fetchone()['c']
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
        return jsonify({'ok': True, 'logs': rows, 'total': total, 'has_more': offset + len(rows) < total})
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
        is_super   = current_admin['role'] == 'super'
        can_see_all = bool(current_admin.get('can_see_all'))
        mgr_id     = current_admin['id']

        if is_super or can_see_all:
            users_count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()['c']
            total_deposits = db.execute("SELECT COALESCE(SUM(amount),0) AS s FROM deposit_transactions WHERE status='successful'").fetchone()['s']
            total_withdrawn = db.execute("SELECT COALESCE(SUM(amount),0) AS s FROM withdraw_requests WHERE status='approved'").fetchone()['s']
            pending_withdraws = db.execute("SELECT COUNT(*) AS c FROM withdraw_requests WHERE status='pending'").fetchone()['c']
            unread_chats = db.execute("""
                SELECT COUNT(DISTINCT user_id) AS c FROM chat_messages
                WHERE sender='user' AND read_by_admin=FALSE
            """).fetchone()['c']
        else:
            users_count = db.execute(
                "SELECT COUNT(*) AS c FROM users WHERE assigned_manager_id=?", (mgr_id,)).fetchone()['c']
            total_deposits = db.execute("""
                SELECT COALESCE(SUM(d.amount),0) AS s FROM deposit_transactions d
                JOIN users u ON u.id = d.user_id
                WHERE d.status='successful' AND u.assigned_manager_id=?
            """, (mgr_id,)).fetchone()['s']
            total_withdrawn = db.execute("""
                SELECT COALESCE(SUM(w.amount),0) AS s FROM withdraw_requests w
                JOIN users u ON u.id = w.user_id
                WHERE w.status='approved' AND u.assigned_manager_id=?
            """, (mgr_id,)).fetchone()['s']
            pending_withdraws = db.execute("""
                SELECT COUNT(*) AS c FROM withdraw_requests w
                JOIN users u ON u.id = w.user_id
                WHERE w.status='pending' AND u.assigned_manager_id=?
            """, (mgr_id,)).fetchone()['c']
            unread_chats = db.execute("""
                SELECT COUNT(DISTINCT cm.user_id) AS c FROM chat_messages cm
                JOIN users u ON u.id = cm.user_id
                WHERE cm.sender='user' AND cm.read_by_admin=FALSE AND u.assigned_manager_id=?
            """, (mgr_id,)).fetchone()['c']

        return jsonify({
            'ok': True,
            'is_super': is_super,
            'users_count': users_count,
            'total_deposits': total_deposits,
            'total_withdrawn': total_withdrawn,
            'pending_withdraws': pending_withdraws,
            'unread_chats': unread_chats,
        })
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/users', methods=['GET'])
@admin_login_required
def admin_users(current_admin):
    search         = (request.args.get('search') or '').strip()
    limit          = min(int(request.args.get('limit', 50)), 200)
    offset         = int(request.args.get('offset', 0))
    manager_filter = request.args.get('manager', '').strip()

    db = get_db()
    try:
        base = """SELECT id, phone, nick, avatar_url, balance, wallet, total_deposit, total_withdraw,
                          ai_income, invite_count, vip_level, assigned_manager_id, created_at
                   FROM users"""
        where, params = [], []

        if search:
            where.append("(phone ILIKE ? OR nick ILIKE ?)")
            params += [f'%{search}%', f'%{search}%']

        is_super    = current_admin['role'] == 'super'
        can_see_all = bool(current_admin.get('can_see_all'))

        if is_super:
            if manager_filter == 'unassigned':
                where.append("assigned_manager_id IS NULL")
            elif manager_filter:
                where.append("assigned_manager_id = ?")
                params.append(int(manager_filter))
        elif can_see_all:
            pass  # sees everyone, no filter
        else:
            where.append("assigned_manager_id = ?")
            params.append(current_admin['id'])

        count_query = "SELECT COUNT(*) AS c FROM users" + (" WHERE " + " AND ".join(where) if where else "")
        total = db.execute(count_query, tuple(params)).fetchone()['c']

        query = base + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        rows = db.execute(query, tuple(params + [limit, offset])).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d') if r['created_at'] else None
        return jsonify({'ok': True, 'users': rows, 'total': total, 'has_more': offset + len(rows) < total})
    finally:
        db.close()


@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@admin_login_required
def admin_user_detail(current_admin, user_id):
    db = get_db()
    try:
        if not _admin_can_access_user(db, current_admin, user_id):
            return _scope_denied()
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
    data    = request.get_json() or {}
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
    params     = list(updates.values()) + [user_id]
    db = get_db()
    try:
        if not _admin_can_access_user(db, current_admin, user_id):
            return _scope_denied()
        try:
            result = db.execute(f"UPDATE users SET {set_clause} WHERE id=?", tuple(params))
        except Exception:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Update failed — check for a duplicate phone number'})
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'User not found'})
        if 'depositing_invites' in updates and 'vip_level' not in updates:
            recompute_vip_level(db, user_id)
        _log_action(db, current_admin, 'update_user', f'user_id={user_id} fields={list(updates.keys())}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/users/<int:user_id>/reassign', methods=['POST'])
@admin_login_required
def admin_reassign_user(current_admin, user_id):
    if current_admin['role'] != 'super':
        return jsonify({'ok': False, 'error': 'Only the main manager can reassign users'}), 403
    data           = request.get_json() or {}
    new_manager_id = data.get('manager_id')
    if new_manager_id is None:
        return jsonify({'ok': False, 'error': 'manager_id is required — every user must have a manager'})
    db = get_db()
    try:
        mgr = db.execute("SELECT id FROM admins WHERE id=?", (new_manager_id,)).fetchone()
        if not mgr:
            return jsonify({'ok': False, 'error': 'Manager not found'})
        result = db.execute("UPDATE users SET assigned_manager_id=? WHERE id=?", (new_manager_id, user_id))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'User not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/users/<int:user_id>/adjust-balance', methods=['POST'])
@admin_login_required
def admin_adjust_balance(current_admin, user_id):
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
        if not _admin_can_access_user(db, current_admin, user_id):
            return _scope_denied()
        result = db.execute("UPDATE users SET balance=balance+? WHERE id=?", (amount, user_id))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'User not found'})
        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?, 'admin_adjustment', ?, ?)",
            (user_id, amount, note)
        )
        _log_action(db, current_admin, 'adjust_balance', f'user_id={user_id} amount={amount}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@admin_login_required
def admin_reset_password(current_admin, user_id):
    data         = request.get_json() or {}
    new_password = (data.get('new_password') or '').strip()
    if len(new_password) < 6:
        return jsonify({'ok': False, 'error': 'Password must be at least 6 characters'})
    db = get_db()
    try:
        if not _admin_can_access_user(db, current_admin, user_id):
            return _scope_denied()
        result = db.execute("UPDATE users SET password=? WHERE id=?",
                             (generate_password_hash(new_password), user_id))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'User not found'})
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        _log_action(db, current_admin, 'reset_password', f'user_id={user_id}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@admin_login_required
def admin_delete_user(current_admin, user_id):
    db = get_db()
    try:
        if not _admin_can_access_user(db, current_admin, user_id):
            return _scope_denied()
        user = db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return jsonify({'ok': False, 'error': 'User not found'})
        db.execute("UPDATE users SET invited_by=NULL WHERE invited_by=?", (user_id,))
        for table in ['sessions', 'chat_messages', 'transactions', 'user_machines',
                      'raffle_records', 'deposit_transactions', 'withdraw_requests']:
            db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        _log_action(db, current_admin, 'delete_user', f'user_id={user_id}')
        db.commit()
        return jsonify({'ok': True})
    except Exception:
        db.rollback()
        return jsonify({'ok': False, 'error': 'Delete failed — user may still be referenced elsewhere'})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# MACHINES
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/machines', methods=['GET'])
@admin_login_required
def admin_list_machines(current_admin):
    limit  = min(int(request.args.get('limit', 30)), 200)
    offset = int(request.args.get('offset', 0))
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) AS c FROM machines").fetchone()['c']
        rows = db.execute("SELECT * FROM machines ORDER BY series, price LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return jsonify({'ok': True, 'machines': rows, 'total': total, 'has_more': offset + len(rows) < total})
    finally:
        db.close()


@admin_bp.route('/machines', methods=['POST'])
@admin_login_required
def admin_create_machine(current_admin):
    data       = request.get_json() or {}
    machine_id = (data.get('id') or '').strip()
    if not machine_id:
        return jsonify({'ok': False, 'error': 'Machine ID is required'})
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
        _log_action(db, current_admin, 'create_machine', f'machine_id={machine_id}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


@admin_bp.route('/machines/<machine_id>', methods=['PATCH'])
@admin_login_required
def admin_update_machine(current_admin, machine_id):
    data    = request.get_json() or {}
    updates = {k: v for k, v in data.items() if k in MACHINE_EDITABLE_FIELDS}
    if not updates:
        return jsonify({'ok': False, 'error': 'No editable fields provided'})
    for k in updates:
        if k in NUMERIC_MACHINE_FIELDS:
            try:
                updates[k] = float(updates[k]) if k not in ('lock', 'sold') else int(float(updates[k]))
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'error': f'"{k}" must be a number'})
    set_clause = ', '.join(f"{k}=?" for k in updates)
    params     = list(updates.values()) + [machine_id]
    db = get_db()
    try:
        result = db.execute(f"UPDATE machines SET {set_clause} WHERE id=?", tuple(params))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Machine not found'})
        _log_action(db, current_admin, 'update_machine', f'machine_id={machine_id}')
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
            return jsonify({'ok': False, 'error': 'Cannot delete — users already own this machine. Set "Sold" instead.'})
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Machine not found'})
        _log_action(db, current_admin, 'delete_machine', f'machine_id={machine_id}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# USER-OWNED MACHINES
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/user-machines/<int:um_id>', methods=['PATCH'])
@admin_login_required
def admin_update_user_machine(current_admin, um_id):
    data    = request.get_json() or {}
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
    params     = list(updates.values()) + [um_id]
    db = get_db()
    try:
        owner = db.execute("SELECT user_id FROM user_machines WHERE id=?", (um_id,)).fetchone()
        if not owner:
            return jsonify({'ok': False, 'error': 'Purchase not found'})
        if not _admin_can_access_user(db, current_admin, owner['user_id']):
            return _scope_denied()
        result = db.execute(f"UPDATE user_machines SET {set_clause} WHERE id=?", tuple(params))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Purchase not found'})
        _log_action(db, current_admin, 'update_user_machine', f'um_id={um_id}')
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
    limit  = min(int(request.args.get('limit', 30)), 200)
    offset = int(request.args.get('offset', 0))
    db = get_db()
    try:
        is_super    = current_admin['role'] == 'super'
        can_see_all = bool(current_admin.get('can_see_all'))
        if is_super or can_see_all:
            total = db.execute("SELECT COUNT(*) AS c FROM withdraw_requests WHERE status=?", (status,)).fetchone()['c']
            rows = db.execute("""
                SELECT w.id, w.user_id, w.amount, w.status, w.created_at, w.processed_at,
                       u.phone, u.nick
                FROM withdraw_requests w JOIN users u ON u.id = w.user_id
                WHERE w.status = ? ORDER BY w.created_at ASC LIMIT ? OFFSET ?
            """, (status, limit, offset)).fetchall()
        else:
            total = db.execute("""
                SELECT COUNT(*) AS c FROM withdraw_requests w JOIN users u ON u.id=w.user_id
                WHERE w.status=? AND u.assigned_manager_id=?
            """, (status, current_admin['id'])).fetchone()['c']
            rows = db.execute("""
                SELECT w.id, w.user_id, w.amount, w.status, w.created_at, w.processed_at,
                       u.phone, u.nick
                FROM withdraw_requests w JOIN users u ON u.id = w.user_id
                WHERE w.status = ? AND u.assigned_manager_id = ? ORDER BY w.created_at ASC LIMIT ? OFFSET ?
            """, (status, current_admin['id'], limit, offset)).fetchall()
        for r in rows:
            r['created_at']   = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
            r['processed_at'] = r['processed_at'].strftime('%Y-%m-%d %H:%M') if r['processed_at'] else None
        return jsonify({'ok': True, 'withdrawals': rows, 'total': total, 'has_more': offset + len(rows) < total})
    finally:
        db.close()


@admin_bp.route('/withdrawals/<int:withdraw_id>/approve', methods=['POST'])
@admin_login_required
def approve_withdrawal(current_admin, withdraw_id):
    db = get_db()
    try:
        w = db.execute("SELECT user_id FROM withdraw_requests WHERE id=?", (withdraw_id,)).fetchone()
        if not w:
            return jsonify({'ok': False, 'error': 'Withdrawal not found'})
        if not _admin_can_access_user(db, current_admin, w['user_id']):
            return _scope_denied()
        result = db.execute(
            "UPDATE withdraw_requests SET status='approved', processed_at=NOW() WHERE id=? AND status='pending'",
            (withdraw_id,)
        )
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Withdrawal not found or already processed'})
        _log_action(db, current_admin, 'approve_withdrawal', f'withdraw_id={withdraw_id}')
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
        if not _admin_can_access_user(db, current_admin, w['user_id']):
            return _scope_denied()
        db.execute("UPDATE withdraw_requests SET status='rejected', processed_at=NOW() WHERE id=?", (withdraw_id,))
        db.execute("UPDATE users SET balance=balance+?, total_withdraw=total_withdraw-? WHERE id=?",
                   (w['amount'], w['amount'], w['user_id']))
        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?,?,?,?)",
            (w['user_id'], 'withdraw_refund', w['amount'], 'Withdrawal rejected by manager — refunded')
        )
        _log_action(db, current_admin, 'reject_withdrawal', f'withdraw_id={withdraw_id}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# DEPOSITS
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/deposits', methods=['GET'])
@admin_login_required
def admin_deposits(current_admin):
    status = request.args.get('status', '')
    limit  = min(int(request.args.get('limit', 30)), 200)
    offset = int(request.args.get('offset', 0))
    db = get_db()
    try:
        where, params = [], []
        if status:
            where.append("d.status=?")
            params.append(status)
        is_super    = current_admin['role'] == 'super'
        can_see_all = bool(current_admin.get('can_see_all'))
        if not is_super and not can_see_all:
            where.append("u.assigned_manager_id=?")
            params.append(current_admin['id'])
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        total = db.execute(
            f"SELECT COUNT(*) AS c FROM deposit_transactions d JOIN users u ON u.id=d.user_id{where_sql}",
            tuple(params)
        ).fetchone()['c']
        query = """
            SELECT d.*, u.phone, u.nick FROM deposit_transactions d
            JOIN users u ON u.id = d.user_id
        """ + where_sql + " ORDER BY d.created_at DESC LIMIT ? OFFSET ?"
        rows = db.execute(query, tuple(params + [limit, offset])).fetchall()
        for r in rows:
            r['created_at']  = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
            r['verified_at'] = r['verified_at'].strftime('%Y-%m-%d %H:%M') if r['verified_at'] else None
        return jsonify({'ok': True, 'deposits': rows, 'total': total, 'has_more': offset + len(rows) < total})
    finally:
        db.close()


@admin_bp.route('/deposits/<int:deposit_id>/status', methods=['PATCH'])
@admin_login_required
def admin_update_deposit_status(current_admin, deposit_id):
    data   = request.get_json() or {}
    status = data.get('status')
    if status not in ('pending', 'successful', 'failed'):
        return jsonify({'ok': False, 'error': 'Status must be pending, successful, or failed'})
    db = get_db()
    try:
        dep = db.execute("SELECT user_id FROM deposit_transactions WHERE id=?", (deposit_id,)).fetchone()
        if not dep:
            return jsonify({'ok': False, 'error': 'Deposit not found'})
        if not _admin_can_access_user(db, current_admin, dep['user_id']):
            return _scope_denied()
        result = db.execute("UPDATE deposit_transactions SET status=? WHERE id=?", (status, deposit_id))
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Deposit not found'})
        _log_action(db, current_admin, 'update_deposit_status', f'deposit_id={deposit_id} status={status}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# ANNOUNCEMENTS
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/announcements', methods=['GET'])
@admin_login_required
def admin_list_announcements(current_admin):
    limit  = min(int(request.args.get('limit', 20)), 200)
    offset = int(request.args.get('offset', 0))
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) AS c FROM messages").fetchone()['c']
        rows = db.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
        return jsonify({'ok': True, 'announcements': rows, 'total': total, 'has_more': offset + len(rows) < total})
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
        _log_action(db, current_admin, 'create_announcement')
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
# REWARD CODES
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/reward-codes', methods=['GET'])
@admin_login_required
def admin_list_reward_codes(current_admin):
    limit  = min(int(request.args.get('limit', 20)), 200)
    offset = int(request.args.get('offset', 0))
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) AS c FROM reward_codes").fetchone()['c']
        rows = db.execute("""
            SELECT rc.*, COUNT(rr.id) AS redemption_count
            FROM reward_codes rc
            LEFT JOIN reward_redemptions rr ON rr.code_id = rc.id
            GROUP BY rc.id ORDER BY rc.created_at DESC LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        for r in rows:
            r['valid_from']  = r['valid_from'].strftime('%Y-%m-%dT%H:%M') if r['valid_from'] else None
            r['valid_until'] = r['valid_until'].strftime('%Y-%m-%dT%H:%M') if r['valid_until'] else None
            r['created_at']  = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
        return jsonify({'ok': True, 'reward_codes': rows, 'total': total, 'has_more': offset + len(rows) < total})
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
                VALUES (?,?,?,?,?,?)
            """, (code, amount, (data.get('description') or '').strip(), valid_from, valid_until, current_admin['id']))
        except Exception:
            db.rollback()
            return jsonify({'ok': False, 'error': f'Code "{code}" already exists — try another'})
        _log_action(db, current_admin, 'create_reward_code', f'code={code} amount={amount}')
        db.commit()
        return jsonify({'ok': True, 'code': code})
    finally:
        db.close()


@admin_bp.route('/reward-codes/<int:code_id>', methods=['PATCH'])
@admin_login_required
def admin_update_reward_code(current_admin, code_id):
    data   = request.get_json() or {}
    fields = {}
    if 'amount' in data:
        try:
            fields['amount'] = float(data['amount'])
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Amount must be a number'})
    if 'description' in data:
        fields['description'] = (data.get('description') or '').strip()
    if 'valid_from'  in data: fields['valid_from']  = data['valid_from']
    if 'valid_until' in data: fields['valid_until'] = data['valid_until']
    if 'active'      in data: fields['active']      = bool(data['active'])
    if not fields:
        return jsonify({'ok': False, 'error': 'No fields to update'})
    set_clause = ', '.join(f"{k}=?" for k in fields)
    params     = list(fields.values()) + [code_id]
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


# ══════════════════════════════════════════════════════════════════════════
# CHAT — with manager name + avatar in responses
# ══════════════════════════════════════════════════════════════════════════

@admin_bp.route('/chat/conversations', methods=['GET'])
@admin_login_required
def admin_chat_conversations(current_admin):
    limit  = min(int(request.args.get('limit', 20)), 200)
    offset = int(request.args.get('offset', 0))
    db = get_db()
    try:
        is_super    = current_admin['role'] == 'super'
        can_see_all = bool(current_admin.get('can_see_all'))
        scope_sql = "" if (is_super or can_see_all) else "WHERE u.assigned_manager_id = ?"
        scope_params = () if (is_super or can_see_all) else (current_admin['id'],)

        total = db.execute(f"""
            SELECT COUNT(DISTINCT cm.user_id) AS c
            FROM chat_messages cm JOIN users u ON u.id = cm.user_id
            {scope_sql}
        """, scope_params).fetchone()['c']

        rows = db.execute(f"""
            SELECT * FROM (
                SELECT DISTINCT ON (cm.user_id)
                       cm.user_id, u.phone, u.nick, u.avatar_url AS user_avatar,
                       cm.body AS last_message, cm.sender AS last_sender,
                       cm.created_at AS last_at,
                       EXISTS (
                           SELECT 1 FROM chat_messages x
                           WHERE x.user_id = cm.user_id AND x.sender='user' AND x.read_by_admin=FALSE
                       ) AS unread
                FROM chat_messages cm
                JOIN users u ON u.id = cm.user_id
                {scope_sql}
                ORDER BY cm.user_id, cm.created_at DESC
            ) sub
            ORDER BY last_at DESC
            LIMIT ? OFFSET ?
        """, scope_params + (limit, offset)).fetchall()

        for r in rows:
            r['last_at'] = r['last_at'].strftime('%Y-%m-%d %H:%M') if r['last_at'] else None
        return jsonify({'ok': True, 'conversations': rows, 'total': total, 'has_more': offset + len(rows) < total})
    finally:
        db.close()


@admin_bp.route('/chat/<int:user_id>', methods=['GET'])
@admin_login_required
def admin_chat_thread(current_admin, user_id):
    db = get_db()
    try:
        if not _admin_can_access_user(db, current_admin, user_id):
            return _scope_denied()
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
        if not _admin_can_access_user(db, current_admin, user_id):
            return _scope_denied()
        user = db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return jsonify({'ok': False, 'error': 'User not found'})
        db.execute(
            "INSERT INTO chat_messages (user_id, sender, body, read_by_admin) VALUES (?, 'admin', ?, TRUE)",
            (user_id, body)
        )
        _log_action(db, current_admin, 'chat_send', f'user_id={user_id}')
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()
