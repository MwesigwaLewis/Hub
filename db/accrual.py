"""
Daily earnings accrual — rate-limited to at most once per 5 minutes per user.

Previously this ran on EVERY authenticated request (wired into
middleware/auth.py's get_current_user). With 73 routes, every page load
triggered multiple DB writes — even for trivial calls like /api/messages or
/api/chat/unread-count. On Supabase free tier (max 2 pool connections,
~200ms round-trip per query) that made every page load visibly slow.

The fix: an in-process timestamp cache (_last_accrual) prevents re-running
within 5 minutes for the same user. Accrual still fires quickly after login
and after a real day has passed — the 5-minute window just eliminates the
pointless mid-session re-runs where days_elapsed hasn't changed anyway.
"""
import time
from datetime import datetime, timezone

# In-process cache: user_id -> unix timestamp of last accrual run.
# Single-worker Gunicorn means this is safe and shared across all requests.
_last_accrual: dict[int, float] = {}
_ACCRUAL_COOLDOWN = 300   # seconds — 5 minutes


def _as_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def accrue_user_earnings(db, user_id: int) -> bool:
    """
    Compute and credit whole-day machine income for user_id.
    Returns True if accrual actually ran, False if it was skipped (cooldown).

    Skips entirely if called within _ACCRUAL_COOLDOWN seconds of the last
    run for this user — no DB queries, no round trips.
    """
    now_ts = time.monotonic()
    last   = _last_accrual.get(user_id, 0)
    if now_ts - last < _ACCRUAL_COOLDOWN:
        return False          # cooldown — skip all DB work

    machines = db.execute("""
        SELECT id, user_id, total_income, earned, bought_at, expires_at, lock_days
        FROM user_machines
        WHERE user_id = ? AND status = 'running'
    """, (user_id,)).fetchall()

    _last_accrual[user_id] = now_ts  # stamp BEFORE any await/IO so a rapid
                                      # second call also sees the cooldown

    if not machines:
        return True   # ran, nothing to do

    now = datetime.now(timezone.utc)
    total_delta    = 0.0
    machine_updates = []
    tx_rows         = []

    for m in machines:
        bought_at  = _as_utc(m['bought_at']) or now
        expires_at = _as_utc(m['expires_at'])
        lock_days  = m['lock_days']
        if not lock_days or lock_days < 1:
            lock_days = max(1, round(
                ((expires_at - bought_at).total_seconds() / 86400)
            )) if expires_at else 1

        elapsed_td   = (min(now, expires_at) if expires_at else now) - bought_at
        days_elapsed = max(0, min(elapsed_td.days, lock_days))

        owed  = round(m['total_income'] * days_elapsed / lock_days, 2)
        delta = max(owed - (m['earned'] or 0.0), 0)
        newly_expired = expires_at is not None and now >= expires_at

        machine_updates.append((m['id'], owed, 'expired' if newly_expired else 'running'))
        if delta > 0:
            total_delta += delta
            tx_rows.append((
                user_id, 'ai_income', delta,
                f"AI income — day {days_elapsed}/{lock_days} (machine #{m['id']})"
            ))

    # Batch UPDATE all machines in one round trip
    values_sql = ','.join(['(%s::int,%s::double precision,%s::text)'] * len(machine_updates))
    flat       = [v for row in machine_updates for v in row]
    db.execute(f"""
        UPDATE user_machines AS um
        SET earned = v.earned, status = v.status
        FROM (VALUES {values_sql}) AS v(id, earned, status)
        WHERE um.id = v.id
    """, tuple(flat))

    # Batch INSERT income ledger rows
    if tx_rows:
        values_sql = ','.join(['(%s,%s,%s,%s)'] * len(tx_rows))
        flat       = [v for row in tx_rows for v in row]
        db.execute(f"""
            INSERT INTO transactions (user_id, type, amount, note)
            VALUES {values_sql}
        """, tuple(flat))

    if total_delta > 0:
        db.execute("""
            UPDATE users
            SET balance        = balance + ?,
                ai_income      = ai_income + ?,
                today_earnings = today_earnings + ?
            WHERE id = ?
        """, (total_delta, total_delta, total_delta, user_id))

    db.commit()
    return True
