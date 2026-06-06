from flask import Blueprint, jsonify
from db.database import get_db
from middleware.auth import login_required

transactions_bp = Blueprint('transactions', __name__, url_prefix='/api')

# ── GET /api/transactions ─────────────────────────────────────────────────────
@transactions_bp.route('/transactions', methods=['GET'])
@login_required
def get_transactions(current_user):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT type, amount, note, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 200
        """, (current_user['id'],)).fetchall()
        return jsonify({'ok': True, 'transactions': [dict(r) for r in rows]})
    finally:
        db.close()
