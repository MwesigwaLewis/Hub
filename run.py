#!/usr/bin/env python3
"""
Entry point — loads .env then starts the Flask app.
Run with:  python run.py
Prod:      gunicorn run:app
"""
from dotenv import load_dotenv
load_dotenv()          # reads .env file into os.environ before anything else

from app import app    # noqa: E402  (import after env is loaded)

if __name__ == '__main__':
    import os
    app.run(
        debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true',
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000))
    )
