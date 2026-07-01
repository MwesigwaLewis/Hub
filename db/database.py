import os
import psycopg
from psycopg.rows import dict_row

# Supabase gives you this under Project Settings -> Database -> Connection string.
# Use the "Transaction pooler" URI (port 6543) in production so you don't run out
# of direct Postgres connections; the direct URI (port 5432) is fine for local dev.
DATABASE_URL = os.environ.get('SUPABASE_DB_URL') or os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    raise RuntimeError(
        "No database URL found. Set SUPABASE_DB_URL (or DATABASE_URL) in your .env — "
        "get it from your Supabase project: Settings -> Database -> Connection string."
    )


class _CursorWrapper:
    """Makes a psycopg dict_row cursor behave like the old sqlite3.Row-based
    cursor the rest of the app was written against."""
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _ConnWrapper:
    """Wraps a psycopg connection so existing route code — db.execute(...),
    .fetchone()/.fetchall(), db.commit(), db.close() — keeps working unchanged.
    Translates sqlite-style '?' placeholders to psycopg's '%s' automatically."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        cur = self._conn.cursor(row_factory=dict_row)
        cur.execute(query.replace('?', '%s'), params)
        return _CursorWrapper(cur)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db():
    """Return a Supabase/Postgres connection wrapped to match the previous
    sqlite3 interface used throughout the app (routes/*.py, middleware/auth.py)."""
    conn = psycopg.connect(DATABASE_URL, sslmode='require')
    return _ConnWrapper(conn)


def init_db():
    """
    Called once on app startup.
    Creates every table in Supabase Postgres if it doesn't already exist —
    safe to run repeatedly.
    """
    conn = psycopg.connect(DATABASE_URL, sslmode='require')
    cur = conn.cursor()

    # ── Users ─────────────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id             SERIAL PRIMARY KEY,
            phone          TEXT NOT NULL UNIQUE,
            password       TEXT NOT NULL,
            nick           TEXT NOT NULL DEFAULT Anon,
            avatar_url     TEXT,
            email          TEXT,
            vip_level      INTEGER NOT NULL DEFAULT 1,
            balance        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            wallet         DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            total_deposit  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            total_withdraw DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            ai_income      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            today_earnings DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            team_income    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            invite_count   INTEGER NOT NULL DEFAULT 0,
            team_count     INTEGER NOT NULL DEFAULT 0,
            invite_code    TEXT UNIQUE,
            invited_by     INTEGER REFERENCES users(id),
            raffle_ready   INTEGER NOT NULL DEFAULT 0,
            last_salary    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            this_salary    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Sessions ──────────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Deposit transactions (Flutterwave verified only) ──────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deposit_transactions (
            id                 SERIAL PRIMARY KEY,
            user_id            INTEGER NOT NULL REFERENCES users(id),
            tx_ref             TEXT NOT NULL UNIQUE,
            flw_transaction_id TEXT UNIQUE,
            amount             DOUBLE PRECISION NOT NULL,
            network            TEXT,
            status             TEXT NOT NULL DEFAULT 'pending',
            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            verified_at        TIMESTAMP
        )
    """)

    # ── Withdraw requests ─────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdraw_requests (
            id           SERIAL PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users(id),
            amount       DOUBLE PRECISION NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP
        )
    """)

    # ── AI Machines catalogue ─────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS machines (
            id        TEXT PRIMARY KEY,
            series    TEXT NOT NULL DEFAULT 'A',
            price     DOUBLE PRECISION NOT NULL,
            income    DOUBLE PRECISION NOT NULL,
            lock      INTEGER NOT NULL DEFAULT 30,
            image_url TEXT,
            sold      INTEGER NOT NULL DEFAULT 0
        )
    """)

    # ── User-owned machines ───────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_machines (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            machine_id      TEXT NOT NULL REFERENCES machines(id),
            purchase_price  DOUBLE PRECISION NOT NULL,
            daily_income    DOUBLE PRECISION NOT NULL,
            total_income    DOUBLE PRECISION NOT NULL,
            earned          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            status          TEXT NOT NULL DEFAULT 'running',
            bought_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at      TIMESTAMP
        )
    """)

    # ── General transactions ledger ───────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            type       TEXT NOT NULL,
            amount     DOUBLE PRECISION NOT NULL,
            note       TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Raffle records ────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS raffle_records (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            user_phone TEXT NOT NULL,
            prize      DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Messages / announcements ──────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         SERIAL PRIMARY KEY,
            text       TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Seed default machines if table is empty ───────────────────────────────
    cur.execute("SELECT COUNT(*) FROM machines")
    if cur.fetchone()[0] == 0:
        default_machines = [
            # A Series — Entry level (short lock, low price)
            ('A1', 'A',   500,    650,  30, '/assets/images/A1_Machine.jpg'),
            ('A2', 'A',  1000,   1350,  30, '/assets/images/A2_Machine.jpg'),
            ('A3', 'A',  2000,   2800,  30, '/assets/images/A3_Machine.jpg'),
            # E Series — Advanced (high ROI, longer lock)
            ('E1', 'E',  60000,  1500000, 2, '/assets/images/E1_Machine.jpg'),
            ('E2', 'E', 180000,  4680000, 120, '/assets/images/E2_Machine.jpg'),
            ('E3', 'E', 600000, 16200000, 100, '/assets/images/E3_Machine.jpg'),
            ('E4', 'E',1200000, 34800000, 100, '/assets/images/E4_Machine.jpg'),
            ('E5', 'E',3000000, 90000000,  80, '/assets/images/E5_Machine.jpg'),
            # G Series — Growth (balanced)
            ('G1', 'G',  55000,  1100000, 100, '/assets/images/G1_Machine.jpg'),
            ('G2', 'G', 200000,  4400000, 100, '/assets/images/G2_Machine.jpg'),
            ('G3', 'G', 500000, 12500000, 100, '/assets/images/G3_Machine.jpg'),
            ('G4', 'G', 950000, 26600000,  95, '/assets/images/G4_Machine.jpg'),
            ('G5', 'G',1800000, 54000000,  95, '/assets/images/G5_Machine.jpg'),
            # Z Series — Elite (highest ROI, longest lock)
            ('Z1', 'Z',  60000,  1620000, 180, '/assets/images/Z1_Machine.jpg'),
            ('Z2', 'Z', 180000,  5310000, 180, '/assets/images/Z2_Machine.jpg'),
            ('Z3', 'Z', 500000, 16200000, 180, '/assets/images/Z3_Machine.jpg'),
            ('Z4', 'Z',1000000, 34200000, 180, '/assets/images/Z4_Machine.jpg'),
            ('Z5', 'Z',2500000, 90000000, 180, '/assets/images/Z5_Machine.jpg'),
            # VIP Series — Exclusive (short lock, VIP only)
            ('VIP-1',     'VIP',  20000,   40000,  20, '/assets/images/VIP_1.jpg'),
            ('VIP-1 PRO', 'VIP',  30000,  120000,  40, '/assets/images/VIP_1.jpg'),
            ('VIP-1 MAX', 'VIP', 200000, 6960000, 120, '/assets/images/VIP_1.jpg'),
            ('VIP-2',     'VIP',  50000,  110000,  20, '/assets/images/VIP_2.jpg'),
            ('VIP-2 PRO', 'VIP',  80000,  320000,  40, '/assets/images/VIP_2.jpg'),
        ]
        cur.executemany(
            "INSERT INTO machines (id, series, price, income, lock, image_url) VALUES (%s,%s,%s,%s,%s,%s)",
            default_machines
        )

    # ── Seed a default announcement if table is empty ─────────────────────────
    cur.execute("SELECT COUNT(*) FROM messages")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO messages (text) VALUES (%s)",
                    ('Welcome to Future AI Hub! Deposit via MTN or Airtel Mobile Money.',))

    conn.commit()
    cur.close()
    conn.close()
    print("[DB] All tables ready (Supabase/Postgres).")
    
