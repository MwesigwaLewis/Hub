from flask import Blueprint, request, jsonify
from db.database import get_db
from middleware.auth import login_required
from middleware.admin_auth import admin_login_required

chat_bp = Blueprint('chat', __name__, url_prefix='/api/chat')


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_manager(db, manager_id):
    """Return the admin row for a manager id, or None."""
    return db.execute("SELECT * FROM admins WHERE id=?", (manager_id,)).fetchone()


def _group_name(mgr):
    """Return the display name for a manager's group channel."""
    if mgr and mgr.get('group_name') and mgr['group_name'].strip():
        return mgr['group_name'].strip()
    return (mgr['name'] if mgr else 'Manager') + ' Group'


def _group_enabled(mgr):
    return bool(mgr.get('group_enabled', True)) if mgr else False


# ─────────────────────────────────────────────────────────────────────────────
# USER — PRIVATE CHAT
# ─────────────────────────────────────────────────────────────────────────────

@chat_bp.route('/messages', methods=['GET'])
@login_required
def chat_messages(current_user):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, sender, body, created_at FROM chat_messages WHERE user_id=? ORDER BY created_at ASC",
            (current_user['id'],)
        ).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None

        manager_info = None
        if current_user.get('assigned_manager_id'):
            mgr = _get_manager(db, current_user['assigned_manager_id'])
            if mgr:
                manager_info = {'name': mgr['name'], 'avatar_url': mgr.get('avatar_url')}

        db.execute(
            "UPDATE chat_messages SET read_by_user=TRUE WHERE user_id=? AND sender='admin'",
            (current_user['id'],)
        )
        db.commit()
        return jsonify({'ok': True, 'messages': rows, 'manager': manager_info})
    finally:
        db.close()


@chat_bp.route('/send', methods=['POST'])
@login_required
def chat_send(current_user):
    data = request.get_json() or {}
    body = (data.get('body') or '').strip()
    if not body:
        return jsonify({'ok': False, 'error': 'Message cannot be empty'})
    if len(body) > 2000:
        return jsonify({'ok': False, 'error': 'Message is too long'})
    db = get_db()
    try:
        db.execute(
            "INSERT INTO chat_messages (user_id, sender, body, read_by_user) VALUES (?, 'user', ?, TRUE)",
            (current_user['id'], body)
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# USER — UNREAD COUNT (private + group combined for nav badge)
# ─────────────────────────────────────────────────────────────────────────────

@chat_bp.route('/unread-count', methods=['GET'])
@login_required
def chat_unread_count(current_user):
    db = get_db()
    try:
        private_unread = db.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE user_id=? AND sender='admin' AND read_by_user=FALSE",
            (current_user['id'],)
        ).fetchone()['c']

        group_unread = 0
        mgr_id = current_user.get('assigned_manager_id')
        if mgr_id:
            mgr = _get_manager(db, mgr_id)
            if mgr and _group_enabled(mgr):
                last_read = db.execute(
                    "SELECT last_read_id FROM group_chat_read WHERE user_id=? AND manager_id=?",
                    (current_user['id'], mgr_id)
                ).fetchone()
                last_read_id = last_read['last_read_id'] if last_read else 0
                group_unread = db.execute(
                    "SELECT COUNT(*) AS c FROM group_chat_messages WHERE manager_id=? AND id > ?",
                    (mgr_id, last_read_id)
                ).fetchone()['c']

        return jsonify({'ok': True, 'unread': private_unread, 'group_unread': group_unread})
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# USER — CHAT SELECTION SCREEN PREVIEWS
# ─────────────────────────────────────────────────────────────────────────────

