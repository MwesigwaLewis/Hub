import os
from flask import Flask, send_from_directory
from db.database import init_db
from routes.auth import auth_bp
from routes.user import user_bp
from routes.deposit import deposit_bp
from routes.withdraw import withdraw_bp
from routes.machines import machines_bp
from routes.raffle import raffle_bp
from routes.transactions import transactions_bp
from routes.chat import chat_bp
from routes.admin import admin_bp

# ── App factory ──────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='frontend', static_url_path='')

app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-production')

# ── Auto-create all DB tables on startup ─────────────────────────────────────
with app.app_context():
    init_db()

# ── Register API blueprints ───────────────────────────────────────────────────
app.register_blueprint(auth_bp)
app.register_blueprint(user_bp)
app.register_blueprint(deposit_bp)
app.register_blueprint(withdraw_bp)
app.register_blueprint(machines_bp)
app.register_blueprint(raffle_bp)
app.register_blueprint(transactions_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(admin_bp)

# ── Serve frontend HTML files ─────────────────────────────────────────────────
@app.route('/')
@app.route('/index.html')
def index():
    return send_from_directory('frontend', 'index.html')

@app.route('/<path:filename>')
def frontend(filename):
    return send_from_directory('frontend', filename)

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true',
            host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
