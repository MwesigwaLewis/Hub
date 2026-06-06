import random
from flask import Blueprint, jsonify
from db.database import get_db
from middleware.auth import login_required

raffle_bp = Blueprint('raffle', __name__, url_prefix='/api')

PRIZES = [50, 100, 200, 500, 1000]
WEIGHTS = [40, 30, 20, 8, 2]          # probability weights

# ── POST /api/raffle ──────────────────────────────────────────────────────────
@raffle_bp.route('/raffle', methods=['POST'])
@login_required
def do_raffle(current_user):
    if current_user['raffle_ready'] < 1:
        return jsonify({'ok': False, 'error': 'No raffle tickets available'})

    prize = random.choices(PRIZES, weights=WEIGHTS, k=1)[0]

    db = get_db()
    try:
        db.execute("""
            UPDATE users
            SET raffle_ready = raffle_ready - 1,
                balance      = balance + ?
            WHERE id = ?
        """, (prize, current_user['id']))

        db.execute("""
            INSERT INTO raffle_records (user_id, user_phone, prize)
            VALUES (?, ?, ?)
        """, (current_user['id'], current_user['phone'], prize))

        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?,?,?,?)",
            (current_user['id'], 'raffle', prize, f'Raffle win: ₦{prize}')
        )
        db.commit()
        return jsonify({'ok': True, 'prize': prize})
    finally:
        db.close()

# ── GET /api/raffle/records ───────────────────────────────────────────────────
@raffle_bp.route('/raffle/records', methods=['GET'])
@login_required
def raffle_records(current_user):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT user_phone, prize, created_at
            FROM raffle_records
            ORDER BY created_at DESC
            LIMIT 50
        """).fetchall()
        return jsonify({'ok': True, 'records': [dict(r) for r in rows]})
    finally:
        db.close()
