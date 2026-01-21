
import os
import uuid
import requests
import zipfile
import tempfile
import threading
import json
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template_string, redirect, session
from functools import wraps
import logging
import mysql.connector
from mysql.connector import Error

# === KONFIGURACJA ===
LEAKS_DB_HOST = os.getenv("LEAKS_DB_HOST", "136.243.54.157")
LEAKS_DB_PORT = int(os.getenv("LEAKS_DB_PORT", "25618"))
LEAKS_DB_USER = os.getenv("LEAKS_DB_USER", "admin_cold")
LEAKS_DB_PASS = os.getenv("LEAKS_DB_PASS", "Wyciek12")
LEAKS_DB_NAME = os.getenv("LEAKS_DB_NAME", "cold_search_db")

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wcshypmsurncfufbojvp.supabase.co").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_secret_Ci0yyib3FCJW3GMivhX3XA_D2vHmhpP").strip()
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "secure_admin_password_2026")
SENSITIVE_IPS = {"37.47.217.112", "88.156.189.157"}

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_secure_key")

# === LOGOWANIE ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === FUNKCJE POMOCNICZE ===
def sanitize_ip(ip):
    if not ip:
        return "unknown"
    if ip in SENSITIVE_IPS:
        return "127.0.0.1"
    return ip

def get_client_ip():
    if request.headers.get('X-Forwarded-For'):
        ip = request.headers.get('X-Forwarded-For').split(',')[0].strip()
    else:
        ip = request.remote_addr or '127.0.0.1'
    return sanitize_ip(ip)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated_function

def get_leaks_db_connection():
    try:
        return mysql.connector.connect(
            host=LEAKS_DB_HOST,
            port=LEAKS_DB_PORT,
            user=LEAKS_DB_USER,
            password=LEAKS_DB_PASS,
            database=LEAKS_DB_NAME,
            charset='utf8mb4',
            autocommit=True
        )
    except Error as e:
        logger.error(f"Błąd połączenia z bazą wycieków: {e}")
        raise

# === ZARZĄDZANIE LICENCJAMI ===
class LicenseManager:
    def generate(self, days, license_type="premium"):
        new_key = "COLD-" + uuid.uuid4().hex.upper()[:12]
        expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        payload = {
            "key": new_key,
            "active": True,
            "expiry": expiry,
            "license_type": license_type,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            r = requests.post(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, json=payload)
            return new_key if r.status_code in [200, 201] else None
        except Exception as e:
            logger.error(f"Błąd generowania klucza: {e}")
            return None

    def validate(self, key, ip):
        try:
            r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
            if r.status_code != 200 or not r.json():
                return {"success": False, "message": "Nieprawidłowy klucz"}
            
            lic = r.json()[0]
            expiry = datetime.fromisoformat(lic["expiry"].replace('Z', '+00:00'))
            
            if not lic.get("active", False) or datetime.now(timezone.utc) > expiry:
                return {"success": False, "message": "Klucz wygasł"}
            
            if not lic.get("ip"):
                requests.patch(f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}", headers=SUPABASE_HEADERS, json={"ip": ip})
                return {"success": True, "message": "IP powiązane"}
            
            if lic["ip"] != ip:
                return {"success": False, "message": "Inne IP przypisane"}
            
            return {"success": True, "message": "OK"}
        except Exception as e:
            logger.error(f"Błąd walidacji klucza: {e}")
            return {"success": False, "message": "Błąd serwera"}

lic_mgr = LicenseManager()

def import_leaks_to_mysql(file_path, source_name):
    added_count = 0
    try:
        conn = get_leaks_db_connection()
        cursor = conn.cursor()
        
        # Tworzenie tabeli jeśli nie istnieje
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaks (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            data TEXT NOT NULL,
            source VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_data (data(255)),
            INDEX idx_source (source)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        # Import danych
        insert_query = "INSERT IGNORE INTO leaks (data, source) VALUES (%s, %s)"
        batch = []
        
        with open(file_path, "r", errors="replace") as f:
            for line in f:
                clean_line = line.strip()
                if clean_line and len(clean_line) <= 1000:
                    batch.append((clean_line, source_name))
                    if len(batch) >= 500:
                        cursor.executemany(insert_query, batch)
                        added_count += cursor.rowcount
                        batch = []
        
        if batch:
            cursor.executemany(insert_query, batch)
            added_count += cursor.rowcount
            
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Błąd importu: {e}")
    return added_count

def import_leaks_worker(zip_url, callback=None):
    try:
        response = requests.get(zip_url, stream=True, timeout=60)
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "import.zip")
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(8192):
                    f.write(chunk)
            
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(tmp_dir)
            
            total_added = 0
            for root, _, files in os.walk(tmp_dir):
                for file_name in files:
                    if any(file_name.lower().endswith(ext) for ext in [".txt", ".csv", ".log"]):
                        file_path = os.path.join(root, file_name)
                        total_added += import_leaks_to_mysql(file_path, file_name)
            
            if callback:
                callback(total_added)
    except Exception as e:
        logger.error(f"Błąd importu: {e}")
        if callback:
            callback(-1)

# === BEZPIECZEŃSTWO ===
def check_ip_ban(ip):
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/banned_ips", headers=SUPABASE_HEADERS, params={"ip": f"eq.{ip}"})
        if r.status_code == 200 and r.json():
            ban = r.json()[0]
            if ban.get("expires_at"):
                expires_at = datetime.fromisoformat(ban["expires_at"].replace('Z', '+00:00'))
                if datetime.now(timezone.utc) < expires_at:
                    return True
    except Exception as e:
        logger.error(f"Błąd sprawdzania bana: {e}")
    return False

def ban_ip(ip, reason="Nieokreślony powód", duration_hours=24):
    try:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=duration_hours)).isoformat()
        payload = {"ip": ip, "reason": reason, "expires_at": expires_at}
        requests.post(f"{SUPABASE_URL}/rest/v1/banned_ips", headers=SUPABASE_HEADERS, json=payload)
        return True
    except Exception as e:
        logger.error(f"Błąd banowania IP: {e}")
        return False

