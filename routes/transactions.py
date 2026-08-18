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


# ── GET /api/income-summary ───────────────────────────────────────────────────
# Replaces 3 separate fetches on income.html (/api/me + /api/my-machines +
# /api/transactions) with a single round trip that returns everything the
# page needs. Three authenticated fetches = three accrual checks, three
# connection acquisitions, and three Supabase round trips. One is one.
@transactions_bp.route('/income-summary', methods=['GET'])
@login_required
def income_summary(current_user):
    db = get_db()
    try:
        uid = current_user['id']

        machines = db.execute("""
            SELECT
                um.id, um.machine_id, um.daily_income, um.total_income,
                um.earned, um.status,
                um.purchase_price AS price,
                um.lock_days AS lock,
                m.series, m.image_url,
                to_char(um.bought_at,  'YYYY-MM-DD') AS start_date,
                to_char(um.expires_at, 'YYYY-MM-DD') AS end_date
            FROM user_machines um
            JOIN machines m ON m.id = um.machine_id
            WHERE um.user_id = ?
            ORDER BY um.bought_at DESC
        """, (uid,)).fetchall()

        txns = db.execute("""
            SELECT type, amount, note, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 200
        """, (uid,)).fetchall()
        for t in txns:
            t['created_at'] = t['created_at'].strftime('%Y-%m-%d %H:%M') if t['created_at'] else None

        return jsonify({
            'ok':          True,
            'user': {
                'ai_income':     current_user['ai_income'],
                'today_earnings':current_user['today_earnings'],
                'team_income':   current_user['team_income'],
                'total_deposit': current_user['total_deposit'],
            },
            'machines':     [dict(m) for m in machines],
            'transactions': [dict(t) for t in txns],
        })
    finally:
        db.close()
