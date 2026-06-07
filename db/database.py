import sqlite3
import os

DB_PATH = os.environ.get('DB_PATH', 'faihub.db')

def get_db():
    """Return a database connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    """
    Called once on app startup.
    Creates every table if it doesn't already exist — safe to run repeatedly.
    """
    conn = get_db()
    cur = conn.cursor()

    # ── Users ─────────────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            phone       TEXT    NOT NULL UNIQUE,
            password    TEXT    NOT NULL,
            nick        TEXT,
            avatar_url  TEXT,
            email       TEXT,
            vip_level   INTEGER NOT NULL DEFAULT 1,
            balance     REAL    NOT NULL DEFAULT 0.0,
            wallet      REAL    NOT NULL DEFAULT 0.0,
            total_deposit  REAL NOT NULL DEFAULT 0.0,
            total_withdraw REAL NOT NULL DEFAULT 0.0,
            ai_income      REAL NOT NULL DEFAULT 0.0,
            today_earnings REAL NOT NULL DEFAULT 0.0,
            team_income    REAL NOT NULL DEFAULT 0.0,
            invite_count   INTEGER NOT NULL DEFAULT 0,
            team_count     INTEGER NOT NULL DEFAULT 0,
            invite_code    TEXT UNIQUE,
            invited_by     INTEGER REFERENCES users(id),
            raffle_ready   INTEGER NOT NULL DEFAULT 0,
            last_salary    REAL    NOT NULL DEFAULT 0.0,
            this_salary    REAL    NOT NULL DEFAULT 0.0,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Sessions ──────────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Deposit transactions (Flutterwave verified only) ──────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deposit_transactions (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id            INTEGER NOT NULL REFERENCES users(id),
            tx_ref             TEXT    NOT NULL UNIQUE,
            flw_transaction_id TEXT    UNIQUE,
            amount             REAL    NOT NULL,
            network            TEXT,
            status             TEXT    NOT NULL DEFAULT 'pending',
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            verified_at        DATETIME
        )
    """)

    # ── Withdraw requests ─────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdraw_requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            amount     REAL    NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            processed_at DATETIME
        )
    """)

    # ── AI Machines catalogue ─────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS machines (
            id          TEXT PRIMARY KEY,
            series      TEXT    NOT NULL DEFAULT 'A',
            price       REAL    NOT NULL,
            income      REAL    NOT NULL,
            lock        INTEGER NOT NULL DEFAULT 30,
            image_url   TEXT,
            sold        INTEGER NOT NULL DEFAULT 0
        )
    """)

    # ── User-owned machines ───────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_machines (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id),
            machine_id   TEXT    NOT NULL REFERENCES machines(id),
            purchase_price REAL  NOT NULL,
            daily_income REAL    NOT NULL,
            total_income REAL    NOT NULL,
            earned       REAL    NOT NULL DEFAULT 0.0,
            status       TEXT    NOT NULL DEFAULT 'running',
            bought_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at   DATETIME
        )
    """)

    # ── General transactions ledger ───────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            type        TEXT    NOT NULL,
            amount      REAL    NOT NULL,
            note        TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Raffle records ────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS raffle_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            user_phone  TEXT    NOT NULL,
            prize       REAL    NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Messages / announcements ──────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            text        TEXT    NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Seed default machines if table is empty ───────────────────────────────
    cur.execute("SELECT COUNT(*) FROM machines")
    if cur.fetchone()[0] == 0:
        default_machines = [
            # A Series — Entry level (short lock, low price)
            ('A1', 'A',   500,    650,  30),
            ('A2', 'A',  1000,   1350,  30),
            ('A3', 'A',  2000,   2800,  30),
            # B Series — Beginner (medium lock)
            ('B1', 'B',  5000,   7200,  60),
            ('B2', 'B', 10000,  15000,  60),
            # C Series — Intermediate (longer lock)
            ('C1', 'C', 25000,  40000,  90),
            ('C2', 'C', 50000,  85000,  90),
            # E Series — Advanced (high ROI, longer lock)
            ('E1', 'E',  60000,  1500000, 120),
            ('E2', 'E', 180000,  4680000, 120),
            ('E3', 'E', 600000, 16200000, 100),
            ('E4', 'E',1200000, 34800000, 100),
            ('E5', 'E',3000000, 90000000,  80),
            # G Series — Growth (balanced)
            ('G1', 'G',  55000,  1100000, 100),
            ('G2', 'G', 200000,  4400000, 100),
            ('G3', 'G', 500000, 12500000, 100),
            ('G4', 'G', 950000, 26600000,  95),
            ('G5', 'G',1800000, 54000000,  95),
            # Z Series — Elite (highest ROI, longest lock)
            ('Z1', 'Z',  60000,  1620000, 180),
            ('Z2', 'Z', 180000,  5310000, 180),
            ('Z3', 'Z', 500000, 16200000, 180),
            ('Z4', 'Z',1000000, 34200000, 180),
            ('Z5', 'Z',2500000, 90000000, 180),
            # VIP Series — Exclusive (short lock, VIP only)
            ('VIP-1',     'VIP',  20000,   40000,  20),
            ('VIP-1 PRO', 'VIP',  30000,  120000,  40),
            ('VIP-1 MAX', 'VIP', 200000, 6960000, 120),
            ('VIP-2',     'VIP',  50000,  110000,  20),
            ('VIP-2 PRO', 'VIP',  80000,  320000,  40),
        ]
        cur.executemany(
            "INSERT INTO machines (id, series, price, income, lock) VALUES (?,?,?,?,?)",
            default_machines
        )

    # ── Seed a default announcement if table is empty ─────────────────────────
    cur.execute("SELECT COUNT(*) FROM messages")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO messages (text) VALUES (?)",
                    ('Welcome to Future AI Hub! Deposit via MTN or Airtel Mobile Money.',))

    conn.commit()
    conn.close()
    print("[DB] All tables ready.")