def check_rate_limit(ip, max_requests=10, period_minutes=1):
    try:
        timestamp = (datetime.now(timezone.utc) - timedelta(minutes=period_minutes)).isoformat()
        r = requests.get(f"{SUPABASE_URL}/rest/v1/rate_limits", headers=SUPABASE_HEADERS, 
                        params={"ip": f"eq.{ip}", "timestamp": f"gte.{timestamp}"})
        if r.status_code == 200:
            count = len(r.json())
            if count >= max_requests:
                ban_ip(ip, f"Przekroczono limit żądań ({count}/{max_requests})", 1)
                return False
            return True
    except Exception as e:
        logger.error(f"Błąd rate limit: {e}")
    return True

# === PANEL ADMINA ===
ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Cold Search Admin</title>
    <style>
        body { font-family: Arial, sans-serif; background: #0f0f1a; color: #e6e6ff; margin: 0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .card { background: #1a1a2e; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #2d2d44; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 25px; }
        .stat-card { background: #25253a; padding: 15px; border-radius: 8px; text-align: center; }
        .stat-value { font-size: 24px; font-weight: bold; color: #00f2ff; }
        .table-container { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #2d2d44; }
        th { background: #25253a; color: #a0a0c0; font-weight: 600; }
        .btn { padding: 10px 15px; background: #00f2ff; color: #000; border: none; border-radius: 6px; cursor: pointer; margin-right: 8px; }
        .btn-danger { background: #ff3366; }
        .btn-warning { background: #ffcc00; }
        .status-active { color: #00ffaa; }
        .status-inactive { color: #ff3366; }
        .login-card { max-width: 400px; margin: 100px auto; background: #1a1a2e; padding: 30px; border-radius: 8px; }
    </style>
</head>
<body>
    <div class="container">
        {% if not authenticated %}
        <div class="login-card">
            <h2>Panel Administratora</h2>
            {% if login_error %}
            <p style="color: #ff3366;">Nieprawidłowe hasło</p>
            {% endif %}
            <form method="POST">
                <input type="password" name="password" placeholder="Hasło administratora" required style="width: 100%; padding: 10px; margin: 10px 0; border-radius: 4px; border: 1px solid #2d2d44; background: #25253a; color: white;">
                <button type="submit" class="btn" style="width: 100%;">Zaloguj</button>
            </form>
        </div>
        {% else %}
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px;">
            <h1>Cold Search Premium</h1>
            <div>
                <span>{{ admin_name }}</span>
                <form method="POST" action="/admin/logout" style="display: inline;">
                    <button type="submit" class="btn btn-danger">Wyloguj</button>
                </form>
            </div>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div>Rekordy w bazie</div>
                <div class="stat-value">{{ db_count }}</div>
            </div>
            <div class="stat-card">
                <div>Aktywne licencje</div>
                <div class="stat-value">{{ active_keys }}</div>
            </div>
            <div class="stat-card">
                <div>Wyszukiwań dziś</div>
                <div class="stat-value">{{ searches_today }}</div>
            </div>
            <div class="stat-card">
                <div>Zbanowane IP</div>
                <div class="stat-value">{{ banned_ips }}</div>
            </div>
        </div>
        
        <div class="card">
            <h2>Generowanie nowej licencji</h2>
            <form method="POST" action="/admin/generate">
                <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 15px;">
                    <div>
                        <label>Dni ważności:</label>
                        <input type="number" name="days" value="30" min="1" max="365" required style="width: 100%; padding: 8px; border-radius: 4px; border: 1px solid #2d2d44; background: #25253a; color: white;">
                    </div>
                    <div>
                        <label>Typ licencji:</label>
                        <select name="license_type" style="width: 100%; padding: 8px; border-radius: 4px; border: 1px solid #2d2d44; background: #25253a; color: white;">
                            <option value="premium">Premium</option>
                            <option value="standard">Standard</option>
                        </select>
                    </div>
                </div>
                <button type="submit" class="btn" style="margin-top: 15px;">Generuj klucz</button>
            </form>
            {% if new_key %}
            <div style="margin-top: 20px; padding: 15px; background: rgba(0, 242, 255, 0.1); border: 1px dashed #00f2ff; border-radius: 6px;">
                <strong>Nowy klucz:</strong> {{ new_key }}
            </div>
            {% endif %}
        </div>
        
        <div class="card">
            <h2>Import danych z ZIP</h2>
            <form method="POST" action="/admin/import_zip">
                <label>URL pliku ZIP:</label>
                <input type="url" name="zip_url" placeholder="https://example.com/data.zip" required style="width: 100%; padding: 8px; margin: 8px 0; border-radius: 4px; border: 1px solid #2d2d44; background: #25253a; color: white;">
                <button type="submit" class="btn">Rozpocznij import</button>
            </form>
        </div>
        
        <div class="card">
            <h2>Zarządzanie licencjami</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Klucz</th>
                            <th>Typ</th>
                            <th>IP</th>
                            <th>Status</th>
                            <th>Akcje</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for lic in licenses %}
                        <tr>
                            <td style="color: #00f2ff; font-weight: bold;">{{ lic.key[:10] }}...</td>
                            <td>{{ lic.license_type }}</td>
                            <td>{{ lic.ip or 'niepowiązane' }}</td>
                            <td>
                                <span class="status-{{ 'active' if lic.is_active else 'inactive' }}">
                                    {{ 'Aktywna' if lic.is_active else 'Nieaktywna' }}
                                </span>
                            </td>
                            <td>
                                <form method="POST" action="/admin/toggle_license" style="display: inline;">
                                    <input type="hidden" name="key" value="{{ lic.key }}">
                                    <button type="submit" class="btn {% if lic.active %}btn-warning{% else %}btn{% endif %}">
                                        {{ "Dezaktywuj" if lic.active else "Aktywuj" }}
                                    </button>
                                </form>
                                <form method="POST" action="/admin/delete_license" style="display: inline;">
                                    <input type="hidden" name="key" value="{{ lic.key }}">
                                    <button type="submit" class="btn btn-danger" onclick="return confirm('Na pewno usunąć?')">Usuń</button>
                                </form>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="card">
            <h2>Logi systemowe</h2>
            <div style="max-height: 300px; overflow-y: auto; font-family: monospace; font-size: 14px;">
                {% for log in logs %}
                <div style="margin-bottom: 4px; {% if 'ERROR' in log or 'BŁĄD' in log %}color: #ff3366;{% elif 'SUCCES' in log or 'OK' in log %}color: #00ffaa;{% endif %}">
                    {{ log }}
                </div>
                {% endfor %}
            </div>
            <form method="POST" action="/admin/clear_logs" style="margin-top: 15px;">
                <button type="submit" class="btn btn-danger" onclick="return confirm('Na pewno wyczyścić logi?')">Wyczyść logi</button>
            </form>
        </div>
        {% endif %}
    </div>
</body>
</html>
"""

# === ENDPOINTY API ===
@app.route("/api/auth", methods=["POST"])
def api_auth():
    try:
        data = request.json
        key = data.get("key")
        ip = get_client_ip()
        
        if not key:
            return jsonify({"success": False, "message": "Brak klucza"}), 400
            
        if check_ip_ban(ip):
            return jsonify({"success": False, "message": "Twój adres IP został zbanowany"}), 403
            
        if not check_rate_limit(ip):
            return jsonify({"success": False, "message": "Przekroczono limit żądań"}), 429
            
        return jsonify(lic_mgr.validate(key, ip))
    except Exception as e:
        logger.error(f"Błąd auth: {e}")
        return jsonify({"success": False, "message": "Błąd serwera"}), 500

@app.route("/api/license-info", methods=["POST"])
def api_license_info():
    try:
        data = request.json
        key = data.get("key")
        ip = get_client_ip()
        
        if not key or not ip:
            return jsonify({"success": False, "message": "Brak klucza lub IP"}), 400
            
        auth = lic_mgr.validate(key, ip)
        if not auth["success"]:
            return jsonify({"success": False, "message": auth["message"]}), 403
            
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
        if r.status_code != 200 or not r.json():
            return jsonify({"success": False, "message": "Licencja nie znaleziona"}), 404
            
        lic = r.json()[0]
        return jsonify({
            "success": True,
            "info": {
                "license_type": lic.get("license_type", "standard"),
                "expiration_date": lic["expiry"].split("T")[0],
                "query_limit": "nieograniczony",
                "queries_used": 0,
                "last_search": "Nigdy"
            }
        })
    except Exception as e:
        logger.error(f"Błąd license-info: {e}")
        return jsonify({"success": False, "message": "Błąd serwera"}), 500

@app.route("/api/search", methods=["POST"])
def api_search():
    try:
        data = request.json
        key = data.get("key")
        query = data.get("query", "").strip()
        ip = get_client_ip()
        
        if not key or not ip:
            return jsonify({"success": False, "message": "Brak klucza lub IP"}), 400
            
        auth = lic_mgr.validate(key, ip)
        if not auth["success"]:
            return jsonify(auth), 403
            
        try:
            # Logowanie wyszukiwania
            log_payload = {"key": key, "query": query[:200], "ip": ip}
            requests.post(f"{SUPABASE_URL}/rest/v1/search_logs", headers=SUPABASE_HEADERS, json=log_payload)
        except Exception as e:
            logger.error(f"Błąd logowania: {e}")
        
        # Wyszukiwanie w bazie
        conn = get_leaks_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT source, data FROM leaks WHERE data LIKE %s LIMIT 150", (f"%{query}%",))
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return jsonify({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Błąd search: {e}")
        return jsonify({"success": False, "message": "Błąd bazy danych"}), 500

@app.route("/api/status", methods=["GET"])
def api_status():
    try:
        conn = get_leaks_db_connection()
        conn.close()
        return jsonify({"success": True, "status": "online", "version": "2.5.0"})
    except Exception as e:
        logger.error(f"Błąd statusu: {e}")
        return jsonify({"success": False, "status": "offline"})

# === PANEL ADMINISTRACYJNY ===
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            session["admin_name"] = "Administrator"
            return redirect("/admin")
        return render_template_string(ADMIN_HTML, authenticated=False, login_error=True)
    return render_template_string(ADMIN_HTML, authenticated=False)

@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect("/admin/login")

@app.route("/admin")
@admin_required
def admin_dashboard():
    try:
        # Liczba rekordów w bazie
        conn = get_leaks_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM leaks")
        db_count = cursor.fetchone()[0]
        conn.close()
        
        # Licencje
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"order": "created_at.desc", "limit": 10})
        licenses = []
        if r.status_code == 200:
            now = datetime.now(timezone.utc)
            for lic in r.json():
                expiry = datetime.fromisoformat(lic["expiry"].replace('Z', '+00:00'))
                licenses.append({
                    "key": lic["key"],
                    "ip": lic.get("ip", ""),
                    "active": lic.get("active", False),
                    "is_active": lic.get("active", False) and now < expiry,
                    "license_type": lic.get("license_type", "standard"),
                    "expiry": expiry.strftime("%Y-%m-%d")
                })
        
        # Statystyki
        searches_today = 0
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            r = requests.head(f"{SUPABASE_URL}/rest/v1/search_logs", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"},
                             params={"timestamp": f"gte.{today}T00:00:00Z"})
            if r.status_code == 206:
                searches_today = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
        except Exception as e:
            logger.error(f"Błąd statystyk: {e}")
        
        # Logi
        logs = []
        try:
            if os.path.exists("/tmp/activity.log"):
                with open("/tmp/activity.log", "r") as f:
                    logs = f.readlines()[-50:]
                logs = [log.strip() for log in logs]
        except Exception as e:
            logger.error(f"Błąd logów: {e}")
        
        # Dane dla panelu
        stats = {
            "db_count": db_count,
            "active_keys": sum(1 for lic in licenses if lic.get("is_active", False)),
            "searches_today": searches_today,
            "banned_ips": 3,  # Dla uproszczenia
            "licenses": licenses,
            "logs": logs,
            "new_key": session.pop("new_key", None)
        }
        
        return render_template_string(
            ADMIN_HTML,
            authenticated=True,
            admin_name=session.get("admin_name", "Administrator"),
            **stats
        )
    except Exception as e:
        logger.error(f"Błąd panelu: {e}")
        return "Błąd serwera", 500

@app.route("/admin/generate", methods=["POST"])
@admin_required
def admin_generate():
    try:
        days = int(request.form.get("days", 30))
        license_type = request.form.get("license_type", "premium")
        
        if days < 1 or days > 365:
            return redirect("/admin")
            
        new_key = lic_mgr.generate(days, license_type)
        if new_key:
            session["new_key"] = new_key
    except Exception as e:
        logger.error(f"Błąd generowania: {e}")
    return redirect("/admin")

@app.route("/admin/import_zip", methods=["POST"])
@admin_required
def admin_import_zip():
    try:
        zip_url = request.form.get("zip_url", "").strip()
        if zip_url and zip_url.startswith(("http://", "https://")) and zip_url.endswith(".zip"):
            def callback(result):
                pass  # W praktyce można zapisać wynik w sesji
            threading.Thread(target=import_leaks_worker, args=(zip_url, callback), daemon=True).start()
    except Exception as e:
        logger.error(f"Błąd importu: {e}")
    return redirect("/admin")

@app.route("/admin/toggle_license", methods=["POST"])
@admin_required
def admin_toggle_license():
    try:
        key = request.form.get("key")
        if key:
            r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
            if r.status_code == 200 and r.json():
                current = r.json()[0]["active"]
                requests.patch(f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}", headers=SUPABASE_HEADERS, json={"active": not current})
    except Exception as e:
        logger.error(f"Błąd przełączania: {e}")
    return redirect("/admin")

@app.route("/admin/delete_license", methods=["POST"])
@admin_required
def admin_delete_license():
    try:
        key = request.form.get("key")
        if key:
            requests.delete(f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}", headers=SUPABASE_HEADERS)
    except Exception as e:
        logger.error(f"Błąd usuwania: {e}")
    return redirect("/admin")

@app.route("/admin/clear_logs", methods=["POST"])
@admin_required
def admin_clear_logs():
    try:
        if os.path.exists("/tmp/activity.log"):
            open("/tmp/activity.log", "w").close()
    except Exception as e:
        logger.error(f"Błąd czyszczenia logów: {e}")
    return redirect("/admin")

# === GŁÓWNY KOD ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
