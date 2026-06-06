from flask import Blueprint, request, jsonify
from db.database import get_db
from middleware.auth import login_required

withdraw_bp = Blueprint('withdraw', __name__, url_prefix='/api')

# ── POST /api/withdraw ────────────────────────────────────────────────────────
@withdraw_bp.route('/withdraw', methods=['POST'])
@login_required
def withdraw(current_user):
    data   = request.get_json() or {}
    amount = float(data.get('amount', 0))

    if amount <= 0:
        return jsonify({'ok': False, 'error': 'Enter a valid amount'})
    if amount > current_user['balance']:
        return jsonify({'ok': False, 'error': 'Insufficient balance'})

    db = get_db()
    try:
        db.execute(
            "UPDATE users SET balance=balance-?, total_withdraw=total_withdraw+? WHERE id=?",
            (amount, amount, current_user['id'])
        )
        db.execute(
            "INSERT INTO withdraw_requests (user_id, amount) VALUES (?,?)",
            (current_user['id'], amount)
        )
        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?,?,?,?)",
            (current_user['id'], 'withdraw', amount, 'Withdrawal request submitted')
        )
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()
