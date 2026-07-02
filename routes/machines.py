from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from db.database import get_db
from middleware.auth import login_required

machines_bp = Blueprint('machines', __name__, url_prefix='/api')

# ── GET /api/machines ─────────────────────────────────────────────────────────
@machines_bp.route('/machines', methods=['GET'])
@login_required
def list_machines(current_user):
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM machines ORDER BY price ASC").fetchall()
        return jsonify({'ok': True, 'machines': [dict(r) for r in rows]})
    finally:
        db.close()

# ── GET /api/my-machines ──────────────────────────────────────────────────────
@machines_bp.route('/my-machines', methods=['GET'])
@login_required
def my_machines(current_user):
    db = get_db()
    try:
        # NOTE: the frontend (income.html) expects `price`, `lock`,
        # `start_date` and `end_date` fields. The old query only selected
        # um.* + m.series, so those all came back undefined/NaN client-side.
        # We alias purchase_price -> price, pull m.lock through the join,
        # and format bought_at/expires_at as plain date strings.
        rows = db.execute("""
            SELECT
                um.id, um.user_id, um.machine_id, um.daily_income,
                um.total_income, um.earned, um.status,
                um.purchase_price AS price,
                m.series, m.lock,
                to_char(um.bought_at,  'YYYY-MM-DD') AS start_date,
                to_char(um.expires_at, 'YYYY-MM-DD') AS end_date
            FROM user_machines um
            JOIN machines m ON m.id = um.machine_id
            WHERE um.user_id = ?
            ORDER BY um.bought_at DESC
        """, (current_user['id'],)).fetchall()
        return jsonify({'ok': True, 'machines': [dict(r) for r in rows]})
    finally:
        db.close()

# ── POST /api/machines/buy ────────────────────────────────────────────────────
@machines_bp.route('/machines/buy', methods=['POST'])
@login_required
def buy_machine(current_user):
    data       = request.get_json() or {}
    machine_id = (data.get('machine_id') or '').strip()

    if not machine_id:
        return jsonify({'ok': False, 'error': 'No machine selected'})

    db = get_db()
    try:
        machine = db.execute("SELECT * FROM machines WHERE id=?", (machine_id,)).fetchone()
        if not machine:
            return jsonify({'ok': False, 'error': 'Machine not found'})
        if machine['sold']:
            return jsonify({'ok': False, 'error': 'Machine is sold out'})
        if current_user['balance'] < machine['price']:
            return jsonify({'ok': False, 'error': 'Insufficient balance'})

        lock_days    = machine['lock']
        daily_income = round(machine['income'] / lock_days, 2)
        expires_at   = datetime.utcnow() + timedelta(days=lock_days)

        # Deduct balance
        db.execute(
            "UPDATE users SET balance=balance-? WHERE id=?",
            (machine['price'], current_user['id'])
        )

        # Record ownership
        db.execute("""
            INSERT INTO user_machines
                (user_id, machine_id, purchase_price, daily_income, total_income, expires_at)
            VALUES (?,?,?,?,?,?)
        """, (current_user['id'], machine_id, machine['price'],
               daily_income, machine['income'], expires_at))

        # Log transaction
        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?,?,?,?)",
            (current_user['id'], 'purchase', machine['price'], f'Bought machine {machine_id}')
        )

        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()
                   