@chat_bp.route('/previews', methods=['GET'])
@login_required
def chat_previews(current_user):
    db = get_db()
    try:
        uid    = current_user['id']
        mgr_id = current_user.get('assigned_manager_id')

        # ── Private preview ───────────────────────────────────────────────────
        last_private = db.execute(
            "SELECT sender, body, created_at FROM chat_messages WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
            (uid,)
        ).fetchone()
        private_unread = db.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE user_id=? AND sender='admin' AND read_by_user=FALSE",
            (uid,)
        ).fetchone()['c']
        private = {
            'last_message': last_private['body'][:80] if last_private else None,
            'last_sender':  last_private['sender'] if last_private else None,
            'last_at':      last_private['created_at'].strftime('%Y/%m/%d') if last_private and last_private['created_at'] else None,
            'unread':       private_unread,
        }

        # ── Group preview (only if group is enabled) ──────────────────────────
        group = {'last_message': None, 'last_at': None, 'unread': 0,
                 'manager': None, 'group_name': None, 'group_enabled': False}
        if mgr_id:
            mgr = _get_manager(db, mgr_id)
            if mgr:
                enabled = _group_enabled(mgr)
                manager_info = {'name': mgr['name'], 'avatar_url': mgr.get('avatar_url')}
                if enabled:
                    last_group = db.execute(
                        "SELECT id, body, created_at FROM group_chat_messages WHERE manager_id=? ORDER BY created_at DESC LIMIT 1",
                        (mgr_id,)
                    ).fetchone()
                    last_read = db.execute(
                        "SELECT last_read_id FROM group_chat_read WHERE user_id=? AND manager_id=?",
                        (uid, mgr_id)
                    ).fetchone()
                    last_read_id = last_read['last_read_id'] if last_read else 0
                    group_unread = db.execute(
                        "SELECT COUNT(*) AS c FROM group_chat_messages WHERE manager_id=? AND id > ?",
                        (mgr_id, last_read_id)
                    ).fetchone()['c']
                    group = {
                        'last_message':  last_group['body'][:80] if last_group else None,
                        'last_at':       last_group['created_at'].strftime('%Y/%m/%d') if last_group and last_group['created_at'] else None,
                        'unread':        group_unread,
                        'manager':       manager_info,
                        'group_name':    _group_name(mgr),
                        'group_enabled': True,
                    }
                else:
                    group = {
                        'last_message': None, 'last_at': None, 'unread': 0,
                        'manager': manager_info,
                        'group_name': _group_name(mgr),
                        'group_enabled': False,
                    }

        return jsonify({'ok': True, 'private': private, 'group': group})
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# USER — GROUP CHAT MESSAGES
# ─────────────────────────────────────────────────────────────────────────────

@chat_bp.route('/group/messages', methods=['GET'])
@login_required
def group_chat_messages(current_user):
    mgr_id = current_user.get('assigned_manager_id')
    if not mgr_id:
        return jsonify({'ok': True, 'messages': [], 'manager': None, 'group_name': None, 'group_enabled': False})

    db = get_db()
    try:
        mgr = _get_manager(db, mgr_id)
        if not mgr or not _group_enabled(mgr):
            return jsonify({'ok': False, 'error': 'Group chat is currently disabled by your manager'}), 403

        rows = db.execute(
            "SELECT id, body, created_at FROM group_chat_messages WHERE manager_id=? ORDER BY created_at ASC",
            (mgr_id,)
        ).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
            r['sender'] = 'admin'

        manager_info = {'name': mgr['name'], 'avatar_url': mgr.get('avatar_url')}

        if rows:
            last_id = rows[-1]['id']
            db.execute("""
                INSERT INTO group_chat_read (user_id, manager_id, last_read_id)
                VALUES (?, ?, ?)
                ON CONFLICT (user_id, manager_id) DO UPDATE SET last_read_id = EXCLUDED.last_read_id
            """, (current_user['id'], mgr_id, last_id))
            db.commit()

        return jsonify({
            'ok': True,
            'messages':      rows,
            'manager':       manager_info,
            'group_name':    _group_name(mgr),
            'group_enabled': True,
        })
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — GROUP CHAT MESSAGES + SEND
# ─────────────────────────────────────────────────────────────────────────────

