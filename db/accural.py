"""
Real-time earnings accrual.

Machines don't have a background worker paying them out — instead, earnings
are computed on-demand from elapsed wall-clock time whenever a logged-in
user makes a request. This means income starts accumulating the moment a
machine is bought (bought_at = purchase time) rather than waiting for some
external cron job that doesn't exist in this project.

Call accrue_user_earnings(db, user_id) once per authenticated request
(wired into middleware/auth.py) BEFORE reading the user row, so every page
the user loads reflects up-to-the-second earnings.
"""
from datetime import datetime, timezone


def _as_utc(dt):
    """Normalize a naive/aware datetime to aware UTC for safe subtraction."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def accrue_user_earnings(db, user_id):
    """
    Walk every 'running' machine owned by user_id, compute how much of its
    total_income has accrued since bought_at (capped at total_income once
    the lock period has fully elapsed), and credit the DELTA since the last
    time we checked (tracked via the 'earned' column on user_machines).

    Any machine that has now fully paid out is flipped to status='expired'.
    """
    machines = db.execute("""
        SELECT id, user_id, daily_income, total_income, earned, bought_at, expires_at
        FROM user_machines
        WHERE user_id = ? AND status = 'running'
    """, (user_id,)).fetchall()

    if not machines:
        return

    now = datetime.now(timezone.utc)
    total_delta = 0.0

    for m in machines:
        bought_at = _as_utc(m['bought_at']) or now
        expires_at = _as_utc(m['expires_at'])

        elapsed_seconds = (min(now, expires_at) if expires_at else now) - bought_at
        elapsed_days = max(elapsed_seconds.total_seconds(), 0) / 86400.0

        accrued = min(m['daily_income'] * elapsed_days, m['total_income'])
        delta = accrued - m['earned']

        if delta <= 0:
            # Nothing new accrued yet (e.g. purchased seconds ago) — still
            # flip to expired below if fully paid out and past expiry.
            delta = 0
        else:
            total_delta += delta

        newly_expired = expires_at is not None and now >= expires_at

        db.execute("""
            UPDATE user_machines
            SET earned = ?,
                status = ?
            WHERE id = ?
        """, (accrued, 'expired' if newly_expired else 'running', m['id']))

        if delta > 0:
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, note) VALUES (?,?,?,?)",
                (user_id, 'ai_income', delta, f"AI machine income (machine #{m['id']})")
            )

    if total_delta > 0:
        db.execute("""
            UPDATE users
            SET balance        = balance + ?,
                ai_income      = ai_income + ?,
                today_earnings = today_earnings + ?
            WHERE id = ?
        """, (total_delta, total_delta, total_delta, user_id))

    db.commit()

    
