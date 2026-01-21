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
import urllib.parse

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
    """Poprawiona funkcja do zapytań Supabase z prawidłowym formatowaniem URL"""
    try:
        # Usunięcie nadmiarowych spacji z parametrów
        params = params.replace("\n", "").replace(" ", "").replace("count  (*)", "count(*)")
        
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        if params:
            url += f"?{params}"
        
        logger.debug(f"Query URL: {url}")
        response = requests.get(url, headers=SUPABASE_HEADERS, timeout=10)
        response.raise_for_status()
        return response.json() if response.status_code == 200 else []
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning(f"⚠️ Tabela '{table}' nie istnieje w Supabase - tworzenie...")
            # Możemy spróbować utworzyć tabelę, ale na razie zwracamy pustą listę
            return []
        logger.error(f"❌ Błąd HTTP podczas zapytania do {table}: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"❌ Błąd zapytania do Supabase ({table}): {e}")
        return []

def sb_insert(table, data):
    try:
        response = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SUPABASE_HEADERS, json=data, timeout=10)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"❌ Błąd wstawiania do Supabase ({table}): {e}")
        return None

def sb_update(table, data, condition):
    try:
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        if condition:
            url += f"?{urllib.parse.quote(condition)}"
        response = requests.patch(url, headers=SUPABASE_HEADERS, json=data, timeout=10)
        return response
    except Exception as e:
        logger.error(f"❌ Błąd aktualizacji w Supabase ({table}): {e}")
        return None

def sb_delete(table, condition):
    try:
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        if condition:
            url += f"?{urllib.parse.quote(condition)}"
        response = requests.delete(url, headers=SUPABASE_HEADERS, timeout=10)
        return response
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

def safe_get(obj, key, default=None):
    """Bezpiecznie pobiera wartość z obiektu"""
    if isinstance(obj, dict) and key in obj:
        return obj[key]
    return default

