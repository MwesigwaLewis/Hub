from flask import Blueprint, request, jsonify
from db.database import get_db
from middleware.auth import login_required
from middleware.admin_auth import admin_login_required

chat_bp = Blueprint('chat', __name__, url_prefix='/api/chat')


# ── GET /api/chat/messages ─────────────────────────────────────────────────────
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
            mgr = db.execute(
                "SELECT name, avatar_url FROM admins WHERE id=?",
                (current_user['assigned_manager_id'],)
            ).fetchone()
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


# ── POST /api/chat/send ────────────────────────────────────────────────────────
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


# ── GET /api/chat/unread-count ─────────────────────────────────────────────────
# Returns both private unread + group unread so the nav badge can sum them.
@chat_bp.route('/unread-count', methods=['GET'])
@login_required
def chat_unread_count(current_user):
    db = get_db()
    try:
        # Private unread
        row = db.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE user_id=? AND sender='admin' AND read_by_user=FALSE",
            (current_user['id'],)
        ).fetchone()
        private_unread = row['c'] if row else 0

        # Group unread — messages from their manager newer than last_read_id
        group_unread = 0
        mgr_id = current_user.get('assigned_manager_id')
        if mgr_id:
            last_read = db.execute(
                "SELECT last_read_id FROM group_chat_read WHERE user_id=? AND manager_id=?",
                (current_user['id'], mgr_id)
            ).fetchone()
            last_read_id = last_read['last_read_id'] if last_read else 0
            g_row = db.execute(
                "SELECT COUNT(*) AS c FROM group_chat_messages WHERE manager_id=? AND id > ?",
                (mgr_id, last_read_id)
            ).fetchone()
            group_unread = g_row['c'] if g_row else 0

        return jsonify({'ok': True, 'unread': private_unread, 'group_unread': group_unread})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# GROUP CHAT — USER SIDE
# ══════════════════════════════════════════════════════════════════════════

# ── GET /api/chat/group/messages ───────────────────────────────────────────────
@chat_bp.route('/group/messages', methods=['GET'])
@login_required
def group_chat_messages(current_user):
    mgr_id = current_user.get('assigned_manager_id')
    if not mgr_id:
        return jsonify({'ok': True, 'messages': [], 'manager': None})

    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, body, created_at FROM group_chat_messages WHERE manager_id=? ORDER BY created_at ASC",
            (mgr_id,)
        ).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
            r['sender'] = 'admin'  # all group messages are from the manager

        mgr = db.execute(
            "SELECT name, avatar_url FROM admins WHERE id=?", (mgr_id,)
        ).fetchone()
        manager_info = {'name': mgr['name'], 'avatar_url': mgr.get('avatar_url')} if mgr else None

        # Mark as read — upsert last_read_id to the latest message id
        if rows:
            last_id = rows[-1]['id']
            db.execute("""
                INSERT INTO group_chat_read (user_id, manager_id, last_read_id)
                VALUES (?, ?, ?)
                ON CONFLICT (user_id, manager_id) DO UPDATE SET last_read_id = EXCLUDED.last_read_id
            """, (current_user['id'], mgr_id, last_id))
            db.commit()

        return jsonify({'ok': True, 'messages': rows, 'manager': manager_info})
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════
# GROUP CHAT — ADMIN SIDE
# ══════════════════════════════════════════════════════════════════════════

# ── GET /api/chat/group/admin/messages ────────────────────────────────────────
@chat_bp.route('/group/admin/messages', methods=['GET'])
@admin_login_required
def admin_group_chat_messages(current_admin):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, body, created_at FROM group_chat_messages WHERE manager_id=? ORDER BY created_at ASC",
            (current_admin['id'],)
        ).fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else None
        return jsonify({'ok': True, 'messages': rows})
    finally:
        db.close()


# ── POST /api/chat/group/admin/send ───────────────────────────────────────────
@chat_bp.route('/group/admin/send', methods=['POST'])
@admin_login_required
def admin_group_chat_send(current_admin):
    data = request.get_json() or {}
    body = (data.get('body') or '').strip()
    if not body:
        return jsonify({'ok': False, 'error': 'Message cannot be empty'})
    if len(body) > 2000:
        return jsonify({'ok': False, 'error': 'Message is too long'})
    db = get_db()
    try:
        db.execute(
            "INSERT INTO group_chat_messages (manager_id, body) VALUES (?, ?)",
            (current_admin['id'], body)
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()
