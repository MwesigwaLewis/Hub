import os
import secrets
import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import ConnectionPool

# Supabase gives you this under Project Settings -> Database -> Connection string.
# Use the "Transaction pooler" URI (port 6543) in production so you don't run out
# of direct Postgres connections; the direct URI (port 5432) is fine for local dev.
DATABASE_URL = os.environ.get('SUPABASE_DB_URL') or os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    raise RuntimeError(
        "No database URL found. Set SUPABASE_DB_URL (or DATABASE_URL) in your .env — "
        "get it from your Supabase project: Settings -> Database -> Connection string."
    )

# One connection pool per process, created once at import time and reused for
# the life of the worker. Previously every request called psycopg.connect()
# from scratch — a fresh TCP handshake + TLS negotiation + Postgres auth
# round trip (commonly 200ms-1s+ against a remote DB) on every single API
# call, twice per page load (once in middleware/auth.py to check the
# session, once in the route itself). Pulling an already-open connection out
# of this pool instead reduces that to an in-process handoff.
#   min_size: connections kept warm at all times
#   max_size: cap on how many concurrent DB connections one gunicorn worker
#             can hold — keep this comfortably under your Postgres/pooler's
#             connection limit if you run multiple workers
_pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=2,          # Supabase free tier — keep well under the 15-conn limit
    open=True,
    kwargs={"sslmode": "require", "row_factory": dict_row},
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

    @property
    def rowcount(self):
        return self._cur.rowcount


