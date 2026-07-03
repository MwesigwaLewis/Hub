"""
Daily earnings accrual.

Machines don't have a background worker paying them out — instead, earnings
are computed on-demand from elapsed whole days whenever a logged-in user
makes a request. Call accrue_user_earnings(db, user_id) once per
authenticated request (wired into middleware/auth.py) BEFORE reading the
user row, so it's never more than one request stale.

IMPORTANT — this pays out in whole-day steps, not continuously:
Earlier versions computed accrual from fractional elapsed seconds, which
credited a tiny sliver of income (a fraction of a cent) on every single page
load — confusing to see, and because daily_income was itself pre-rounded to
2 decimals at purchase time, the running total could fall a few cents short
of total_income by the final day instead of matching it exactly.

This version instead tracks whole days elapsed since purchase and computes
each day's payout as a CUMULATIVE share of total_income:
    owed_after_day(k) = round(total_income * k / lock_days, 2)
    credited_on_day(k) = owed_after_day(k) - owed_after_day(k-1)
Two things fall out of that for free:
  1. Nothing is credited between whole-day boundaries — no more mystery
     fractional-cent transactions between page loads.
  2. Summing every day's credit from k=1..lock_days telescopes to exactly
     round(total_income, 2) — the machine's full total_income, exactly, on
     the last day. No rounding shortfall.
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
    Walk every 'running' machine owned by user_id, compute the cumulative
    whole-day payout owed since bought_at (capped at total_income once
    lock_days have fully elapsed), and credit the DELTA since the last time
    we checked (tracked via the 'earned' column on user_machines, which now
    always equals owed_after_day(days_elapsed)).

    Any machine whose full lock period has elapsed is flipped to
    status='expired'.
    """
    machines = db.execute("""
        SELECT id, user_id, total_income, earned, bought_at, expires_at, lock_days
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

        # lock_days should always be set (snapshotted at purchase, backfilled
        # by migration for older rows) — this is just a last-resort fallback
        # so a missing value can never divide by zero.
        lock_days = m['lock_days']
        if not lock_days or lock_days < 1:
            lock_days = max(1, round(((expires_at - bought_at).total_seconds() / 86400))) if expires_at else 1

        elapsed_td = (min(now, expires_at) if expires_at else now) - bought_at
        days_elapsed = max(0, min(elapsed_td.days, lock_days))

        owed = round(m['total_income'] * days_elapsed / lock_days, 2)
        delta = max(owed - (m['earned'] or 0.0), 0)
        newly_expired = expires_at is not None and now >= expires_at

        machine_updates.append((m['id'], owed, 'expired' if newly_expired else 'running'))

        if delta > 0:
            total_delta += delta
            tx_rows.append((user_id, 'ai_income', delta, f"AI machine income — day {days_elapsed}/{lock_days} (machine #{m['id']})"))

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
        
