# Hub — Backend

## Folder Structure

```
faihub/
│
├── run.py                  ← Start the app from here
├── app.py                  ← Flask app factory + route registration
├── requirements.txt
├── .env.example            ← Copy to .env and fill in your keys
├── .env                    ← YOUR secrets (never commit this)
├── faihub.db               ← SQLite database (auto-created on first run)
│
├── db/
│   ├── __init__.py
│   └── database.py         ← DB connection + all CREATE TABLE IF NOT EXISTS
│
├── middleware/
│   ├── __init__.py
│   └── auth.py             ← Session token check, @login_required decorator
│
├── routes/
│   ├── __init__.py
│   ├── auth.py             ← /api/register  /api/login  /api/logout
│   ├── user.py             ← /api/me  /api/profile/nick  /api/messages  /api/salary/claim
│   ├── deposit.py          ← /api/deposit/verify  (Flutterwave server-side check)
│   ├── withdraw.py         ← /api/withdraw
│   ├── machines.py         ← /api/machines  /api/my-machines  /api/machines/buy
│   ├── raffle.py           ← /api/raffle  /api/raffle/records
│   └── transactions.py     ← /api/transactions
│
└── frontend/               ← Drop all your HTML/CSS/JS files here
    ├── index.html
    ├── home.html
    ├── my.html
    ├── login.html
    ├── ai.html
    ├── income.html
    ├── bill.html
    ├── raffle.html
    ├── settings.html
    └── assets/
        ├── shared.css
        └── nav.js
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # then edit .env with your keys
python run.py
```

Database and all tables are created automatically on first run.

## Keys

| Key | Where to get it | Where it goes |
|---|---|---|
| SECRET_KEY | Any long random string | .env |
| FLW_SECRET_KEY | Flutterwave Dashboard → Settings → API | .env |
| FLW_PUBLIC_KEY | Same dashboard | frontend/my.html → const FLW_PUBLIC_KEY = '' |

## Production

```bash
gunicorn run:app --bind 0.0.0.0:5000 --workers 2
```
