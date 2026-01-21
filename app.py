import os
import uuid
import requests
import zipfile
import tempfile
import threading
import logging
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template_string, redirect, session, url_for
from functools import wraps
import mysql.connector
from mysql.connector import pooling

# === KONFIGURACJA ŚRODOWISKOWA ===
LEAKS_DB_CONFIG = {
    "host": os.getenv("LEAKS_DB_HOST", "136.243.54.157"),
    "port": int(os.getenv("LEAKS_DB_PORT", "25618")),
    "user": os.getenv("LEAKS_DB_USER", "admin_cold"),
    "password": os.getenv("LEAKS_DB_PASS", "Wyciek12"),
    "database": os.getenv("LEAKS_DB_NAME", "cold_search_db"),
    "charset": "utf8mb4",
    "autocommit": True
}

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wcshypmsurncfufbojvp.supabase.co").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "TWOJ_KLUCZ_SUPABASE").strip()
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", str(uuid.uuid4()))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === POOL POŁĄCZEŃ MARIADB ===
try:
    db_pool = mysql.connector.pooling.MySQLConnectionPool(pool_name="p", pool_size=10, **LEAKS_DB_CONFIG)
except Exception as e:
    logger.error(f"Błąd MariaDB: {e}")

def get_db(): return db_pool.get_connection()

# === POMOCNICY SUPABASE ===

def supabase_get(table, params=""):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=SUPABASE_HEADERS)
    return r.json() if r.status_code == 200 else []

def supabase_post(table, data):
    return requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SUPABASE_HEADERS, json=data)

def supabase_delete(table, query_params):
    return requests.delete(f"{SUPABASE_URL}/rest/v1/{table}?{query_params}", headers=SUPABASE_HEADERS)

# === MIDDLEWARE ===
def get_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()

def check_access(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = get_ip()
        # Sprawdź bany
        banned = supabase_get("banned_ips", f"ip=eq.{ip}")
        if banned:
            return jsonify({"success": False, "message": "Twoje IP jest zbanowane"}), 403
        return f(*args, **kwargs)
    return decorated

# === ENDPOINTY DLA CLIENT.PY ===

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"success": True, "version": "2.0.1", "status": "online"})

@app.route("/api/auth", methods=["POST"])
@check_access
def api_auth():
    data = request.json
    key = data.get("key")
    lics = supabase_get("licenses", f"key=eq.{key}")
    
    if not lics:
        return jsonify({"success": False, "message": "Niepoprawny klucz"}), 401
    
    lic = lics[0]
    expiry = datetime.fromisoformat(lic['expiry'].replace('Z', '+00:00'))
    
    if datetime.now(timezone.utc) > expiry:
        return jsonify({"success": False, "message": "Klucz wygasł"}), 401
    
    return jsonify({"success": True, "message": "Autoryzacja pomyślna"})

@app.route("/api/license-info", methods=["POST"])
@check_access
def api_info():
    key = request.json.get("key")
    lics = supabase_get("licenses", f"key=eq.{key}")
    if not lics: return jsonify({"success": False}), 404
    
    lic = lics[0]
    return jsonify({
        "success": True,
        "type": lic.get("type", "Premium"),
        "expiry": lic["expiry"],
        "usage_count": lic.get("usage", 0)
    })

@app.route("/api/search", methods=["POST"])
@check_access
def api_search():
    data = request.json
    query = data.get("query", "").strip()
    key = data.get("key")
    
    # Weryfikacja klucza przy wyszukiwaniu
    lics = supabase_get("licenses", f"key=eq.{key}")
    if not lics: return jsonify({"success": False, "message": "Błąd licencji"}), 403

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    # Full-text search na MariaDB
    cursor.execute("SELECT data, source FROM leaks WHERE MATCH(data) AGAINST(%s IN BOOLEAN MODE) LIMIT 100", (f"*{query}*",))
    results = cursor.fetchall()
    conn.close()
    
    # Zwiększ licznik użycia w Supabase (opcjonalnie)
    supabase_post("logs", {"ip": get_ip(), "query": query, "key": key})
    
    return jsonify({"success": True, "results": results})

# === PANEL ADMINISTRATORA ===

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST" and request.form.get("password") == ADMIN_PASSWORD:
        session['admin'] = True
        return redirect(url_for('admin_dashboard'))
    return '<h1>Panel Cold Search</h1><form method="post">Hasło: <input type="password" name="password"><button>Zaloguj</button></form>'

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get('admin'): return redirect(url_for('admin_login'))
    
    lics = supabase_get("licenses", "order=expiry.desc")
    bans = supabase_get("banned_ips")
    
    return render_template_string("""
    <!DOCTYPE html>
    <style>
        body { font-family: sans-serif; background: #0f0f18; color: white; padding: 20px; }
        .card { background: #1a1a25; padding: 20px; border-radius: 10px; margin-bottom: 20px; }
        input, button { padding: 8px; margin: 5px 0; border-radius: 4px; border: none; }
        button { background: #00f2ff; color: black; cursor: pointer; font-weight: bold; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { text-align: left; padding: 10px; border-bottom: 1px solid #333; }
        .del-btn { background: #ff3c78; color: white; padding: 4px 8px; }
    </style>
    <h1>Manager Cold Search Premium</h1>
    
    <div class="card">
        <h3>Generuj Licencję</h3>
        <form action="/admin/add_license" method="post">
            Dni: <input type="number" name="days" value="30" style="width: 60px">
            Typ: <select name="type"><option>Premium</option><option>Lifetime</option></select>
            <button type="submit">Dodaj Klucz</button>
        </form>
    </div>

    <div class="card">
        <h3>Aktywne Licencje</h3>
        <table>
            <tr><th>Klucz</th><th>Typ</th><th>Wygasa</th><th>Akcja</th></tr>
            {% for l in lics %}
            <tr>
                <td>{{l.key}}</td>
                <td>{{l.type}}</td>
                <td>{{l.expiry[:10]}}</td>
                <td><a href="/admin/del_license/{{l.key}}"><button class="del-btn">Usuń</button></a></td>
            </tr>
            {% endfor %}
        </table>
    </div>

    <div class="card">
        <h3>Zarządzanie Banami IP</h3>
        <form action="/admin/add_ban" method="post">
            IP: <input type="text" name="ip" placeholder="0.0.0.0">
            Powód: <input type="text" name="reason" placeholder="Abuse">
            <button type="submit" style="background: #ff3c78;">Zbanuj</button>
        </form>
        <table>
            {% for b in bans %}
            <tr><td>{{b.ip}}</td><td>{{b.reason}}</td><td><a href="/admin/del_ban/{{b.ip}}">Odblokuj</a></td></tr>
            {% endfor %}
        </table>
    </div>
    """, lics=lics, bans=bans)

@app.route("/admin/add_license", methods=["POST"])
def add_lic():
    if not session.get('admin'): return "Brak dostępu"
    new_key = "COLD-" + str(uuid.uuid4()).upper()[:12]
    days = int(request.form.get("days", 30))
    expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    supabase_post("licenses", {"key": new_key, "expiry": expiry, "type": request.form.get("type")})
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/del_license/<key>")
def del_lic(key):
    if not session.get('admin'): return "Brak dostępu"
    supabase_delete("licenses", f"key=eq.{key}")
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/add_ban", methods=["POST"])
def add_ban():
    if not session.get('admin'): return "Brak dostępu"
    supabase_post("banned_ips", {"ip": request.form.get("ip"), "reason": request.form.get("reason")})
    return redirect(url_for('admin_dashboard'))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