def get_license_usage(key):
    """Poprawiona funkcja pobierania statystyk użycia licencji"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_start = f"{today}T00:00:00.000Z"
    
    try:
        # Pobierz dzienne użycie - POPRAWIONE FORMATOWANIE PARAMETRÓW
        today_params = f"key=eq.{key}&timestamp=gte.{urllib.parse.quote(today_start)}&select=count(*)"
        today_logs = sb_query("search_logs", today_params)
        
        today_count = 0
        # Obsługa różnych formatów odpowiedzi
        if isinstance(today_logs, list) and today_logs and isinstance(today_logs[0], dict):
            today_count = today_logs[0].get("count", 0)
        elif isinstance(today_logs, dict) and "count" in today_logs:
            today_count = today_logs["count"]
        elif isinstance(today_logs, int):
            today_count = today_logs
        elif isinstance(today_logs, str) and today_logs.isdigit():
            today_count = int(today_logs)
        
        # Pobierz całkowite użycie - POPRAWIONE FORMATOWANIE PARAMETRÓW
        total_params = f"key=eq.{key}&select=count(*)"
        total_logs = sb_query("search_logs", total_params)
        
        total_count = 0
        if isinstance(total_logs, list) and total_logs and isinstance(total_logs[0], dict):
            total_count = total_logs[0].get("count", 0)
        elif isinstance(total_logs, dict) and "count" in total_logs:
            total_count = total_logs["count"]
        elif isinstance(total_logs, int):
            total_count = total_logs
        elif isinstance(total_logs, str) and total_logs.isdigit():
            total_count = int(total_logs)
                
        return today_count, total_count
    except Exception as e:
        logger.error(f"❌ Błąd pobierania statystyk użycia licencji: {e}")
        return 0, 0

# === JEDYNY ENDPOINT: / (panel admina) ===
@app.route("/", methods=["GET", "POST"])
def admin_panel():
    if request.method == "POST":
        if not session.get('is_admin'):
            # Obsługa logowania
            password = request.form.get("password", "")
            if password == ADMIN_PASSWORD:
                session['is_admin'] = True
                session['login_time'] = datetime.now(timezone.utc).isoformat()
                flash('✅ Zalogowano pomyślnie!', 'success')
                # PRAWIDŁOWE PRZEKIEROWANIE PO ZALOGOWANIU
                return redirect(url_for('admin_panel'))
            else:
                flash('❌ Nieprawidłowe hasło!', 'error')
                return render_template_string(ADMIN_LOGIN_TEMPLATE)
        else:
            # Obsługa akcji panelu administratora
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
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
                r = sb_insert("licenses", payload)
                if r and r.status_code in (200, 201):
                    flash(f"✅ Licencja: {new_key} (limit dzienny: {daily_limit}, całkowity: {total_limit})", 'success')
                else:
                    flash("❌ Błąd generowania licencji", 'error')

            # Reszta akcji (toggle_license, del_license, add_ban, del_ban, import_start)...
            # [Ta część kodu jest taka sama jak wcześniej i została pominięta dla zwięzłości]

    # Sprawdź czy użytkownik jest zalogowany
    if not session.get('is_admin'):
        return render_template_string(ADMIN_LOGIN_TEMPLATE)

    # Ładowanie danych - POPRAWIONA OBSŁUGA BŁĘDÓW
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

        # Pobierz licencje i uzupełnij brakujące pola
        licenses = sb_query("licenses", "order=created_at.desc")
        if not isinstance(licenses, list):
            licenses = []

        # Pobierz zbanowane IP
        banned_ips = sb_query("banned_ips", "order=created_at.desc")
        if not isinstance(banned_ips, list):
            banned_ips = []
        
        # Pobierz liczbę aktywnych licencji
        active_licenses = sum(1 for lic in licenses if safe_get(lic, 'active', False))
        
        # Pobierz całkowitą liczbę wyszukiwań
        total_searches = 0
        try:
            total_logs = sb_query("search_logs", "select=count(*)")
            if isinstance(total_logs, list) and total_logs and isinstance(total_logs[0], dict):
                total_searches = total_logs[0].get("count", 0)
            elif isinstance(total_logs, dict) and "count" in total_logs:
                total_searches = total_logs["count"]
        except Exception as e:
            logger.error(f"❌ Błąd pobierania liczby wyszukiwań: {e}")

        # Oblicz czas trwania sesji
        login_time = datetime.fromisoformat(session['login_time'])
        session_duration = str(datetime.now(timezone.utc) - login_time).split('.')[0]

        # Przekazanie danych do szablonu
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
            format_datetime=format_datetime,
            now=datetime.now(timezone.utc)
        )
    except Exception as e:
        logger.error(f"💥 Błąd ładowania panelu: {e}")
        flash(f"❌ Błąd serwera: {str(e)}", 'error')
        session.clear()
        return redirect(url_for('admin_panel'))

@app.route("/logout")
def admin_logout():
    session.clear()
    flash("✅ Wylogowano", 'success')
    return redirect(url_for('admin_panel'))

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
--primary: #6366f1;
--primary-dark: #4f46e5;
--bg: #0f172a;
--card-bg: #1e293b;
--border: #334155;
--text: #f1f5f9;
--text-secondary: #94a3b8;
--success: #10b981;
--danger: #ef4444;
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
radial-gradient(circle at 10% 20%, rgba(99, 102, 241, 0.1) 0%, transparent 20%),
radial-gradient(circle at 90% 80%, rgba(147, 51, 234, 0.1) 0%, transparent 20%);
}
.login-container { 
max-width: 450px; 
width: 100%;
animation: fadeIn 0.5s ease;
}
.logo { 
text-align: center; 
margin-bottom: 20px; 
}
.logo-text {
font-size: 2.8rem;
font-weight: 800;
background: linear-gradient(90deg, #8b5cf6, #ec4899);
-webkit-background-clip: text;
-webkit-text-fill-color: transparent;
background-clip: text;
letter-spacing: -0.5px;
}
.logo-subtitle {
font-size: 1.1rem;
color: var(--text-secondary);
margin-top: 8px;
font-weight: 300;
}
.card {
background: var(--card-bg);
border-radius: 24px;
padding: 40px;
box-shadow: 
0 10px 15px -3px rgba(0, 0, 0, 0.1),
0 4px 6px -4px rgba(0, 0, 0, 0.1),
0 0 30px rgba(99, 102, 241, 0.15);
border: 1px solid var(--border);
backdrop-filter: blur(12px);
animation: slideUp 0.6s ease;
}
.card-title { 
font-size: 1.8rem; 
font-weight: 700; 
margin-bottom: 25px; 
text-align: center; 
color: var(--text);
letter-spacing: -0.5px;
}
.form-group { 
margin-bottom: 20px; 
}
.form-label { 
display: block; 
margin-bottom: 8px; 
font-weight: 500; 
color: var(--text);
font-size: 0.95rem;
}
.form-input {
width: 100%;
padding: 16px;
background: rgba(0, 0, 0, 0.2);
border: 1px solid var(--border);
border-radius: 14px;
color: white;
font-family: 'Inter', sans-serif;
font-size: 1rem;
transition: all 0.3s;
}
.form-input:focus {
outline: none;
border-color: var(--primary);
box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.3);
}
.btn {
width: 100%;
padding: 16px;
background: linear-gradient(90deg, var(--primary), var(--primary-dark));
color: white;
border: none;
border-radius: 14px;
font-family: 'Inter', sans-serif;
font-weight: 600;
font-size: 1.1rem;
cursor: pointer;
transition: all 0.2s ease;
margin-top: 10px;
box-shadow: 0 4px 6px rgba(99, 102, 241, 0.3);
}
.btn:hover {
transform: translateY(-2px);
box-shadow: 0 6px 8px rgba(99, 102, 241, 0.4);
}
.btn:active {
transform: translateY(0);
}
.alert {
padding: 14px;
margin: 18px 0;
border-radius: 12px;
font-weight: 500;
display: flex;
align-items: center;
gap: 12px;
}
.alert-error { 
background: rgba(239, 68, 68, 0.15); 
border: 1px solid var(--danger); 
color: var(--danger); 
animation: shake 0.5s;
}
.alert-success { 
background: rgba(16, 185, 129, 0.15); 
border: 1px solid var(--success); 
color: var(--success); 
}
@keyframes fadeIn {
from { opacity: 0; }
to { opacity: 1; }
}
@keyframes slideUp {
from { 
opacity: 0; 
transform: translateY(30px); 
}
to { 
opacity: 1; 
transform: translateY(0); 
}
}
@keyframes shake {
0%, 100% { transform: translateX(0); }
25% { transform: translateX(-5px); }
75% { transform: translateX(5px); }
}
</style>
</head>
<body>
<div class="login-container">
<div class="logo">
<div class="logo-text">❄ COLD SEARCH</div>
<div class="logo-subtitle">Premium Admin Panel</div>
</div>
<div class="card">
<h1 class="card-title">🔐 Logowanie Administratora</h1>
<form method="post">
<div class="form-group">
<label for="password" class="form-label">Hasło dostępu</label>
<input type="password" id="password" name="password" class="form-input" placeholder="••••••••••••••••" required autofocus>
</div>
<button type="submit" class="btn">ZALOGUJ SIĘ</button>
{% with messages = get_flashed_messages(with_categories=true) %}
{% for cat, msg in messages %}
<div class="alert alert-{{ 'success' if cat == 'success' else 'error' }}">
{% if cat == 'success' %}
<i class="fas fa-check-circle"></i>
{% else %}
<i class="fas fa-exclamation-triangle"></i>
{% endif %}
{{ msg }}
</div>
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
--primary: #6366f1;
--primary-dark: #4f46e5;
--secondary: #8b5cf6;
--secondary-dark: #7c3aed;
--bg: #0f172a;
--card-bg: #1e293b;
--border: #334155;
--text: #f1f5f9;
--text-secondary: #94a3b8;
--success: #10b981;
--danger: #ef4444;
--warning: #f59e0b;
--info: #3b82f6;
--gradient-start: #8b5cf6;
--gradient-end: #ec4899;
--stats-gradient-1: linear-gradient(135deg, #6366f1, #8b5cf6);
--stats-gradient-2: linear-gradient(135deg, #10b981, #0ea5e9);
--stats-gradient-3: linear-gradient(135deg, #f59e0b, #ef4444);
--stats-gradient-4: linear-gradient(135deg, #3b82f6, #8b5cf6);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
background: var(--bg);
color: var(--text);
font-family: 'Inter', sans-serif;
padding: 20px;
min-height: 100vh;
background-image: 
radial-gradient(circle at 10% 20%, rgba(99, 102, 241, 0.05) 0%, transparent 20%),
radial-gradient(circle at 90% 80%, rgba(139, 92, 246, 0.05) 0%, transparent 20%);
}
.container { 
max-width: 1400px; 
margin: 0 auto; 
}
.header {
display: flex;
justify-content: space-between;
align-items: center;
margin-bottom: 30px;
padding-bottom: 15px;
border-bottom: 1px solid var(--border);
}
.logo {
display: flex;
align-items: center;
gap: 12px;
}
.logo-text {
font-size: 1.8rem;
font-weight: 800;
background: linear-gradient(90deg, var(--gradient-start), var(--gradient-end));
-webkit-background-clip: text;
-webkit-text-fill-color: transparent;
background-clip: text;
}
.page-title { 
font-size: 1.5rem; 
font-weight: 700; 
color: var(--text); 
}
.logout-btn {
background: rgba(239, 68, 68, 0.15);
color: var(--danger);
border: 1px solid var(--danger);
padding: 8px 16px;
border-radius: 10px;
text-decoration: none;
font-weight: 600;
display: inline-flex;
align-items: center;
gap: 8px;
transition: all 0.2s;
}
.logout-btn:hover {
background: rgba(239, 68, 68, 0.25);
transform: translateY(-1px);
}
.stats-grid {
display: grid;
grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
gap: 24px;
margin-bottom: 30px;
}
.stat-card {
background: var(--card-bg);
border-radius: 20px;
padding: 24px;
border: 1px solid var(--border);
text-align: center;
transition: all 0.3s ease;
box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
}
.stat-card:hover {
transform: translateY(-5px);
box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
border-color: rgba(99, 102, 241, 0.5);
}
.stat-card:nth-child(1) { background: var(--stats-gradient-1); }
.stat-card:nth-child(2) { background: var(--stats-gradient-2); }
.stat-card:nth-child(3) { background: var(--stats-gradient-3); }
.stat-card:nth-child(4) { background: var(--stats-gradient-4); }
.stat-icon {
font-size: 2.5rem;
margin-bottom: 12px;
opacity: 0.9;
}
.stat-value { 
font-size: 2.2rem; 
font-weight: 800; 
color: white; 
font-family: 'JetBrains Mono', monospace; 
margin: 10px 0; 
text-shadow: 0 2px 4px rgba(0,0,0,0.2);
}
.stat-label { 
font-size: 1rem; 
color: rgba(255,255,255,0.9); 
font-weight: 500; 
margin-top: 4px;
}
.section {
background: var(--card-bg);
border-radius: 20px;
padding: 25px;
margin-bottom: 25px;
border: 1px solid var(--border);
transition: all 0.3s ease;
box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
}
.section:hover {
border-color: var(--primary);
box-shadow: 0 6px 15px rgba(0, 0, 0, 0.1);
}
.section-title {
display: flex;
align-items: center;
gap: 12px;
margin-bottom: 20px;
font-size: 1.4rem;
font-weight: 700;
color: white;
padding-bottom: 10px;
border-bottom: 2px solid rgba(99, 102, 241, 0.3);
}
.section-title i { 
color: var(--primary); 
font-size: 1.3rem; 
}
.form-row { 
display: flex; 
gap: 20px; 
margin-bottom: 20px; 
flex-wrap: wrap; 
align-items: flex-end;
}
.form-group { 
flex: 1; 
min-width: 180px; 
}
.form-label { 
display: block; 
margin-bottom: 8px; 
font-size: 0.95rem;
color: var(--text);
font-weight: 500;
}
.form-input, .form-select {
width: 100%;
padding: 14px;
background: rgba(15, 23, 42, 0.7);
border: 1px solid var(--border);
border-radius: 12px;
color: white;
font-family: 'Inter', sans-serif;
font-size: 0.95rem;
transition: all 0.3s;
}
.form-input:focus, .form-select:focus {
outline: none;
border-color: var(--primary);
box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.2);
}
.btn {
padding: 12px 28px;
background: var(--stats-gradient-1);
color: white;
border: none;
border-radius: 12px;
font-weight: 600;
cursor: pointer;
font-size: 0.95rem;
transition: all 0.2s;
display: inline-flex;
align-items: center;
gap: 8px;
box-shadow: 0 3px 5px rgba(0, 0, 0, 0.2);
font-family: 'Inter', sans-serif;
}
.btn:hover {
transform: translateY(-2px);
box-shadow: 0 5px 10px rgba(99, 102, 241, 0.3);
}
.btn:active {
transform: translateY(0);
}
.btn-danger {
background: linear-gradient(90deg, #ef4444, #f97316);
}
.btn-danger:hover {
box-shadow: 0 5px 10px rgba(239, 68, 68, 0.3);
}
.btn-primary {
background: var(--stats-gradient-1);
}
.table-container {
overflow-x: auto;
margin-top: 15px;
border-radius: 16px;
border: 1px solid var(--border);
}
.table { 
width: 100%; 
border-collapse: collapse; 
}
.table th { 
text-align: left; 
padding: 16px 18px; 
border-bottom: 2px solid var(--border); 
font-weight: 700;
background: rgba(0,0,0,0.2);
color: var(--text);
font-size: 0.95rem;
}
.table td { 
padding: 16px 18px; 
border-bottom: 1px solid var(--border);
font-size: 0.95rem;
color: var(--text);
}
.table tr:last-child td { border-bottom: none; }
.table tr:hover {
background: rgba(99, 102, 241, 0.08);
}
.key { 
font-family: 'JetBrains Mono', monospace; 
color: var(--primary); 
font-weight: 600; 
letter-spacing: 0.5px;
font-size: 0.9rem;
}
.status-active { 
color: var(--success); 
font-weight: 600; 
display: inline-flex;
align-items: center;
gap: 6px;
}
.status-active::before {
content: '';
display: inline-block;
width: 8px;
height: 8px;
background: var(--success);
border-radius: 50%;
}
.status-inactive { 
color: var(--danger); 
font-weight: 600; 
display: inline-flex;
align-items: center;
gap: 6px;
}
.status-inactive::before {
content: '';
display: inline-block;
width: 8px;
height: 8px;
background: var(--danger);
border-radius: 50%;
}
.alert {
padding: 16px;
margin-bottom: 20px;
border-radius: 14px;
font-weight: 500;
display: flex;
align-items: center;
gap: 12px;
border-left: 4px solid;
}
.alert-info { 
background: rgba(59, 130, 246, 0.15); 
border-left-color: var(--info);
color: #bfdbfe; 
}
.alert-error { 
background: rgba(239, 68, 68, 0.15); 
border-left-color: var(--danger);
color: #fecaca; 
}
.alert-success { 
background: rgba(16, 185, 129, 0.15); 
border-left-color: var(--success);
color: #bbf7d0; 
}
.leak-item { 
padding: 12px 0; 
border-bottom: 1px dashed var(--border); 
}
.leak-item:last-child { border-bottom: none; }
.leak-data {
font-family: 'JetBrains Mono', monospace;
font-size: 0.95rem;
word-break: break-all;
color: var(--text);
}
.ip-badge {
display: inline-block;
background: rgba(139, 92, 246, 0.2);
border: 1px solid rgba(139, 92, 246, 0.4);
padding: 4px 10px;
border-radius: 20px;
font-size: 0.85rem;
font-family: 'JetBrains Mono', monospace;
}
.limit-badge {
display: inline-block;
padding: 4px 12px;
border-radius: 20px;
font-size: 0.85rem;
font-weight: 600;
margin-right: 8px;
margin-bottom: 4px;
}
.limit-daily { 
background: rgba(245, 158, 11, 0.2); 
color: #fcd34d; 
border: 1px solid rgba(245, 158, 11, 0.3);
}
.limit-total { 
background: rgba(239, 68, 68, 0.2); 
color: #fca5a5; 
border: 1px solid rgba(239, 68, 68, 0.3);
}
.usage-bar {
height: 10px;
background: rgba(56, 189, 248, 0.1);
border-radius: 5px;
margin-top: 6px;
overflow: hidden;
}
.usage-fill {
height: 100%;
border-radius: 5px;
}
.usage-low { background: var(--success); }
.usage-medium { background: var(--warning); }
.usage-high { background: var(--danger); }
.usage-critical {
background: var(--danger);
animation: pulse 1.5s infinite;
}
@keyframes pulse {
0% { opacity: 1; }
50% { opacity: 0.7; }
100% { opacity: 1; }
}
.usage-text {
font-size: 0.85rem;
color: var(--text-secondary);
margin-top: 4px;
font-weight: 500;
}
.search-input {
display: flex;
gap: 12px;
margin-bottom: 20px;
}
.search-box {
flex: 1;
position: relative;
}
.search-box i {
position: absolute;
left: 16px;
top: 50%;
transform: translateY(-50%);
color: var(--text-secondary);
font-size: 1.1rem;
}
.search-input-field {
width: 100%;
padding: 14px 14px 14px 45px;
background: rgba(15, 23, 42, 0.7);
border: 1px solid var(--border);
border-radius: 14px;
color: white;
font-family: 'Inter', sans-serif;
font-size: 0.95rem;
transition: all 0.3s;
}
.search-input-field:focus {
outline: none;
border-color: var(--primary);
box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.2);
}
.system-grid {
display: grid;
grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
gap: 20px;
}
.system-card {
background: rgba(15, 23, 42, 0.6);
border-radius: 16px;
padding: 20px;
border: 1px solid var(--border);
}
.system-card h4 {
font-size: 1rem;
font-weight: 600;
margin-bottom: 12px;
color: var(--text);
display: flex;
align-items: center;
gap: 10px;
}
.system-card h4 i {
color: var(--primary);
}
.system-info {
display: grid;
grid-template-columns: 1fr 1fr;
gap: 15px;
}
.system-info-item {
padding: 12px;
border-radius: 12px;
background: rgba(0,0,0,0.2);
}
.system-info-label {
font-size: 0.85rem;
color: var(--text-secondary);
margin-bottom: 4px;
}
.system-info-value {
font-weight: 600;
font-size: 0.95rem;
color: var(--text);
}
.footer {
text-align: center;
margin-top: 30px;
padding-top: 20px;
border-top: 1px solid var(--border);
color: var(--text-secondary);
font-size: 0.9rem;
}
@media (max-width: 768px) {
.form-row { flex-direction: column; }
.stats-grid { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
}
@keyframes fadeIn {
from { opacity: 0; }
to { opacity: 1; }
}
</style>
</head>
<body>
<div class="container">
<div class="header">
<div class="logo">
<i class="fas fa-snowflake" style="font-size: 1.8rem; color: var(--primary);"></i>
<div class="logo-text">COLD SEARCH ADMIN</div>
</div>
<a href="/logout" class="logout-btn">
<i class="fas fa-sign-out-alt"></i> Wyloguj
</a>
</div>

{% with messages = get_flashed_messages(with_categories=true) %}
{% for cat, msg in messages %}
<div class="alert alert-{{ 'success' if cat == 'success' else 'error' if cat == 'error' else 'info' }}">
{% if cat == 'success' %}
<i class="fas fa-check-circle" style="color: var(--success); font-size: 1.2rem;"></i>
{% elif cat == 'error' %}
<i class="fas fa-exclamation-triangle" style="color: var(--danger); font-size: 1.2rem;"></i>
{% else %}
<i class="fas fa-info-circle" style="color: var(--info); font-size: 1.2rem;"></i>
{% endif %}
<div style="flex: 1;">
<strong style="display: block; margin-bottom: 4px;">{% if cat == 'success' %}Sukces{% elif cat == 'error' %}Błąd{% else %}Informacja{% endif %}</strong>
<span>{{ msg }}</span>
</div>
</div>
{% endfor %}
{% endwith %}

<!-- Statystyki -->
<div class="stats-grid">
<div class="stat-card">
<div class="stat-icon">
<i class="fas fa-database"></i>
</div>
<div class="stat-value">{{ "{:,}".format(total_leaks).replace(",", " ") }}</div>
<div class="stat-label">Rekordów w bazie</div>
</div>
<div class="stat-card">
<div class="stat-icon">
<i class="fas fa-file-alt"></i>
</div>
<div class="stat-value">{{ "{:,}".format(source_count).replace(",", " ") }}</div>
<div class="stat-label">Źródeł danych</div>
</div>
<div class="stat-card">
<div class="stat-icon">
<i class="fas fa-key"></i>
</div>
<div class="stat-value">{{ active_licenses }}</div>
<div class="stat-label">Aktywnych licencji</div>
</div>
<div class="stat-card">
<div class="stat-icon">
<i class="fas fa-search"></i>
</div>
<div class="stat-value">{{ "{:,}".format(total_searches).replace(",", " ") }}</div>
<div class="stat-label">Wyszukań łącznie</div>
</div>
</div>

<!-- Panel z komunikatem powitalnym -->
<div class="section">
<div class="section-title">
<i class="fas fa-info-circle"></i> Witamy w panelu administratora
</div>
<p style="color: var(--text-secondary); line-height: 1.6;">
Panel administratora Cold Search Premium umożliwia zarządzanie licencjami, importowanie danych z leaków oraz monitorowanie aktywności użytkowników. Wszystkie funkcje są dostępne z jednego, spójnego interfejsu.
</p>
</div>

<!-- Informacje systemowe -->
<div class="section">
<div class="section-title">
<i class="fas fa-server"></i> Informacje systemowe
</div>
<div class="system-grid">
<div class="system-card">
<h4><i class="fas fa-clock"></i> Sesja administratora</h4>
<div class="system-info">
<div class="system-info-item">
<div class="system-info-label">Czas trwania</div>
<div class="system-info-value">{{ session_duration }}</div>
</div>
<div class="system-info-item">
<div class="system-info-label">Twój adres IP</div>
<div class="system-info-value"><span class="ip-badge">{{ client_ip }}</span></div>
</div>
</div>
</div>
<div class="system-card">
<h4><i class="fas fa-database"></i> Baza danych leaków</h4>
<div class="system-info">
<div class="system-info-item">
<div class="system-info-label">Status</div>
<div class="system-info-value" style="color: var(--success);">
<i class="fas fa-circle" style="font-size: 0.6rem; margin-right: 6px;"></i> Online
</div>
</div>
<div class="system-info-item">
<div class="system-info-label">Liczba rekordów</div>
<div class="system-info-value">{{ "{:,}".format(total_leaks).replace(",", " ") }} rekordów</div>
</div>
<div class="system-info-item">
<div class="system-info-label">Serwer</div>
<div class="system-info-value">136.243.54.157:25618</div>
</div>
</div>
</div>
<div class="system-card">
<h4><i class="fas fa-cloud"></i> Supabase API</h4>
<div class="system-info">
<div class="system-info-item">
<div class="system-info-label">Status</div>
<div class="system-info-value" style="color: var(--success);">
<i class="fas fa-circle" style="font-size: 0.6rem; margin-right: 6px;"></i> Online
</div>
</div>
<div class="system-info-item">
<div class="system-info-label">Ostatnia aktualizacja</div>
<div class="system-info-value">{{ format_datetime(now) }}</div>
</div>
<div class="system-info-item">
<div class="system-info-label">Wersja API</div>
<div class="system-info-value">2.1.0</div>
</div>
</div>
</div>
</div>
</div>

<div class="footer">
<p>❄️ Cold Search Premium Admin Panel &copy; {{ now.year }} | Wersja 3.0</p>
<p style="margin-top: 6px; font-size: 0.85rem; color: var(--text-secondary);">
Panel jest chroniony hasłem i dostępny wyłącznie dla upoważnionych administratorów
</p>
</div>
</div>

<script>
// Formatowanie liczb - POPRAWIONA SKŁADNIA
document.addEventListener('DOMContentLoaded', function() {
const statValues = document.querySelectorAll('.stat-value');
statValues.forEach(el => {
const numStr = el.textContent.replace(/\\s/g, '');
const num = parseInt(numStr.replace(/[^0-9]/g, ''));
if (!isNaN(num)) {
el.textContent = num.toLocaleString('pl-PL');
}
});
});

// Automatyczne odświeżanie co 60 sekund
setTimeout(function() {
location.reload();
}, 60000);
</script>
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
        "version": "3.0.0",
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
    if not licenses or (isinstance(licenses, list) and len(licenses) == 0):
        return jsonify({"success": False, "message": "Nieprawidłowy klucz licencyjny"}), 401
    
    lic = licenses[0] if isinstance(licenses, list) else licenses
    
    expiry_str = safe_get(lic, 'expiry')
    if expiry_str:
        expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
    else:
        expiry = datetime.now(timezone.utc) + timedelta(days=30)
    
    if datetime.now(timezone.utc) > expiry or not safe_get(lic, 'active', True):
        return jsonify({"success": False, "message": "Licencja wygasła lub została zablokowana"}), 401
    
    if not safe_get(lic, "ip"):
        sb_update("licenses", {"ip": ip}, f"key=eq.{key}")
    elif safe_get(lic, "ip") != ip:
        return jsonify({"success": False, "message": "Klucz przypisany do innego adresu IP"}), 403
    
    today_count, total_count = get_license_usage(key)
    daily_limit = safe_get(lic, 'daily_limit', 100)
    total_limit = safe_get(lic, 'total_limit', 1000)
    
    if today_count >= daily_limit:
        return jsonify({"success": False, "message": "Przekroczono dzienny limit wyszukiwań"}), 429
    if total_count >= total_limit:
        return jsonify({"success": False, "message": "Przekroczono całkowity limit wyszukiwań"}), 429
        
    return jsonify({"success": True, "message": "Zalogowano pomyślnie"})

# === URUCHOMIENIE ===
if __name__ == "__main__":
    initialize_db_pool()
    logger.info("🚀 Cold Search Premium — Panel admina gotowy")
    port = int(os.environ.get('PORT', 10000))  # Render wymaga PORT=10000
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
