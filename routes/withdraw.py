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

    db = get_db()
    try:
        # Balance check is part of the same atomic UPDATE (not a separate
        # check-then-act step) so two concurrent withdrawals can't both pass
        # and take the user's balance negative.
        result = db.execute(
            "UPDATE users SET balance=balance-?, total_withdraw=total_withdraw+? WHERE id=? AND balance>=?",
            (amount, amount, current_user['id'], amount)
        )
        if result.rowcount == 0:
            db.rollback()
            return jsonify({'ok': False, 'error': 'Insufficient balance'})
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
            
