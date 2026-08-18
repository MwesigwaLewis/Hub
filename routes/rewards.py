from flask import Blueprint, request, jsonify
from db.database import get_db
from middleware.auth import login_required

rewards_bp = Blueprint('rewards', __name__, url_prefix='/api/rewards')

# ── POST /api/rewards/redeem ───────────────────────────────────────────────────
@rewards_bp.route('/redeem', methods=['POST'])
@login_required
def redeem_reward(current_user):
    data = request.get_json() or {}
    code = (data.get('code') or '').strip().upper()
    if not code:
        return jsonify({'ok': False, 'error': 'Enter a reward code'})

    db = get_db()
    try:
        reward = db.execute("SELECT * FROM reward_codes WHERE code=?", (code,)).fetchone()
        if not reward:
            return jsonify({'ok': False, 'error': 'Invalid reward code'})
        if not reward['active']:
            return jsonify({'ok': False, 'error': 'This reward code is no longer active'})

        now = db.execute("SELECT NOW()::timestamp AS now").fetchone()['now']
        if now < reward['valid_from']:
            return jsonify({'ok': False, 'error': 'This reward code is not active yet'})
        if now > reward['valid_until']:
            return jsonify({'ok': False, 'error': 'This reward code has expired'})

        # One redemption per user per code — the UNIQUE(code_id, user_id)
        # constraint is the real guard against a double-tap race; this is
        # just for a clean error message ahead of that.
        already = db.execute(
            "SELECT id FROM reward_redemptions WHERE code_id=? AND user_id=?",
            (reward['id'], current_user['id'])
        ).fetchone()
        if already:
            return jsonify({'ok': False, 'error': "You've already redeemed this code"})

        try:
            db.execute(
                "INSERT INTO reward_redemptions (code_id, user_id) VALUES (?, ?)",
                (reward['id'], current_user['id'])
            )
        except Exception:
            db.rollback()
            return jsonify({'ok': False, 'error': "You've already redeemed this code"})

        db.execute("UPDATE users SET balance=balance+? WHERE id=?", (reward['amount'], current_user['id']))
        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?, 'reward_code', ?, ?)",
            (current_user['id'], reward['amount'], f'Reward code "{code}"' + (f' — {reward["description"]}' if reward['description'] else ''))
        )
        db.commit()

        return jsonify({'ok': True, 'amount': reward['amount']})
    finally:
        db.close()

# ── GET /api/rewards/history ───────────────────────────────────────────────────
@rewards_bp.route('/history', methods=['GET'])
@login_required
def reward_history(current_user):
    db = get_db()
    try:
        rows = db.execute("""
            SELECT rc.code, rc.amount, rc.description, rr.redeemed_at
            FROM reward_redemptions rr
            JOIN reward_codes rc ON rc.id = rr.code_id
            WHERE rr.user_id = ?
            ORDER BY rr.redeemed_at DESC
        """, (current_user['id'],)).fetchall()
        for r in rows:
            r['redeemed_at'] = r['redeemed_at'].strftime('%Y-%m-%d %H:%M') if r['redeemed_at'] else None
        return jsonify({'ok': True, 'history': rows})
    finally:
        db.close()
                           
