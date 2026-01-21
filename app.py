import os
import uuid
import requests
import zipfile
import tempfile
import threading
import logging
import time
import contextlib
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template_string, redirect, session, url_for, flash
import mysql.connector
from mysql.connector import pooling
import re

# === KONFIGURACJA ŚRODOWISKOWA ===
DB_CONFIG = {
    "host": os.getenv("LEAKS_DB_HOST", "136.243.54.157"),
    "port": int(os.getenv("LEAKS_DB_PORT", "25618")),
    "user": os.getenv("LEAKS_DB_USER", "admin_cold"),
    "password": os.getenv("LEAKS_DB_PASS", "Wyciek12"),
    "database": os.getenv("LEAKS_DB_NAME", "cold_search_db"),
    "charset": "utf8mb4",
    "autocommit": True,
    "connection_timeout": 30,
    "pool_size": 30,
    "pool_reset_session": True
}
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wcshypmsurncfufbojvp.supabase.co").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_secret_Ci0yyib3FCJW3GMivhX3XA_D2vHmhpP").strip()
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "wyciek12")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_ultra_2026_fixed")

# === POOL POŁĄCZEŃ MARIADB ===
db_pool = None

def initialize_db_pool():
    global db_pool
    max_attempts = 5
    attempt = 0
    while attempt < max_attempts:
        try:
            if db_pool is None:
                logger.info(f"🚀 Próba połączenia z MariaDB (próba {attempt + 1}/{max_attempts})")
                db_pool = mysql.connector.pooling.MySQLConnectionPool(**DB_CONFIG)
                logger.info("✅ Pula połączeń z MariaDB została pomyślnie utworzona")
                ensure_leaks_table_exists()
                return True
        except Exception as e:
            logger.error(f"❌ Błąd połączenia z MariaDB (próba {attempt + 1}): {e}")
            attempt += 1
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    logger.error("❌ Krytyczny błąd: nie udało się połączyć z MariaDB po wielu próbach")
    raise SystemExit("Nie można kontynuować bez połączenia z bazą danych leaków")