class _ConnWrapper:
    """Wraps a pooled psycopg connection so existing route code — db.execute(...),
    .fetchone()/.fetchall(), db.commit(), db.close() — keeps working unchanged.
    Translates sqlite-style '?' placeholders to psycopg's '%s' automatically.
    close() returns the connection to the pool instead of tearing it down."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        cur = self._conn.cursor(row_factory=dict_row)
        cur.execute(query.replace('?', '%s'), params)
        return _CursorWrapper(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        # Guard against handing back a connection mid-transaction (e.g. a
        # route raised before calling commit()/rollback()) — rollback() on
        # an already-clean connection is a harmless no-op.
        try:
            self._conn.rollback()
        except Exception:
            pass
        _pool.putconn(self._conn)


def get_db():
    """Return a pooled Postgres connection wrapped to match the previous
    sqlite3 interface used throughout the app (routes/*.py, middleware/auth.py)."""
    conn = _pool.getconn()
    return _ConnWrapper(conn)


def init_db():
    """
    Called once on app startup. Creates every table and runs migrations.
    Uses the pool instead of a raw psycopg.connect() so startup doesn't
    push the live connection count past Supabase's free-tier limit.
    """
    conn = _pool.getconn()
    # Explicitly use tuple_row here — the pool default is dict_row (for routes),
    # but init_db uses fetchone()[0] integer indexing on COUNT(*) results.
    cur  = conn.cursor(row_factory=tuple_row)

    # ── Users ─────────────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id             SERIAL PRIMARY KEY,
            phone          TEXT NOT NULL UNIQUE,
            password       TEXT NOT NULL,
            nick           TEXT NOT NULL DEFAULT 'Anon',
            avatar_url     TEXT,
            email          TEXT,
            vip_level      INTEGER NOT NULL DEFAULT 0,
            depositing_invites INTEGER NOT NULL DEFAULT 0,
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

    # ── Admins (separate identity from regular users, on purpose — a manager
    #    logging in should never be able to accidentally land in a customer
    #    session or vice versa) ───────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id           SERIAL PRIMARY KEY,
            username     TEXT NOT NULL UNIQUE,
            password     TEXT NOT NULL,
            name         TEXT NOT NULL DEFAULT 'Manager',
            role         TEXT NOT NULL DEFAULT 'manager',   -- 'super' or 'manager'
            manager_code TEXT UNIQUE,                        -- personal signup-link code
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            token      TEXT PRIMARY KEY,
            admin_id   INTEGER NOT NULL REFERENCES admins(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Support chat: one thread per user, admin replies into the same thread ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            sender      TEXT NOT NULL,                 -- 'user' or 'admin'
            body        TEXT NOT NULL,
            read_by_user  BOOLEAN NOT NULL DEFAULT FALSE,
            read_by_admin BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Reward codes: manager-created, redeemable by any number of DIFFERENT
    #    users (each only once each — enforced by the UNIQUE constraint below),
    #    only within the [valid_from, valid_until] window the manager set. ─────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reward_codes (
            id          SERIAL PRIMARY KEY,
            code        TEXT NOT NULL UNIQUE,
            amount      DOUBLE PRECISION NOT NULL,
            description TEXT,
            valid_from  TIMESTAMP NOT NULL,
            valid_until TIMESTAMP NOT NULL,
            active      BOOLEAN NOT NULL DEFAULT TRUE,
            created_by  INTEGER REFERENCES admins(id),
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reward_redemptions (
            id          SERIAL PRIMARY KEY,
            code_id     INTEGER NOT NULL REFERENCES reward_codes(id),
            user_id     INTEGER NOT NULL REFERENCES users(id),
            redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (code_id, user_id)
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

    # ── Migrations for databases created before these columns/defaults existed ──
    # Placed here, before any seed-data block below, so every column referenced
    # by seeding logic (e.g. admins.role, admins.manager_code) is guaranteed to
    # exist first — regardless of whether this is a fresh install or an
    # upgrade of a database that already has some of these tables.
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS depositing_invites INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE users ALTER COLUMN vip_level SET DEFAULT 0")
    cur.execute("ALTER TABLE user_machines ADD COLUMN IF NOT EXISTS lock_days INTEGER")
    cur.execute("""
        UPDATE user_machines SET lock_days = GREATEST(1, ROUND(EXTRACT(EPOCH FROM (expires_at - bought_at)) / 86400)::int)
        WHERE lock_days IS NULL
    """)
    cur.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'manager'")
    cur.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS manager_code TEXT UNIQUE")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS assigned_manager_id INTEGER REFERENCES admins(id)")
    # Manager profile picture + cross-visibility grant from super
    cur.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS avatar_url TEXT")
    cur.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS can_see_all BOOLEAN NOT NULL DEFAULT FALSE")
    # Group chat customisation — manager sets their group name and can toggle it on/off
    cur.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS group_name TEXT")
    cur.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS group_enabled BOOLEAN NOT NULL DEFAULT TRUE")
    cur.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS group_icon TEXT")  # custom group avatar URL
    # Activity audit log — every non-super admin action, visible only to super
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_activity_log (
            id         SERIAL PRIMARY KEY,
            admin_id   INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
            action     TEXT NOT NULL,
            detail     TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_admin_activity_admin_id ON admin_activity_log(admin_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_admin_activity_created ON admin_activity_log(created_at DESC)")

    # ── Group chat: one broadcast channel per manager ─────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_chat_messages (
            id           SERIAL PRIMARY KEY,
            manager_id   INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
            body         TEXT NOT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gcm_manager_id ON group_chat_messages(manager_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gcm_created_at ON group_chat_messages(created_at DESC)")
    # Tracks the last group message each user has read (for unread count)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_chat_read (
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            manager_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
            last_read_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, manager_id)
        )
    """)
    # Backfill manager_code for any admin rows created before this existed.
    cur.execute("SELECT id FROM admins WHERE manager_code IS NULL")
    for (aid,) in cur.fetchall():
        cur.execute("UPDATE admins SET manager_code=%s WHERE id=%s", (secrets.token_hex(4).upper(), aid))

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

    # ── Seed a default admin if the table is empty ─────────────────────────────
    # There's no self-serve admin signup (by design — see routes/admin.py), so
    # something has to create the first account. Override via env vars before
    # first deploy; otherwise a default is created and logged so you notice it.
    # It's seeded as role='super' — the only role that can see every batch,
    # manage other managers, and reassign users between them.
    cur.execute("SELECT COUNT(*) FROM admins")
    if cur.fetchone()[0] == 0:
        from werkzeug.security import generate_password_hash
        default_user = os.environ.get('ADMIN_USERNAME', 'admin')
        default_pass = os.environ.get('ADMIN_PASSWORD', 'changeme123')
        cur.execute(
            "INSERT INTO admins (username, password, name, role, manager_code) VALUES (%s, %s, %s, 'super', %s)",
            (default_user, generate_password_hash(default_pass), 'Manager', secrets.token_hex(4).upper())
        )
        print(f"[DB] Seeded default admin '{default_user}' with role=super. "
              f"{'Using ADMIN_PASSWORD from env.' if os.environ.get('ADMIN_PASSWORD') else '⚠️  Using default password changeme123 — log in at /admin/login.html and change it immediately.'}")

    # ── Guarantee: no user is ever left unassigned ──────────────────────────────
    # Defense in depth on top of the registration-time fallback chain in
    # routes/auth.py — covers users created before manager assignment
    # existed, or anyone orphaned by a deleted manager slipping through.
    cur.execute("SELECT id FROM admins WHERE role='super' ORDER BY created_at ASC LIMIT 1")
    main_manager_row = cur.fetchone()
    if main_manager_row:
        cur.execute(
            "UPDATE users SET assigned_manager_id=%s WHERE assigned_manager_id IS NULL",
            (main_manager_row[0],)   # tuple row — index 0 = id column
        )

    # ── Indexes ───────────────────────────────────────────────────────────────
    # These columns are hit on essentially every request (session check on
    # every page load, my-machines / team / transaction history lookups) but
    # had no index, meaning Postgres had to scan the whole table for each
    # lookup as row counts grew. phone/invite_code/token already have
    # implicit indexes from their UNIQUE/PRIMARY KEY constraints.
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_invited_by ON users(invited_by)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_assigned_manager_id ON users(assigned_manager_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_machines_user_id ON user_machines(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_machines_user_status ON user_machines(user_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_deposit_tx_user_id ON deposit_transactions(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_withdraw_req_user_id ON withdraw_requests(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raffle_records_user_id ON raffle_records(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_user_id ON chat_messages(user_id, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reward_redemptions_code_id ON reward_redemptions(code_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reward_redemptions_user_id ON reward_redemptions(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_admin_sessions_admin_id ON admin_sessions(admin_id)")

    conn.commit()
    cur.close()
    _pool.putconn(conn)   # return to pool — not conn.close() which would destroy it
    print("[DB] All tables ready (Supabase/Postgres).")
