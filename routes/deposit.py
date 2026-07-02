import os
import requests
from datetime import datetime
from flask import Blueprint, request, jsonify
from db.database import get_db
from middleware.auth import login_required

deposit_bp = Blueprint('deposit', __name__, url_prefix='/api')

FLW_SECRET_KEY = os.environ.get('FLW_SECRET_KEY', '')
FLW_VERIFY_URL = 'https://api.flutterwave.com/v3/transactions/{}/verify'

# ── POST /api/deposit/verify ──────────────────────────────────────────────────
#
# This is the ONLY way money enters a user's balance.
# Flow:
#   1. Frontend calls this after Flutterwave's callback fires.
#   2. We call Flutterwave's own API with the secret key to confirm.
#   3. We check status === 'successful', amount matches, currency is NGN.
#   4. We check the tx_ref has never been credited before (duplicate guard).
#   5. Only then do we credit the user's balance.
#
# The user cannot fake this — they would need our FLW_SECRET_KEY AND
# a real successful transaction on Flutterwave's servers.
#
@deposit_bp.route('/deposit/verify', methods=['POST'])
@login_required
def verify_deposit(current_user):
    data            = request.get_json() or {}
    transaction_id  = data.get('transaction_id')
    tx_ref          = data.get('tx_ref', '').strip()
    expected_amount = float(data.get('expected_amount', 0))

    # ── Basic input checks ────────────────────────────────────────────────────
    if not transaction_id or not tx_ref or expected_amount <= 0:
        return jsonify({'ok': False, 'error': 'Invalid request'})

    if not FLW_SECRET_KEY:
        return jsonify({'ok': False, 'error': 'Payment gateway not configured. Contact support.'})

    db = get_db()
    try:
        # ── Duplicate guard — has this tx_ref already been credited? ──────────
        existing = db.execute(
            "SELECT id, status FROM deposit_transactions WHERE tx_ref=?", (tx_ref,)
        ).fetchone()
        if existing and existing['status'] == 'successful':
            return jsonify({'ok': False, 'error': 'Transaction already processed'})

        # ── Also guard by Flutterwave transaction_id ──────────────────────────
        dup_flw = db.execute(
            "SELECT id FROM deposit_transactions WHERE flw_transaction_id=? AND status='successful'",
            (str(transaction_id),)
        ).fetchone()
        if dup_flw:
            return jsonify({'ok': False, 'error': 'Transaction already used'})

        # ── Log as pending before calling Flutterwave ─────────────────────────
        if not existing:
            db.execute("""
                INSERT INTO deposit_transactions
                    (user_id, tx_ref, flw_transaction_id, amount, status)
                VALUES (?, ?, ?, ?, 'pending')
            """, (current_user['id'], tx_ref, str(transaction_id), expected_amount))
            db.commit()

        # ── Call Flutterwave's verification API ───────────────────────────────
        try:
            flw_resp = requests.get(
                FLW_VERIFY_URL.format(transaction_id),
                headers={
                    'Authorization': f'Bearer {FLW_SECRET_KEY}',
                    'Content-Type':  'application/json',
                },
                timeout=15
            )
            flw_data = flw_resp.json()
        except Exception as e:
            return jsonify({'ok': False, 'error': 'Could not reach payment gateway. Try again.'})

        # ── Validate Flutterwave's response ───────────────────────────────────
        if flw_data.get('status') != 'success':
            db.execute(
                "UPDATE deposit_transactions SET status='failed' WHERE tx_ref=?", (tx_ref,)
            )
            db.commit()
            return jsonify({'ok': False, 'error': 'Payment verification failed'})

        txn = flw_data.get('data', {})

        # Must be successful
        if txn.get('status') != 'successful':
            db.execute(
                "UPDATE deposit_transactions SET status='failed' WHERE tx_ref=?", (tx_ref,)
            )
            db.commit()
            return jsonify({'ok': False, 'error': 'Payment was not successful'})

        # Must be NGN
        if txn.get('currency') != 'NGN':
            return jsonify({'ok': False, 'error': 'Invalid currency'})

        # Amount must match (allow small rounding, but never less than expected)
        flw_amount = float(txn.get('amount', 0))
        if flw_amount < expected_amount - 1:
            db.execute(
                "UPDATE deposit_transactions SET status='failed' WHERE tx_ref=?", (tx_ref,)
            )
            db.commit()
            return jsonify({'ok': False, 'error': 'Amount mismatch. Contact support.'})

        # tx_ref must match what we generated
        if txn.get('tx_ref') != tx_ref:
            return jsonify({'ok': False, 'error': 'Reference mismatch'})

        # ── All checks passed — credit the balance ────────────────────────────
        credited = flw_amount  # credit exact amount Flutterwave confirmed
        network  = txn.get('payment_type', '')

        db.execute("""
            UPDATE deposit_transactions
            SET status='successful', flw_transaction_id=?, network=?, verified_at=?
            WHERE tx_ref=?
        """, (str(txn.get('id', transaction_id)), network, datetime.utcnow(), tx_ref))

        # Grant one raffle ticket per successful deposit. Without this,
        # raffle_ready never leaves 0 and the raffle page is unreachable.
        db.execute("""
            UPDATE users
            SET balance       = balance + ?,
                wallet        = wallet  + ?,
                total_deposit = total_deposit + ?,
                raffle_ready  = raffle_ready + 1
            WHERE id = ?
        """, (credited, credited, credited, current_user['id']))

        db.execute(
            "INSERT INTO transactions (user_id, type, amount, note) VALUES (?,?,?,?)",
            (current_user['id'], 'deposit', credited,
             f'Mobile money deposit via {network} — ref:{tx_ref}')
        )
        db.commit()

        return jsonify({'ok': True, 'credited_amount': credited})

    finally:
        db.close()
    