def ensure_leaks_table_exists():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES LIKE 'leaks'")
            if cursor.fetchone() is None:
                logger.info("🔧 Tworzenie tabeli 'leaks' z unikalnym kluczem...")
                create_table_query = """
                CREATE TABLE leaks (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    data VARCHAR(1000) NOT NULL,
                    source VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_data_source (data(255), source),
                    FULLTEXT INDEX ft_data (data),
                    INDEX idx_source (source),
                    INDEX idx_created_at (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
                cursor.execute(create_table_query)
                cursor.execute("""
                INSERT INTO leaks (data, source) VALUES
                ('test@example.com', 'test_data'),
                ('admin123', 'test_data'),
                ('user_2024', 'test_data')
                ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
                """)
                logger.info("✅ Tabela 'leaks' gotowa")
            else:
                cursor.execute("SHOW CREATE TABLE leaks")
                create_stmt = cursor.fetchone()[1]
                if "unique_data_source" not in create_stmt:
                    logger.warning("🔧 Dodawanie unikalnego klucza...")
                    cursor.execute("ALTER TABLE leaks ADD UNIQUE KEY unique_data_source (data(255), source)")
    except Exception as e:
        logger.error(f"❌ Błąd struktury tabeli: {e}")
        raise

def get_db_connection():
    global db_pool
    if db_pool is None:
        initialize_db_pool()
    try:
        conn = db_pool.get_connection()
        return conn
    except mysql.connector.Error as e:
        logger.error(f"❌ Błąd pobierania połączenia: {e}")
        if "pool exhausted" in str(e):
            time.sleep(1)
            try:
                conn = db_pool.get_connection()
                return conn
            except:
                pass
        logger.warning("🔄 Reset puli połączeń...")
        initialize_db_pool()
        return get_db_connection()

@contextlib.contextmanager
def get_db():
    conn = None
    try:
        conn = get_db_connection()
        yield conn
    finally:
        if conn and conn.is_connected():
            conn.close()

# === FUNKCJE POMOCNICZE ===
def sb_query(table, params=""):
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=SUPABASE_HEADERS, timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.error(f"❌ Błąd zapytania do Supabase ({table}): {e}")
        return []

def sb_insert(table, data):
    try:
        return requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SUPABASE_HEADERS, json=data, timeout=10)
    except Exception as e:
        logger.error(f"❌ Błąd wstawiania do Supabase ({table}): {e}")
        return None

def sb_update(table, data, condition):
    try:
        return requests.patch(f"{SUPABASE_URL}/rest/v1/{table}?{condition}", headers=SUPABASE_HEADERS, json=data, timeout=10)
    except Exception as e:
        logger.error(f"❌ Błąd aktualizacji w Supabase ({table}): {e}")
        return None

def sb_delete(table, condition):
    try:
        return requests.delete(f"{SUPABASE_URL}/rest/v1/{table}?{condition}", headers=SUPABASE_HEADERS, timeout=10)
    except Exception as e:
        logger.error(f"❌ Błąd usuwania z Supabase ({table}): {e}")
        return None

def get_client_ip():
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'

def is_valid_ip(ip):
    pattern = re.compile(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$')
    return pattern.match(ip) is not None

def format_datetime(dt):
    if isinstance(dt, datetime):
        return dt.strftime("%d.%m.%Y %H:%M")
    return str(dt)

def get_license_usage(key):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        today_logs = sb_query("search_logs", f"key=eq.{key}&timestamp=gte.{today}T00:00:00.000Z&select=count(*)")
        today_count = today_logs[0]["count"] if today_logs and today_logs[0] else 0
        total_logs = sb_query("search_logs", f"key=eq.{key}&select=count(*)")
        total_count = total_logs[0]["count"] if total_logs and total_logs[0] else 0
        return today_count, total_count
    except Exception as e:
        logger.error(f"❌ Błąd pobierania statystyk użycia licencji: {e}")
        return 0, 0

# === JEDYNY ENDPOINT: / (panel admina) ===
@app.route("/", methods=["GET", "POST"])
def admin_panel():
    if request.method == "POST":
        if not session.get('is_admin'):
            if request.form.get("password") == ADMIN_PASSWORD:
                session['is_admin'] = True
                session['login_time'] = datetime.now(timezone.utc).isoformat()
                flash('✅ Zalogowano pomyślnie!', 'success')
            else:
                flash('❌ Nieprawidłowe hasło!', 'error')
                return render_template_string(ADMIN_LOGIN_TEMPLATE)
        else:
            action = request.form.get("action")
            if action == "add_license":
                days = int(request.form.get("days", 30))
                daily_limit = int(request.form.get("daily_limit", 100))
                total_limit = int(request.form.get("total_limit", 1000))
                new_key = "COLD-" + uuid.uuid4().hex.upper()[:12]
                expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
                payload = {
                    "key": new_key,
                    "active": True,
                    "expiry": expiry,
                    "daily_limit": daily_limit,
                    "total_limit": total_limit,
                    "ip": get_client_ip(),
                    "created_at": "now()"
                }
                r = sb_insert("licenses", payload)
                if r and r.status_code in (200, 201):
                    flash(f"✅ Licencja: {new_key} (limit dzienny: {daily_limit}, całkowity: {total_limit})", 'success')
                else:
                    flash("❌ Błąd generowania licencji", 'error')

            elif action == "toggle_license":
                key = request.form.get("key")
                licenses = sb_query("licenses", f"key=eq.{key}")
                if licenses:
                    new_status = not licenses[0].get('active', False)
                    sb_update("licenses", {"active": new_status}, f"key=eq.{key}")
                    flash(f"{'Włączono' if new_status else 'Wyłączono'} licencję", 'success')

            elif action == "del_license":
                key = request.form.get("key")
                sb_delete("licenses", f"key=eq.{key}")
                flash("✅ Licencja usunięta", 'success')

            elif action == "add_ban":
                ip = request.form.get("ip", "").strip()
                if is_valid_ip(ip):
                    if not sb_query("banned_ips", f"ip=eq.{ip}"):
                        sb_insert("banned_ips", {"ip": ip, "reason": request.form.get("reason", "—"), "admin_ip": get_client_ip()})
                        flash(f"✅ Zbanowano IP: {ip}", 'success')
                    else:
                        flash("❌ IP już zbanowane", 'error')
                else:
                    flash("❌ Nieprawidłowe IP", 'error')

            elif action == "del_ban":
                ip = request.form.get("ip")
                sb_delete("banned_ips", f"ip=eq.{ip}")
                flash("✅ Odbanowano IP", 'success')

            elif action == "import_start":
                url = request.form.get("import_url")
                if url and url.startswith(('http://', 'https://')):
                    threading.Thread(target=import_worker, args=(url,), daemon=True).start()
                    flash("✅ Import uruchomiony w tle", 'info')
                else:
                    flash("❌ Nieprawidłowy URL", 'error')

    if not session.get('is_admin'):
        return render_template_string(ADMIN_LOGIN_TEMPLATE)

    # Ładowanie danych
    try:
        with get_db() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT COUNT(*) as total FROM leaks")
            total_leaks = cursor.fetchone()['total']
            cursor.execute("SELECT COUNT(DISTINCT source) as sources FROM leaks")
            source_count = cursor.fetchone()['sources']
            cursor.execute("SELECT data, source, created_at FROM leaks ORDER BY created_at DESC LIMIT 10")
            recent_leaks = cursor.fetchall()
            cursor.execute("SELECT source, COUNT(*) as count FROM leaks GROUP BY source ORDER BY count DESC LIMIT 5")
            top_sources = cursor.fetchall()

        licenses = sb_query("licenses", "order=created_at.desc")
        for lic in licenses:
            today_count, total_count = get_license_usage(lic["key"])
            lic["today_count"] = today_count
            lic["total_count"] = total_count
            
        banned_ips = sb_query("banned_ips", "order=created_at.desc")
        active_licenses = sum(1 for lic in licenses if lic.get('active'))
        total_searches = 0
        try:
            logs = sb_query("search_logs", "select=count(*)")
            total_searches = logs[0]['count'] if logs else 0
        except:
            pass

        login_time = datetime.fromisoformat(session['login_time'])
        session_duration = str(datetime.now(timezone.utc) - login_time).split('.')[0]

        return render_template_string(
            ADMIN_TEMPLATE,
            total_leaks=total_leaks,
            source_count=source_count,
            recent_leaks=recent_leaks,
            top_sources=top_sources,
            licenses=licenses,
            banned_ips=banned_ips,
            active_licenses=active_licenses,
            total_searches=total_searches,
            session_duration=session_duration,
            client_ip=get_client_ip(),
            format_datetime=format_datetime
        )
    except Exception as e:
        logger.error(f"💥 Błąd ładowania panelu: {e}")
        flash("❌ Błąd serwera", 'error')
        return redirect("/")

@app.route("/logout")
def admin_logout():
    session.clear()
    flash("✅ Wylogowano", 'success')
    return redirect("/")

# === IMPORT WORKER ===
def import_worker(url):
    try:
        logger.info(f"📥 Rozpoczęto import z: {url}")
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
            for chunk in response.iter_content(chunk_size=8192):
                tmp_file.write(chunk)
            tmp_path = tmp_file.name

        total_added = 0
        with tempfile.TemporaryDirectory() as tmp_dir:
            with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                zip_ref.extractall(tmp_dir)
            with get_db() as conn:
                cursor = conn.cursor()
                for root, _, files in os.walk(tmp_dir):
                    for filename in files:
                        if filename.endswith(('.txt', '.csv', '.log')):
                            file_path = os.path.join(root, filename)
                            source_name = os.path.relpath(file_path, tmp_dir)
                            try:
                                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    batch = []
                                    for line in f:
                                        clean_line = line.strip()
                                        if 5 < len(clean_line) <= 1000:
                                            batch.append((clean_line, source_name))
                                            if len(batch) >= 1000:
                                                cursor.executemany(
                                                    "INSERT INTO leaks (data, source) VALUES (%s, %s) "
                                                    "ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP",
                                                    batch
                                                )
                                                total_added += cursor.rowcount
                                                batch = []
                                    if batch:
                                        cursor.executemany(
                                            "INSERT INTO leaks (data, source) VALUES (%s, %s) "
                                            "ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP",
                                            batch
                                        )
                                        total_added += cursor.rowcount
                            except Exception as e:
                                logger.error(f"❌ Błąd pliku {source_name}: {e}")
                conn.commit()
        os.unlink(tmp_path)
        logger.info(f"✅ Import zakończony. Nowe rekordy: {total_added}")
    except Exception as e:
        logger.error(f"💥 Fatalny błąd importu: {e}")

# === SZABLONY HTML ===
ADMIN_LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cold Search Premium — Panel Admina</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
:root {
--primary: #00f2ff;
--secondary: #bc13fe;
--bg: #0a0a12;
--card-bg: rgba(15, 15, 25, 0.8);
--border: rgba(255, 255, 255, 0.1);
--text: #eaeaff;
--error: #ff4d4d;
--success: #00ffaa;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
background: var(--bg);
color: var(--text);
font-family: 'Inter', sans-serif;
min-height: 100vh;
display: flex;
align-items: center;
justify-content: center;
padding: 20px;
background-image:
radial-gradient(circle at 10% 20%, rgba(0, 242, 255, 0.1) 0%, transparent 20%),
radial-gradient(circle at 90% 80%, rgba(188, 19, 254, 0.1) 0%, transparent 20%);
}
.login-container { max-width: 450px; width: 100%; }
.logo { text-align: center; margin-bottom: 30px; }
.logo-text {
font-size: 2.2rem;
font-weight: 800;
background: linear-gradient(90deg, var(--primary), var(--secondary));
-webkit-background-clip: text;
-webkit-text-fill-color: transparent;
background-clip: text;
}
.card {
background: var(--card-bg);
border-radius: 20px;
padding: 40px;
box-shadow: 0 15px 35px rgba(0, 0, 0, 0.5);
border: 1px solid var(--border);
backdrop-filter: blur(10px);
}
.card-title { font-size: 1.75rem; font-weight: 700; margin-bottom: 25px; text-align: center; color: var(--text); }
.form-group { margin-bottom: 20px; }
.form-label { display: block; margin-bottom: 8px; font-weight: 500; color: var(--text); }
.form-input {
width: 100%;
padding: 14px;
background: rgba(0, 0, 0, 0.3);
border: 1px solid var(--border);
border-radius: 12px;
color: white;
font-family: 'Inter', sans-serif;
font-size: 1rem;
transition: border-color 0.3s;
}
.form-input:focus {
outline: none;
border-color: var(--primary);
box-shadow: 0 0 0 2px rgba(0, 242, 255, 0.2);
}
.btn {
width: 100%;
padding: 15px;
background: linear-gradient(135deg, var(--primary), #00b3cc);
color: #000;
border: none;
border-radius: 12px;
font-family: 'Inter', sans-serif;
font-weight: 700;
font-size: 1.05rem;
cursor: pointer;
transition: all 0.2s ease;
margin-top: 10px;
}
.btn:hover {
transform: translateY(-2px);
box-shadow: 0 5px 15px rgba(0, 242, 255, 0.4);
}
.alert {
padding: 12px;
margin: 15px 0;
border-radius: 8px;
font-weight: 500;
display: flex;
align-items: center;
gap: 10px;
}
.alert-error { background: rgba(255,77,77,0.15); border: 1px solid var(--error); color: var(--error); }
.alert-success { background: rgba(0,255,170,0.15); border: 1px solid var(--success); color: var(--success); }
</style>
</head>
<body>
<div class="login-container">
<div class="logo">
<div class="logo-text">❄️ Cold Search Premium</div>
</div>
<div class="card">
<h1 class="card-title">🔐 Panel Administratora</h1>
<form method="post">
<div class="form-group">
<label for="password" class="form-label">Hasło administratora</label>
<input type="password" id="password" name="password" class="form-input" placeholder="••••••••••••••••" required autofocus>
</div>
<button type="submit" class="btn">Zaloguj się</button>
{% with messages = get_flashed_messages(with_categories=true) %}
{% for cat, msg in messages %}
<div class="alert alert-{{ 'success' if cat == 'success' else 'error' }}">{{ msg }}</div>
{% endfor %}
{% endwith %}
</form>
</div>
</div>
</body>
</html>
'''

ADMIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cold Search Premium — Panel Admina</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
:root {
--primary: #00f2ff;
--secondary: #bc13fe;
--bg: #0a0a12;
--card-bg: rgba(15, 15, 25, 0.8);
--border: rgba(255, 255, 255, 0.1);
--text: #eaeaff;
--text-secondary: #8888aa;
--success: #00ffaa;
--danger: #ff4d4d;
--warning: #ffcc00;
--limit-low: rgba(255, 204, 0, 0.15);
--limit-medium: rgba(255, 102, 0, 0.15);
--limit-high: rgba(255, 0, 0, 0.15);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
background: var(--bg);
color: var(--text);
font-family: 'Inter', sans-serif;
padding: 20px;
}
.container { max-width: 1400px; margin: 0 auto; }
.header {
display: flex;
justify-content: space-between;
align-items: center;
margin-bottom: 30px;
padding-bottom: 15px;
border-bottom: 1px solid var(--border);
}
.page-title { font-size: 1.8rem; font-weight: 700; }
.logout-btn {
background: rgba(255, 77, 77, 0.15);
color: var(--danger);
border: 1px solid var(--danger);
padding: 8px 16px;
border-radius: 8px;
text-decoration: none;
font-weight: 600;
display: inline-flex;
align-items: center;
gap: 8px;
}
.stats-grid {
display: grid;
grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
gap: 20px;
margin-bottom: 30px;
}
.stat-card {
background: var(--card-bg);
border-radius: 16px;
padding: 20px;
border: 1px solid var(--border);
text-align: center;
transition: transform 0.3s ease;
}
.stat-card:hover {
transform: translateY(-5px);
box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
}
.stat-value { 
font-size: 1.8rem; 
font-weight: 800; 
color: white; 
font-family: 'Courier New', monospace; 
margin: 10px 0; 
}
.stat-label { font-size: 0.9rem; color: var(--text-secondary); }
.section {
background: var(--card-bg);
border-radius: 16px;
padding: 25px;
margin-bottom: 25px;
border: 1px solid var(--border);
}
.section-title {
display: flex;
align-items: center;
gap: 10px;
margin-bottom: 20px;
font-size: 1.3rem;
font-weight: 600;
color: white;
}
.section-title i { color: var(--primary); }
.form-row { 
display: flex; 
gap: 15px; 
margin-bottom: 15px; 
flex-wrap: wrap; 
align-items: flex-end;
}
.form-group { 
flex: 1; 
min-width: 180px; 
}
label { 
display: block; 
margin-bottom: 6px; 
font-size: 0.95rem;
color: var(--text);
}
input, select, textarea {
width: 100%;
padding: 12px;
background: rgba(0,0,0,0.3);
border: 1px solid var(--border);
border-radius: 8px;
color: white;
font-family: 'Inter', sans-serif;
font-size: 0.95rem;
transition: border-color 0.3s;
}
input:focus, select:focus {
outline: none;
border-color: var(--primary);
box-shadow: 0 0 0 2px rgba(0, 242, 255, 0.2);
}
.btn {
padding: 12px 24px;
background: linear-gradient(90deg, var(--primary), #00b3cc);
color: #000;
border: none;
border-radius: 8px;
font-weight: 600;
cursor: pointer;
font-size: 0.95rem;
transition: all 0.2s;
display: inline-flex;
align-items: center;
gap: 8px;
}
.btn:hover {
transform: translateY(-2px);
box-shadow: 0 5px 15px rgba(0, 242, 255, 0.4);
}
.btn-danger {
background: linear-gradient(90deg, rgba(255,77,77,0.2), rgba(255,0,0,0.2));
color: var(--danger);
}
.btn-danger:hover {
background: linear-gradient(90deg, rgba(255,77,77,0.3), rgba(255,0,0,0.3));
}
.table { 
width: 100%; 
border-collapse: collapse; 
margin-top: 15px; 
}
.table th { 
text-align: left; 
padding: 14px 12px; 
border-bottom: 2px solid var(--border); 
font-weight: 700;
background: rgba(0,0,0,0.2);
}
.table td { 
padding: 14px 12px; 
border-bottom: 1px solid var(--border);
font-size: 0.95rem;
}
.table tr:last-child td { border-bottom: none; }
.key { 
font-family: 'Courier New', monospace; 
color: var(--primary); 
font-weight: 600; 
letter-spacing: 0.5px;
}
.status-active { color: var(--success); font-weight: 600; }
.status-inactive { color: var(--danger); font-weight: 600; }
.alert {
padding: 12px;
margin-bottom: 20px;
border-radius: 8px;
font-weight: 500;
display: flex;
align-items: center;
gap: 10px;
}
.alert-info { 
background: rgba(0,242,255,0.1); 
border: 1px solid rgba(0,242,255,0.3); 
color: var(--primary); 
}
.alert-error { 
background: rgba(255,77,77,0.1); 
border: 1px solid var(--danger); 
color: var(--danger); 
}
.alert-success { 
background: rgba(0,255,170,0.1); 
border: 1px solid var(--success); 
color: var(--success); 
}
.leak-item { 
padding: 10px 0; 
border-bottom: 1px dashed var(--border); 
}
.leak-item:last-child { border-bottom: none; }
.leak-data {
font-family: 'Courier New', monospace;
font-size: 0.95rem;
word-break: break-all;
}
.ip-badge {
display: inline-block;
background: rgba(188, 19, 254, 0.2);
border: 1px solid rgba(188, 19, 254, 0.4);
padding: 2px 8px;
border-radius: 4px;
font-size: 0.85rem;
font-family: monospace;
}
.limit-badge {
display: inline-block;
padding: 3px 10px;
border-radius: 20px;
font-size: 0.85rem;
font-weight: 600;
}
.limit-daily { background: var(--limit-low); color: #ffcc00; }
.limit-total { background: var(--limit-medium); color: #ff9900; }
.usage-bar {
height: 8px;
background: rgba(100, 100, 100, 0.3);
border-radius: 4px;
margin-top: 4px;
overflow: hidden;
}
.usage-fill {
height: 100%;
border-radius: 4px;
}
.usage-low { background: var(--success); }
.usage-medium { background: #ffcc00; }
.usage-high { background: var(--danger); }
@media (max-width: 768px) {
.form-row { flex-direction: column; }
.stats-grid { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1 class="page-title">❄️ Cold Search Premium — Panel Admina</h1>
<a href="/logout" class="logout-btn"><i class="fas fa-sign-out-alt"></i> Wyloguj</a>
</div>

{% with messages = get_flashed_messages(with_categories=true) %}
{% for cat, msg in messages %}
<div class="alert alert-{{ 'success' if cat == 'success' else 'error' if cat == 'error' else 'info' }}">
{% if cat == 'success' %}
<i class="fas fa-check-circle"></i>
{% elif cat == 'error' %}
<i class="fas fa-exclamation-circle"></i>
{% else %}
<i class="fas fa-info-circle"></i>
{% endif %}
{{ msg }}
</div>
{% endfor %}
{% endwith %}

<!-- Statystyki -->
<div class="stats-grid">
<div class="stat-card"><div class="stat-value">{{ "{:,}".format(total_leaks).replace(",", " ") }}</div><div class="stat-label">Rekordów w bazie</div></div>
<div class="stat-card"><div class="stat-value">{{ "{:,}".format(source_count).replace(",", " ") }}</div><div class="stat-label">Źródeł danych</div></div>
<div class="stat-card"><div class="stat-value">{{ active_licenses }}</div><div class="stat-label">Aktywnych licencji</div></div>
<div class="stat-card"><div class="stat-value">{{ "{:,}".format(total_searches).replace(",", " ") }}</div><div class="stat-label">Wyszukań łącznie</div></div>
</div>

<!-- Ostatnie leaki -->
<div class="section">
<div class="section-title"><i class="fas fa-history"></i> Ostatnie dane</div>
{% for leak in recent_leaks %}
<div class="leak-item">
<div class="leak-data">{{ leak.data | truncate(70) }}</div>
<small>{{ leak.source }} • {{ format_datetime(leak.created_at) }}</small>
</div>
{% endfor %}
</div>

<!-- Licencje -->
<div class="section">
<div class="section-title"><i class="fas fa-key"></i> Licencje ({{ licenses|length }})</div>
<form method="post" style="margin-bottom:20px;">
<input type="hidden" name="action" value="add_license">
<div class="form-row">
<div class="form-group">
<label>Liczba dni ważności</label>
<input type="number" name="days" value="30" min="1" max="3650">
</div>
<div class="form-group">
<label>Limit wyszukiwań dziennych</label>
<input type="number" name="daily_limit" value="100" min="1" max="10000">
</div>
<div class="form-group">
<label>Limit wyszukiwań całkowitych</label>
<input type="number" name="total_limit" value="1000" min="10" max="100000">
</div>
<div class="form-group" style="align-self: flex-end;">
<button type="submit" class="btn"><i class="fas fa-plus"></i> Generuj licencję</button>
</div>
</div>
</form>

<table class="table">
<thead>
<tr>
<th>Klucz</th>
<th>IP</th>
<th>Limity</th>
<th>Ważna do</th>
<th>Użycie</th>
<th>Status</th>
<th>Akcje</th>
</tr>
</thead>
<tbody>
{% for lic in licenses %}
<tr>
<td><span class="key">{{ lic.key }}</span></td>
<td>
{% if lic.ip %}
<span class="ip-badge">{{ lic.ip }}</span>
{% else %}
<span class="ip-badge" style="background: rgba(255,100,100,0.2); border-color: rgba(255,100,100,0.4); color: #ff6666;">Brak IP</span>
{% endif %}
</td>
<td>
<div><span class="limit-badge limit-daily">Dzienny: {{ lic.daily_limit }}</span></div>
<div style="margin-top: 4px;"><span class="limit-badge limit-total">Całkowity: {{ lic.total_limit }}</span></div>
</td>
<td>{{ lic.expiry.split('T')[0] }}</td>
<td>
<div style="font-size: 0.85rem; color: var(--text-secondary);">
Dzienny: {{ lic.today_count }}/{{ lic.daily_limit }}
</div>
<div class="usage-bar">
<div class="usage-fill {% if lic.today_count / lic.daily_limit < 0.7 %}usage-low{% elif lic.today_count / lic.daily_limit < 0.9 %}usage-medium{% else %}usage-high{% endif %}" 
style="width: {{ (lic.today_count / lic.daily_limit * 100) | min(100) }}%"></div>
</div>
<div style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 6px;">
Całkowity: {{ lic.total_count }}/{{ lic.total_limit }}
</div>
<div class="usage-bar">
<div class="usage-fill {% if lic.total_count / lic.total_limit < 0.7 %}usage-low{% elif lic.total_count / lic.total_limit < 0.9 %}usage-medium{% else %}usage-high{% endif %}" 
style="width: {{ (lic.total_count / lic.total_limit * 100) | min(100) }}%"></div>
</div>
</td>
<td>
<span class="{{ 'status-active' if lic.active else 'status-inactive' }}">
{{ 'Aktywna' if lic.active else 'Nieaktywna' }}
</span>
</td>
<td>
<form method="post" style="display:inline;" onsubmit="return confirm('Na pewno?')">
<input type="hidden" name="action" value="toggle_license">
<input type="hidden" name="key" value="{{ lic.key }}">
<button type="submit" class="btn {% if lic.active %}btn-warning{% else %}btn{% endif %}">
{{ 'Wyłącz' if lic.active else 'Włącz' }}
</button>
</form>
<form method="post" style="display:inline; margin-left: 5px;" onsubmit="return confirm('USUNĄĆ LICENCJĘ?')">
<input type="hidden" name="action" value="del_license">
<input type="hidden" name="key" value="{{ lic.key }}">
<button type="submit" class="btn btn-danger"><i class="fas fa-trash"></i></button>
</form>
</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>

<!-- Bany IP -->
<div class="section">
<div class="section-title"><i class="fas fa-ban"></i> Zbanowane IP ({{ banned_ips|length }})</div>
<form method="post" style="margin-bottom:20px;">
<input type="hidden" name="action" value="add_ban">
<div class="form-row">
<div class="form-group">
<label>Adres IP</label>
<input type="text" name="ip" placeholder="np. 192.168.1.1" required>
</div>
<div class="form-group">
<label>Powód</label>
<input type="text" name="reason" placeholder="Opcjonalnie">
</div>
<div class="form-group" style="align-self: flex-end;">
<button type="submit" class="btn"><i class="fas fa-ban"></i> Zbanuj IP</button>
</div>
</div>
</form>

<table class="table">
<thead><tr><th>IP</th><th>Powód</th><th>Data</th><th>Akcje</th></tr></thead>
<tbody>
{% for ban in banned_ips %}
<tr>
<td><span class="ip-badge">{{ ban.ip }}</span></td>
<td>{{ ban.reason or '—' }}</td>
<td>{{ format_datetime(ban.created_at) if ban.created_at else '—' }}</td>
<td>
<form method="post" style="display:inline;" onsubmit="return confirm('Odbanować?')">
<input type="hidden" name="action" value="del_ban">
<input type="hidden" name="ip" value="{{ ban.ip }}">
<button type="submit" class="btn btn-danger"><i class="fas fa-unlock"></i></button>
</form>
</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>

<!-- Import danych -->
<div class="section">
<div class="section-title"><i class="fas fa-file-import"></i> Import bazy leaków</div>
<p style="margin-bottom:15px; color:var(--text-secondary);">
Wklej URL do pliku ZIP zawierającego pliki tekstowe (.txt, .csv, .log). System automatycznie zaimportuje unikalne linie.
</p>
<form method="post">
<input type="hidden" name="action" value="import_start">
<div class="form-row">
<div class="form-group">
<label>URL do archiwum ZIP</label>
<input type="url" name="import_url" placeholder="https://example.com/data.zip" required>
</div>
<div class="form-group" style="align-self: flex-end;">
<button type="submit" class="btn"><i class="fas fa-cloud-download-alt"></i> Rozpocznij import</button>
</div>
</div>
</form>
<div style="margin-top:15px; padding:12px; background:rgba(0,0,0,0.2); border-radius:8px; font-size:0.9rem;">
<i class="fas fa-info-circle" style="margin-right: 8px; color: var(--primary);"></i>
⚠️ Import działa w tle. Sprawdzaj logi serwera lub odśwież statystyki po chwili.
</div>
</div>

<!-- Informacje systemowe -->
<div class="section">
<div class="section-title"><i class="fas fa-server"></i> Informacje systemowe</div>
<p><strong>Twój IP:</strong> <span class="ip-badge">{{ client_ip }}</span></p>
<p><strong>Czas sesji:</strong> {{ session_duration }}</p>
<p><strong>Baza danych:</strong> Online ({{ total_leaks }} rekordów)</p>
<p><strong>Host bazy:</strong> <span class="ip-badge">136.243.54.157:25618</span></p>
</div>

</div>
</body>
</html>
'''

# === API ENDPOINTS ===
@app.route("/api/status", methods=["GET"])
def api_status():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            db_status = cursor.fetchone()[0] == 1
    except:
        db_status = False
    return jsonify({
        "success": True,
        "status": "online",
        "version": "2.1.0",
        "server_time": datetime.now(timezone.utc).isoformat(),
        "database_status": db_status
    })

@app.route("/api/auth", methods=["POST"])
def api_auth():
    data = request.json or request.form.to_dict()
    key = data.get("key")
    ip = data.get("client_ip") or get_client_ip()
    if not key:
        return jsonify({"success": False, "message": "Brak klucza"}), 400
    licenses = sb_query("licenses", f"key=eq.{key}")
    if not licenses:
        return jsonify({"success": False, "message": "Nieprawidłowy klucz licencyjny"}), 401
    lic = licenses[0]
    expiry = datetime.fromisoformat(lic['expiry'].replace('Z', '+00:00'))
    if datetime.now(timezone.utc) > expiry or not lic.get('active', True):
        return jsonify({"success": False, "message": "Licencja wygasła lub została zablokowana"}), 401
    if not lic.get("ip"):
        sb_update("licenses", {"ip": ip}, f"key=eq.{key}")
    if lic.get("ip") and lic["ip"] != ip:
        return jsonify({"success": False, "message": "Klucz przypisany do innego adresu IP"}), 403
    
    today_count, total_count = get_license_usage(key)
    if today_count >= lic.get("daily_limit", 100):
        return jsonify({"success": False, "message": "Przekroczono dzienny limit wyszukiwań"}), 429
    if total_count >= lic.get("total_limit", 1000):
        return jsonify({"success": False, "message": "Przekroczono całkowity limit wyszukiwań"}), 429
        
    return jsonify({"success": True, "message": "Zalogowano pomyślnie"})

@app.route("/api/license-info", methods=["POST"])
def api_info():
    data = request.json or request.form.to_dict()
    key = data.get("key")
    ip = data.get("client_ip") or get_client_ip()
    if not key:
        return jsonify({"success": False, "message": "Brak klucza"}), 400
    auth_response = api_auth()
    if auth_response.status_code != 200:
        return auth_response
    licenses = sb_query("licenses", f"key=eq.{key}")
    if not licenses:
        return jsonify({"success": False, "message": "Nie znaleziono licencji"}), 404
    lic = licenses[0]
    today_count, total_count = get_license_usage(key)
    return jsonify({
        "success": True,
        "info": {
            "license_type": "Premium",
            "expiration_date": lic["expiry"].split("T")[0],
            "daily_limit": lic.get("daily_limit", 100),
            "total_limit": lic.get("total_limit", 1000),
            "daily_used": today_count,
            "total_used": total_count,
            "ip_bound": lic.get("ip", "Nie przypisano"),
            "last_search": "Brak danych"
        }
    })

@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.json or request.form.to_dict()
    query = data.get("query", "").strip()
    key = data.get("key")
    ip = data.get("client_ip") or get_client_ip()
    limit = int(data.get("limit", 150))
    if not key:
        return jsonify({"success": False, "message": "Brak klucza"}), 400
    if not query:
        return jsonify({"success": False, "message": "Puste zapytanie"}), 400
    auth_response = api_auth()
    if auth_response.status_code != 200:
        return auth_response
    try:
        sb_insert("search_logs", {
            "key": key,
            "query": query,
            "ip": ip,
            "timestamp": "now()"
        })
        with get_db() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT data, source
                FROM leaks
                WHERE MATCH(data) AGAINST (%s IN BOOLEAN MODE)
                LIMIT %s
            """, (f"*{query}*", limit))
            results = cursor.fetchall()
        return jsonify({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Błąd wyszukiwania: {e}")
        return jsonify({"success": False, "message": f"Błąd bazy danych: {str(e)}"}), 500

# === URUCHOMIENIE ===
if __name__ == "__main__":
    initialize_db_pool()
    logger.info("🚀 Cold Search Premium — Panel admina gotowy")
    port = int(os.environ.get('PORT', 10000))  # Render wymaga PORT=10000
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
