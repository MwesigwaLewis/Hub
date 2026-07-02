from flask import Blueprint, request, jsonify
from db.database import get_db
from middleware.auth import login_required

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

        # Opening the chat marks the manager's replies as read.
        db.execute("UPDATE chat_messages SET read_by_user=TRUE WHERE user_id=? AND sender='admin'",
                   (current_user['id'],))
        db.commit()

        return jsonify({'ok': True, 'messages': rows})
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
# Lightweight poll target for a notification dot on the Manager button.
@chat_bp.route('/unread-count', methods=['GET'])
@login_required
def chat_unread_count(current_user):
    db = get_db()
    try:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM chat_messages WHERE user_id=? AND sender='admin' AND read_by_user=FALSE",
            (current_user['id'],)
        ).fetchone()
        return jsonify({'ok': True, 'unread': row['c']})
    finally:
        db.close()
      
