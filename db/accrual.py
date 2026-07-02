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

Performance note: this used to issue one UPDATE (and one INSERT) per
running machine, so a user with N machines cost N+1 sequential DB round
trips on every single page load. It's now batched into at most 4 round
trips total regardless of N — one SELECT, one batched UPDATE, one batched
INSERT, one UPDATE on the user row.
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
    machine_updates = []   # (id, earned, status)
    tx_rows = []            # (user_id, type, amount, note)

    for m in machines:
        bought_at = _as_utc(m['bought_at']) or now
        expires_at = _as_utc(m['expires_at'])

        elapsed_seconds = (min(now, expires_at) if expires_at else now) - bought_at
        elapsed_days = max(elapsed_seconds.total_seconds(), 0) / 86400.0

        accrued = min(m['daily_income'] * elapsed_days, m['total_income'])
        delta = max(accrued - m['earned'], 0)
        newly_expired = expires_at is not None and now >= expires_at

        machine_updates.append((m['id'], accrued, 'expired' if newly_expired else 'running'))

        if delta > 0:
            total_delta += delta
            tx_rows.append((user_id, 'ai_income', delta, f"AI machine income (machine #{m['id']})"))

    # One round trip to update every machine at once, instead of one UPDATE
    # per machine. Explicit casts avoid "could not determine data type of
    # parameter" errors, since a bare VALUES list has no inherent column type.
    values_sql = ','.join(['(%s::int,%s::double precision,%s::text)'] * len(machine_updates))
    flat_params = [v for row in machine_updates for v in row]
    db.execute(f"""
        UPDATE user_machines AS um
        SET earned = v.earned, status = v.status
        FROM (VALUES {values_sql}) AS v(id, earned, status)
        WHERE um.id = v.id
    """, tuple(flat_params))

    # Same idea for the income-ledger rows: one INSERT for all of them.
    if tx_rows:
        values_sql = ','.join(['(%s,%s,%s,%s)'] * len(tx_rows))
        flat_params = [v for row in tx_rows for v in row]
        db.execute(f"""
            INSERT INTO transactions (user_id, type, amount, note)
            VALUES {values_sql}
        """, tuple(flat_params))

    if total_delta > 0:
        db.execute("""
            UPDATE users
            SET balance        = balance + ?,
                ai_income      = ai_income + ?,
                today_earnings = today_earnings + ?
            WHERE id = ?
        """, (total_delta, total_delta, total_delta, user_id))

    db.commit()
    