@chat_bp.route('/group/admin/messages', methods=['GET'])
@admin_login_required
def admin_group_chat_messages(current_admin):
    # Super can read any manager's group by passing ?manager_id=
    target_id = _resolve_target(request, current_admin)
    db = get_db()
    try:
        mgr = _get_manager(db, target_id)
        rows = db.execute(
            "SELECT id, body, created_at FROM group_chat_messages WHERE manager_id=? ORDER BY created_at ASC",
            (target_id,)
        ).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
        return jsonify({
            'ok': True,
            'messages':      rows,
            'group_name':    _group_name(mgr) if mgr else '',
            'group_enabled': _group_enabled(mgr) if mgr else False,
        })
    finally:
        db.close()


@chat_bp.route('/group/admin/send', methods=['POST'])
@admin_login_required
def admin_group_chat_send(current_admin):
    target_id = _resolve_target(request, current_admin)
    data = request.get_json() or {}
    body = (data.get('body') or '').strip()
    if not body:
        return jsonify({'ok': False, 'error': 'Message cannot be empty'})
    if len(body) > 2000:
        return jsonify({'ok': False, 'error': 'Message is too long'})
    db = get_db()
    try:
        mgr = _get_manager(db, target_id)
        # Super can send even when group is disabled; others cannot
        if not _group_enabled(mgr) and current_admin['role'] != 'super':
            return jsonify({'ok': False, 'error': 'Your group chat is currently disabled'})
        db.execute(
            "INSERT INTO group_chat_messages (manager_id, body) VALUES (?, ?)",
            (target_id, body)
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN — GROUP SETTINGS (name + enabled toggle)
# ─────────────────────────────────────────────────────────────────────────────

@chat_bp.route('/group/admin/settings', methods=['GET'])
@admin_login_required
def admin_group_settings_get(current_admin):
    target_id = _resolve_target(request, current_admin)
    db = get_db()
    try:
        mgr = _get_manager(db, target_id)
        if not mgr:
            return jsonify({'ok': False, 'error': 'Manager not found'})
        return jsonify({
            'ok':            True,
            'group_name':    mgr.get('group_name') or '',
            'group_enabled': _group_enabled(mgr),
        })
    finally:
        db.close()


@chat_bp.route('/group/admin/settings', methods=['PATCH'])
@admin_login_required
def admin_group_settings_patch(current_admin):
    target_id = _resolve_target(request, current_admin)
    # Non-super managers can only update their own group
    if current_admin['role'] != 'super' and target_id != current_admin['id']:
        return jsonify({'ok': False, 'error': 'Only the main manager can modify other managers\' groups'}), 403

    data    = request.get_json() or {}
    updates = {}
    if 'group_name' in data:
        name = (data['group_name'] or '').strip()
        if len(name) > 60:
            return jsonify({'ok': False, 'error': 'Group name must be 60 characters or fewer'})
        updates['group_name'] = name or None   # store NULL when blanked out (falls back to default)
    if 'group_enabled' in data:
        updates['group_enabled'] = bool(data['group_enabled'])

    if not updates:
        return jsonify({'ok': False, 'error': 'Nothing to update'})

    set_clause = ', '.join(f"{k}=?" for k in updates)
    db = get_db()
    try:
        result = db.execute(
            f"UPDATE admins SET {set_clause} WHERE id=?",
            tuple(updates.values()) + (target_id,)
        )
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Manager not found'})
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# HELPER — resolve which manager's group to act on
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_target(req, current_admin):
    """
    Super admins can pass ?manager_id=X to act on any manager's group.
    All other admins are locked to their own id.
    """
    if current_admin['role'] == 'super':
        try:
            mid = int(req.args.get('manager_id') or (req.get_json() or {}).get('manager_id') or 0)
            return mid if mid else current_admin['id']
        except (TypeError, ValueError):
            pass
    return current_admin['id']
