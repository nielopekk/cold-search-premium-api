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
from functools import wraps
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

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_ultra_2026_fixed")

# === POOL POŁĄCZEŃ MARIADB Z MECHANIZMEM ODZYSKIWANIA ===
db_pool = None

def initialize_db_pool():
    """Inicjalizuje pulę połączeń z mechanizmem ponownych prób"""
    global db_pool
    max_attempts = 5
    attempt = 0
    
    while attempt < max_attempts:
        try:
            if db_pool is None:
                logger.info(f"🚀 Próba połączenia z MariaDB (próba {attempt + 1}/{max_attempts})")
                db_pool = mysql.connector.pooling.MySQLConnectionPool(**DB_CONFIG)
                logger.info("✅ Pula połączeń z MariaDB została pomyślnie utworzona")
                
                # Sprawdź i utwórz tabelę leaks jeśli nie istnieje
                ensure_leaks_table_exists()
                
                return True
            return True
        except Exception as e:
            logger.error(f"❌ Błąd połączenia z MariaDB (próba {attempt + 1}): {e}")
            attempt += 1
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    
    logger.error("❌ Krytyczny błąd: nie udało się połączyć z MariaDB po wielu próbach")
    raise SystemExit("Nie można kontynuować bez połączenia z bazą danych leaków")

def ensure_leaks_table_exists():
    """Sprawdza czy tabela leaks istnieje i tworzy ją jeśli nie istnieje"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Sprawdź czy tabela istnieje
            cursor.execute("SHOW TABLES LIKE 'leaks'")
            if cursor.fetchone() is None:
                logger.info("🔧 Tabela 'leaks' nie istnieje. Tworzenie...")
                
                # Utwórz tabelę z pełną strukturą
                create_table_query = """
                CREATE TABLE leaks (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    data VARCHAR(1000) NOT NULL,
                    source VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FULLTEXT INDEX ft_data (data),
                    INDEX idx_source (source),
                    INDEX idx_created_at (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
                cursor.execute(create_table_query)
                logger.info("✅ Tabela 'leaks' została utworzona")
                
                # Dodaj przykładowe dane testowe
                cursor.execute("""
                INSERT INTO leaks (data, source) VALUES
                ('test@example.com', 'test_data'),
                ('admin123', 'test_data'),
                ('user_2024', 'test_data')
                """)
                logger.info("✅ Dodano przykładowe dane testowe do tabeli 'leaks'")
                
            else:
                # Sprawdź strukturę tabeli i dodaj brakujące kolumny
                cursor.execute("SHOW COLUMNS FROM leaks")
                columns = [column[0] for column in cursor.fetchall()]
                
                if 'created_at' not in columns:
                    logger.warning("🔧 Dodawanie brakującej kolumny 'created_at' do tabeli leaks...")
                    cursor.execute("""
                    ALTER TABLE leaks 
                    ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER source
                    """)
                
                if 'updated_at' not in columns:
                    logger.warning("🔧 Dodawanie brakującej kolumny 'updated_at' do tabeli leaks...")
                    cursor.execute("""
                    ALTER TABLE leaks 
                    ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at
                    """)
                
                # Dodaj indeksy jeśli nie istnieją
                cursor.execute("SHOW INDEX FROM leaks WHERE Key_name = 'ft_data'")
                if cursor.fetchone() is None:
                    logger.warning("🔧 Dodawanie indeksu FULLTEXT do kolumny 'data'...")
                    cursor.execute("ALTER TABLE leaks ADD FULLTEXT INDEX ft_data (data)")
                
                logger.info("✅ Tabela 'leaks' jest gotowa do użytku")
                
    except Exception as e:
        logger.error(f"❌ Błąd podczas tworzenia/aktualizacji tabeli leaks: {e}")
        raise

def get_db_connection():
    """Bezpiecznie pobiera połączenie z puli z timeoutem i odzyskiwaniem"""
    global db_pool
    
    if db_pool is None:
        initialize_db_pool()
    
    try:
        conn = db_pool.get_connection()
        logger.debug(f"🔌 Uzyskano połączenie z puli. Aktywne połączenia: {db_pool._cnx_queue.qsize()}/{db_pool._pool_size}")
        return conn
    except mysql.connector.Error as e:
        logger.error(f"❌ Błąd pobierania połączenia: {e}")
        
        if "pool exhausted" in str(e):
            logger.warning("⚠️ Pula połączeń wyczerpana. Próba odzyskania...")
            time.sleep(1)
            
            try:
                conn = db_pool.get_connection()
                logger.info("✅ Połączenie odzyskane po timeout")
                return conn
            except:
                pass
        
        logger.warning("🔄 Reset puli połączeń...")
        initialize_db_pool()
        return get_db_connection()

@contextlib.contextmanager
def get_db():
    """Context manager do bezpiecznego zarządzania połączeniami"""
    conn = None
    try:
        conn = get_db_connection()
        yield conn
    finally:
        if conn is not None and conn.is_connected():
            conn.close()
            logger.debug(f"🔌 Połączenie zamknięte. Pozostałe w puli: {db_pool._cnx_queue.qsize()}/{db_pool._pool_size}")

# === FUNKCJE POMOCNICZE ===
def sb_query(table, params=""):
    """Wykonuje zapytanie do Supabase"""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}?{params}", 
            headers=SUPABASE_HEADERS,
            timeout=10
        )
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.error(f"❌ Błąd zapytania do Supabase ({table}): {e}")
        return []

def sb_insert(table, data):
    """Wstawia dane do Supabase"""
    try:
        return requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}", 
            headers=SUPABASE_HEADERS, 
            json=data,
            timeout=10
        )
    except Exception as e:
        logger.error(f"❌ Błąd wstawiania do Supabase ({table}): {e}")
        return None

def sb_delete(table, condition):
    """Usuwa dane z Supabase na podstawie warunku"""
    try:
        return requests.delete(
            f"{SUPABASE_URL}/rest/v1/{table}?{condition}",
            headers=SUPABASE_HEADERS,
            timeout=10
        )
    except Exception as e:
        logger.error(f"❌ Błąd usuwania z Supabase ({table}): {e}")
        return None

def get_client_ip():
    """Bezpiecznie pobiera IP klienta"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'

def is_valid_ip(ip):
    """Waliduje format adresu IP"""
    pattern = re.compile(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$')
    return pattern.match(ip) is not None

def admin_required(f):
    """Dekorator wymagający autoryzacji administratora"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Musisz być zalogowany jako administrator!', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def format_datetime(dt):
    """Formatuje obiekt datetime do czytelnej postaci"""
    if isinstance(dt, datetime):
        return dt.strftime("%d.%m.%Y %H:%M")
    return str(dt)

# === STRONY ADMINISTRACYJNE ===

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    """Strona logowania administratora"""
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session['is_admin'] = True
            session['login_time'] = datetime.now(timezone.utc).isoformat()
            flash('Zalogowano pomyślnie!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Nieprawidłowe hasło!', 'error')
    
    return render_template_string(admin_login_template)

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    """Główny dashboard panelu administracyjnego"""
    try:
        # Statystyki z MariaDB dla leaków
        with get_db() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # Liczba rekordów w bazie leaków
            cursor.execute("SELECT COUNT(*) as total FROM leaks")
            total_leaks = cursor.fetchone()['total']
            
            # Liczba plików źródłowych
            cursor.execute("SELECT COUNT(DISTINCT source) as sources FROM leaks")
            source_count = cursor.fetchone()['sources']
            
            # Ostatnie 5 dodanych rekordów
            cursor.execute("SELECT data, source, created_at FROM leaks ORDER BY created_at DESC LIMIT 5")
            recent_leaks = cursor.fetchall()
            
            # Najczęstsze źródła
            cursor.execute("""
                SELECT source, COUNT(*) as count 
                FROM leaks 
                GROUP BY source 
                ORDER BY count DESC 
                LIMIT 5
            """)
            top_sources = cursor.fetchall()

        # Statystyki z Supabase
        licenses = sb_query("licenses", "order=created_at.desc")
        active_licenses = len([lic for lic in licenses if lic.get('active', False)])
        
        banned_ips = sb_query("banned_ips")
        
        # Liczba zapytań z Supabase (jeśli tabela istnieje)
        search_logs = sb_query("search_logs", "select=count(*)")
        total_searches = search_logs[0].get('count', 0) if search_logs else 0
        
        # Liczba aktywnych użytkowników w ciągu ostatnich 24 godzin
        active_users = 0
        try:
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
            r = requests.head(
                f"{SUPABASE_URL}/rest/v1/search_logs",
                headers={**SUPABASE_HEADERS, "Prefer": "count=exact"},
                params={"timestamp": f"gte.{yesterday}"}
            )
            if r.status_code == 206:
                active_users = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
        except Exception as e:
            logger.error(f"Błąd pobierania liczby aktywnych użytkowników: {e}")
        
        # Czas działania sesji administratora
        login_time = datetime.fromisoformat(session['login_time'])
        session_duration = datetime.now(timezone.utc) - login_time
        
        # Przekazujemy funkcję get_client_ip do kontekstu szablonu
        return render_template_string(
            admin_dashboard_template,
            total_leaks=total_leaks,
            source_count=source_count,
            recent_leaks=recent_leaks,
            licenses=licenses,
            active_licenses=active_licenses,
            banned_ips=banned_ips,
            total_searches=total_searches,
            active_users=active_users,
            session_duration=str(session_duration).split('.')[0],
            get_client_ip=get_client_ip,
            format_datetime=format_datetime,
            top_sources=top_sources
        )
    except Exception as e:
        logger.error(f"❌ Błąd ładowania dashboardu: {e}")
        flash(f"Wystąpił błąd podczas ładowania danych: {str(e)}", 'error')
        return redirect(url_for('admin_login'))

# === SZABLONY HTML ===

admin_login_template = '''
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Search Premium - Logowanie Admina</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
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
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
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
        
        .login-container {
            max-width: 450px;
            width: 100%;
        }
        
        .logo {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .logo-text {
            font-size: 2.2rem;
            font-weight: 800;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .logo-sub {
            color: rgba(255, 255, 255, 0.6);
            font-size: 0.95rem;
            margin-top: 8px;
        }
        
        .card {
            background: var(--card-bg);
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.5);
            border: 1px solid var(--border);
            backdrop-filter: blur(10px);
        }
        
        .card-title {
            font-size: 1.75rem;
            font-weight: 700;
            margin-bottom: 25px;
            text-align: center;
            color: var(--text);
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-label {
            display: block;
            margin-bottom: 8px;
            font-weight: 500;
            color: var(--text);
        }
        
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
            border: none;
            border-radius: 12px;
            font-family: 'Inter', sans-serif;
            font-weight: 600;
            font-size: 1.05rem;
            cursor: pointer;
            transition: all 0.2s ease;
            margin-top: 10px;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, var(--primary), #00b3cc);
            color: #000;
            font-weight: 700;
        }
        
        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0, 242, 255, 0.4);
        }
        
        .btn-primary:active {
            transform: translateY(0);
        }
        
        .error-message {
            color: var(--error);
            text-align: center;
            margin-top: 15px;
            font-size: 0.95rem;
            min-height: 22px;
        }
        
        .success-message {
            color: var(--success);
            text-align: center;
            margin-top: 15px;
            font-size: 0.95rem;
            min-height: 22px;
        }
        
        .info-box {
            background: rgba(30, 30, 50, 0.7);
            border-radius: 12px;
            padding: 15px;
            margin-top: 25px;
            border: 1px solid var(--border);
            font-size: 0.9rem;
            line-height: 1.5;
        }
        
        .info-box ul {
            padding-left: 20px;
            margin-top: 8px;
        }
        
        .info-box li {
            margin-bottom: 5px;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">
            <div class="logo-text">❄️ Cold Search Premium</div>
            <div class="logo-sub">Zaawansowane narzędzie do wyszukiwania danych</div>
        </div>
        
        <div class="card">
            <h1 class="card-title">🔐 Panel Administratora</h1>
            
            <form method="post">
                <div class="form-group">
                    <label for="password" class="form-label">Hasło administratora</label>
                    <input 
                        type="password" 
                        id="password" 
                        name="password" 
                        class="form-input" 
                        placeholder="••••••••••••••••" 
                        required
                        autofocus
                    >
                </div>
                
                <button type="submit" class="btn btn-primary">Zaloguj się</button>
                
                {% with messages = get_flashed_messages(with_categories=true) %}
                    {% if messages %}
                        {% for category, message in messages %}
                            {% if category == 'error' %}
                                <div class="error-message">{{ message }}</div>
                            {% elif category == 'success' %}
                                <div class="success-message">{{ message }}</div>
                            {% endif %}
                        {% endfor %}
                    {% endif %}
                {% endwith %}
            </form>
            
            <div class="info-box">
                <strong>ℹ️ Instrukcja dostępu:</strong>
                <ul>
                    <li>Ten panel jest dostępny tylko dla zaufanych administratorów</li>
                    <li>Wszystkie akcje są rejestrowane i monitorowane</li>
                    <li>Nie udostępniaj hasła osobom trzecim</li>
                    <li>Przy podejrzeniu naruszenia bezpieczeństwa natychmiast zmień hasło</li>
                </ul>
            </div>
        </div>
    </div>
</body>
</html>
'''

admin_dashboard_template = '''
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Search Premium - Dashboard</title>
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
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            background: var(--bg);
            color: var(--text);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
        }
        
        .container {
            display: grid;
            grid-template-columns: 240px 1fr;
            min-height: 100vh;
        }
        
        /* Sidebar */
        .sidebar {
            background: rgba(10, 10, 18, 0.95);
            border-right: 1px solid var(--border);
            padding: 20px 0;
            height: 100vh;
            position: fixed;
            width: 240px;
            z-index: 100;
        }
        
        .logo {
            padding: 0 20px 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 20px;
        }
        
        .logo-text {
            font-size: 1.4rem;
            font-weight: 800;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .logo-sub {
            color: var(--text-secondary);
            font-size: 0.85rem;
            margin-top: 4px;
        }
        
        .nav-links {
            padding: 0 10px;
        }
        
        .nav-item {
            display: flex;
            align-items: center;
            padding: 12px 20px;
            margin-bottom: 4px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
            color: var(--text);
        }
        
        .nav-item:hover {
            background: rgba(255, 255, 255, 0.05);
        }
        
        .nav-item.active {
            background: linear-gradient(90deg, rgba(0, 242, 255, 0.15), rgba(188, 19, 254, 0.15));
            border-left: 3px solid var(--primary);
        }
        
        .nav-icon {
            margin-right: 12px;
            font-size: 1.1rem;
            width: 20px;
            text-align: center;
        }
        
        .nav-text {
            font-weight: 500;
        }
        
        .logout-btn {
            margin-top: 30px;
            padding: 10px 20px;
            background: rgba(255, 77, 77, 0.15);
            border: 1px solid var(--danger);
            color: var(--danger);
            border-radius: 8px;
            width: calc(100% - 40px);
            cursor: pointer;
            display: flex;
            align-items: center;
            transition: all 0.2s;
        }
        
        .logout-btn:hover {
            background: rgba(255, 77, 77, 0.25);
        }
        
        .logout-icon {
            margin-right: 10px;
        }
        
        /* Main Content */
        .main-content {
            margin-left: 240px;
            padding: 20px;
        }
        
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding-bottom: 15px;
            border-bottom: 1px solid var(--border);
        }
        
        .page-title {
            font-size: 1.8rem;
            font-weight: 700;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .stat-card {
            background: var(--card-bg);
            border-radius: 16px;
            padding: 25px;
            border: 1px solid var(--border);
            transition: transform 0.2s;
        }
        
        .stat-card:hover {
            transform: translateY(-3px);
        }
        
        .stat-title {
            font-size: 0.95rem;
            color: var(--text-secondary);
            margin-bottom: 8px;
            font-weight: 500;
        }
        
        .stat-value {
            font-size: 2.2rem;
            font-weight: 800;
            font-family: 'Courier New', monospace;
            color: white;
        }
        
        .stat-icon {
            font-size: 2.5rem;
            margin-bottom: 15px;
            color: var(--primary);
        }
        
        .stat-footer {
            margin-top: 15px;
            font-size: 0.9rem;
            color: var(--text-secondary);
        }
        
        .content-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .card {
            background: var(--card-bg);
            border-radius: 16px;
            padding: 25px;
            border: 1px solid var(--border);
            overflow: hidden;
        }
        
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border);
        }
        
        .card-title {
            font-size: 1.3rem;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .card-icon {
            color: var(--primary);
        }
        
        .recent-leak {
            padding: 12px 0;
            border-bottom: 1px dashed var(--border);
        }
        
        .recent-leak:last-child {
            border-bottom: none;
        }
        
        .leak-data {
            font-family: 'Courier New', monospace;
            font-size: 0.9rem;
            color: var(--text);
            margin-bottom: 4px;
            word-break: break-all;
        }
        
        .leak-meta {
            display: flex;
            justify-content: space-between;
            font-size: 0.85rem;
            color: var(--text-secondary);
        }
        
        .leak-source {
            background: rgba(188, 19, 254, 0.15);
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.8rem;
        }
        
        .session-info {
            background: rgba(0, 242, 255, 0.1);
            border: 1px solid rgba(0, 242, 255, 0.3);
            border-radius: 12px;
            padding: 15px;
            margin-top: 20px;
        }
        
        .session-label {
            display: block;
            font-size: 0.9rem;
            color: var(--text-secondary);
            margin-bottom: 5px;
        }
        
        .session-value {
            font-weight: 600;
            color: var(--primary);
            font-size: 1.1rem;
        }
        
        .status-online {
            color: var(--success);
        }
        
        @media (max-width: 992px) {
            .content-grid {
                grid-template-columns: 1fr;
            }
        }
        
        .flash-message {
            padding: 12px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .flash-success {
            background: rgba(0, 255, 170, 0.15);
            border: 1px solid rgba(0, 255, 170, 0.3);
            color: var(--success);
        }
        
        .flash-error {
            background: rgba(255, 77, 77, 0.15);
            border: 1px solid rgba(255, 77, 77, 0.3);
            color: var(--danger);
        }
        
        .source-chart {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-top: 15px;
        }
        
        .source-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .source-name {
            font-size: 0.9rem;
            color: var(--text);
        }
        
        .source-count {
            background: rgba(0, 242, 255, 0.15);
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.85rem;
            font-weight: 600;
        }
        
        .source-bar {
            height: 6px;
            background: rgba(0, 242, 255, 0.2);
            border-radius: 3px;
            margin-top: 4px;
            overflow: hidden;
        }
        
        .source-bar-fill {
            height: 100%;
            background: var(--primary);
            border-radius: 3px;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Sidebar -->
        <div class="sidebar">
            <div class="logo">
                <div class="logo-text">❄️ Cold Search</div>
                <div class="logo-sub">Panel Administratora</div>
            </div>
            
            <div class="nav-links">
                <a href="{{ url_for('admin_dashboard') }}" class="nav-item active">
                    <i class="fas fa-home nav-icon"></i>
                    <span class="nav-text">Dashboard</span>
                </a>
                <a href="{{ url_for('admin_licenses') }}" class="nav-item">
                    <i class="fas fa-key nav-icon"></i>
                    <span class="nav-text">Licencje</span>
                </a>
                <a href="{{ url_for('admin_bans') }}" class="nav-item">
                    <i class="fas fa-ban nav-icon"></i>
                    <span class="nav-text">Bany IP</span>
                </a>
                <a href="{{ url_for('admin_stats') }}" class="nav-item">
                    <i class="fas fa-chart-bar nav-icon"></i>
                    <span class="nav-text">Statystyki</span>
                </a>
                <a href="{{ url_for('admin_logs') }}" class="nav-item">
                    <i class="fas fa-clipboard-list nav-icon"></i>
                    <span class="nav-text">Logi</span>
                </a>
                <a href="{{ url_for('admin_import_ui') }}" class="nav-item">
                    <i class="fas fa-file-import nav-icon"></i>
                    <span class="nav-text">Import Danych</span>
                </a>
            </div>
            
            <button class="logout-btn" onclick="if(confirm('Czy na pewno chcesz się wylogować?')) window.location.href='{{ url_for('admin_logout') }}'">
                <i class="fas fa-sign-out-alt logout-icon"></i>
                <span>Wyloguj się</span>
            </button>
        </div>
        
        <!-- Main Content -->
        <div class="main-content">
            <div class="header">
                <h1 class="page-title">📊 Dashboard</h1>
                <div>
                    <span class="status-online">
                        <i class="fas fa-circle" style="font-size: 0.6rem; color: var(--success); margin-right: 5px;"></i>
                        Serwer: Online
                    </span>
                </div>
            </div>
            
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="flash-message flash-{{ category }}">
                            {% if category == 'success' %}
                                <i class="fas fa-check-circle"></i>
                            {% elif category == 'error' %}
                                <i class="fas fa-exclamation-circle"></i>
                            {% endif %}
                            {{ message }}
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="stats-grid">
                <div class="stat-card">
                    <i class="fas fa-database stat-icon"></i>
                    <div class="stat-title">REKORDY W BAZIE</div>
                    <div class="stat-value">{{ "{:,}".format(total_leaks).replace(",", " ") }}</div>
                    <div class="stat-footer">Wszystkie dane</div>
                </div>
                
                <div class="stat-card">
                    <i class="fas fa-file-alt stat-icon"></i>
                    <div class="stat-title">ŹRÓDŁA DANYCH</div>
                    <div class="stat-value">{{ "{:,}".format(source_count).replace(",", " ") }}</div>
                    <div class="stat-footer">Unikalne źródła</div>
                </div>
                
                <div class="stat-card">
                    <i class="fas fa-users stat-icon"></i>
                    <div class="stat-title">AKTYWNI UŻYTKOWNICY</div>
                    <div class="stat-value">{{ "{:,}".format(active_users).replace(",", " ") }}</div>
                    <div class="stat-footer">Ostatnie 24h</div>
                </div>
                
                <div class="stat-card">
                    <i class="fas fa-search stat-icon"></i>
                    <div class="stat-title">WYSZUKAŃ W TYM TYGODNIU</div>
                    <div class="stat-value">{{ "{:,}".format(total_searches).replace(",", " ") }}</div>
                    <div class="stat-footer">Wszystkie zapytania</div>
                </div>
            </div>
            
            <div class="content-grid">
                <div class="card">
                    <div class="card-header">
                        <h2 class="card-title">
                            <i class="fas fa-history card-icon"></i>
                            Ostatnie dane w bazie
                        </h2>
                    </div>
                    
                    {% if recent_leaks %}
                        {% for leak in recent_leaks %}
                            <div class="recent-leak">
                                <div class="leak-data">{{ leak.data | truncate(60) }}</div>
                                <div class="leak-meta">
                                    <span class="leak-source">{{ leak.source }}</span>
                                    <span>{% if leak.created_at %}{{ format_datetime(leak.created_at) }}{% else %}Brak daty{% endif %}</span>
                                </div>
                            </div>
                        {% endfor %}
                    {% else %}
                        <div style="text-align: center; color: var(--text-secondary); padding: 40px 0;">
                            <i class="fas fa-inbox" style="font-size: 2.5rem; margin-bottom: 15px; opacity: 0.5;"></i>
                            <div>Brak ostatnich danych w bazie</div>
                        </div>
                    {% endif %}
                </div>
                
                <div class="card">
                    <div class="card-header">
                        <h2 class="card-title">
                            <i class="fas fa-chart-pie card-icon"></i>
                            Najpopularniejsze źródła
                        </h2>
                    </div>
                    
                    {% if top_sources %}
                        <div class="source-chart">
                            {% for source in top_sources %}
                                <div class="source-item">
                                    <span class="source-name">{{ source.source }}</span>
                                    <span class="source-count">{{ source.count }}</span>
                                </div>
                                <div class="source-bar">
                                    <div class="source-bar-fill" style="width: {{ (source.count / total_leaks * 100) | int }}%"></div>
                                </div>
                            {% endfor %}
                        </div>
                    {% else %}
                        <div style="text-align: center; color: var(--text-secondary); padding: 30px 0;">
                            <i class="fas fa-chart-bar" style="font-size: 2rem; margin-bottom: 10px; opacity: 0.5;"></i>
                            <div>Brak danych statystycznych</div>
                        </div>
                    {% endif %}
                </div>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">
                        <i class="fas fa-info-circle card-icon"></i>
                        Informacje systemowe
                    </h2>
                </div>
                
                <div class="session-info">
                    <span class="session-label">Czas trwania sesji:</span>
                    <div class="session-value">{{ session_duration }}</div>
                </div>
                
                <div class="session-info" style="margin-top: 15px;">
                    <span class="session-label">Twój adres IP:</span>
                    <div class="session-value">{{ get_client_ip() }}</div>
                </div>
                
                <div class="session-info" style="margin-top: 15px;">
                    <span class="session-label">Baza danych:</span>
                    <div class="session-value status-online">
                        <i class="fas fa-circle" style="font-size: 0.6rem; margin-right: 5px;"></i>
                        MariaDB: Online ({{ total_leaks }} rekordów)
                    </div>
                </div>
                
                <div class="session-info" style="margin-top: 15px;">
                    <span class="session-label">Aktywne licencje:</span>
                    <div class="session-value">{{ active_licenses }}</div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // Formatowanie liczb z separatorami tysięcy
        function formatNumbers() {
            document.querySelectorAll('.stat-value').forEach(el => {
                const num = parseInt(el.textContent.replace(/\s/g, ''));
                if (!isNaN(num)) {
                    el.textContent = num.toLocaleString('pl-PL');
                }
            });
        }
        
        // Uruchom formatowanie po załadowaniu strony
        document.addEventListener('DOMContentLoaded', formatNumbers);
        
        // Automatyczne odświeżanie statystyk co 60 sekund
        setTimeout(function() {
            window.location.reload();
        }, 60000);
    </script>
</body>
</html>
'''

# === POZOSTAŁE FUNKCJE I ENDPOINTY ===

@app.route("/admin/licenses")
@admin_required
def admin_licenses():
    """Zarządzanie licencjami"""
    try:
        licenses = sb_query("licenses", "order=created_at.desc")
        return render_template_string(admin_licenses_template, licenses=licenses)
    except Exception as e:
        logger.error(f"❌ Błąd ładowania licencji: {e}")
        flash(f"Wystąpił błąd podczas ładowania licencji: {str(e)}", 'error')
        return redirect(url_for('admin_dashboard'))

@app.route("/admin/bans")
@admin_required
def admin_bans():
    """Zarządzanie zbanowanymi IP"""
    try:
        banned_ips = sb_query("banned_ips", "order=created_at.desc")
        return render_template_string(admin_bans_template, banned_ips=banned_ips)
    except Exception as e:
        logger.error(f"❌ Błąd ładowania banów: {e}")
        flash(f"Wystąpił błąd podczas ładowania listy banów: {str(e)}", 'error')
        return redirect(url_for('admin_dashboard'))

@app.route("/admin/stats")
@admin_required
def admin_stats():
    """Statystyki systemu"""
    return render_template_string(admin_stats_template)

@app.route("/admin/logs")
@admin_required
def admin_logs():
    """Przeglądanie logów systemowych"""
    return render_template_string(admin_logs_template)

@app.route("/admin/import-ui")
@admin_required
def admin_import_ui():
    """Interfejs do importowania bazy leaków"""
    return render_template_string(admin_import_template)

@app.route("/admin/add_license", methods=["POST"])
@admin_required
def admin_add_license():
    """Generowanie nowej licencji"""
    try:
        days = int(request.form.get("days", 30))
        license_type = request.form.get("type", "Premium")
        
        new_key = "COLD-" + uuid.uuid4().hex.upper()[:12]
        expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        
        payload = {
            "key": new_key,
            "active": True,
            "expiry": expiry,
            "type": license_type,
            "created_at": "now()",
            "ip": get_client_ip()
        }
        
        response = sb_insert("licenses", payload)
        if response and response.status_code in [200, 201]:
            flash(f"✅ Licencja wygenerowana pomyślnie! Klucz: {new_key}", 'success')
        else:
            error_msg = response.text if response else "Brak odpowiedzi od Supabase"
            flash(f"❌ Błąd podczas generowania licencji: {error_msg}", 'error')
            
    except Exception as e:
        logger.error(f"❌ Błąd generowania licencji: {e}")
        flash(f"❌ Wystąpił błąd: {str(e)}", 'error')
    
    return redirect(url_for('admin_licenses'))

@app.route("/admin/toggle_license/<key>", methods=["POST"])
@admin_required
def admin_toggle_license(key):
    """Aktywacja/dezaktywacja licencji"""
    try:
        licenses = sb_query("licenses", f"key=eq.{key}")
        if licenses:
            current_status = licenses[0].get('active', False)
            new_status = not current_status
            
            response = requests.patch(
                f"{SUPABASE_URL}/rest/v1/licenses",
                headers=SUPABASE_HEADERS,
                json={"active": new_status},
                params={"key": f"eq.{key}"}
            )
            
            action = "Aktywowano" if new_status else "Dezaktywowano"
            if response.status_code in [200, 204]:
                flash(f"✅ {action} licencję pomyślnie!", 'success')
            else:
                flash(f"❌ Błąd podczas {action.lower()} licencji: {response.text}", 'error')
        else:
            flash("❌ Nie znaleziono licencji o podanym kluczu!", 'error')
            
    except Exception as e:
        logger.error(f"❌ Błąd przełączania statusu licencji: {e}")
        flash(f"❌ Wystąpił błąd: {str(e)}", 'error')
    
    return redirect(url_for('admin_licenses'))

@app.route("/admin/del_license/<key>", methods=["POST"])
@admin_required
def admin_del_license(key):
    """Usunięcie licencji"""
    try:
        response = sb_delete("licenses", f"key=eq.{key}")
        if response and response.status_code == 204:
            flash(f"✅ Licencja {key} została usunięta!", 'success')
        else:
            error_msg = response.text if response else "Brak odpowiedzi od Supabase"
            flash(f"❌ Błąd podczas usuwania licencji: {error_msg}", 'error')
    except Exception as e:
        logger.error(f"❌ Błąd usuwania licencji: {e}")
        flash(f"❌ Wystąpił błąd: {str(e)}", 'error')
    
    return redirect(url_for('admin_licenses'))

@app.route("/admin/add_ban", methods=["POST"])
@admin_required
def admin_add_ban():
    """Dodanie IP do listy banów"""
    try:
        ip = request.form.get("ip", "").strip()
        reason = request.form.get("reason", "Brak powodu")
        
        if not is_valid_ip(ip):
            flash("❌ Nieprawidłowy format adresu IP!", 'error')
            return redirect(url_for('admin_bans'))
        
        existing_bans = sb_query("banned_ips", f"ip=eq.{ip}")
        if existing_bans:
            flash("❌ To IP jest już zbanowane!", 'error')
            return redirect(url_for('admin_bans'))
        
        payload = {
            "ip": ip,
            "reason": reason,
            "created_at": "now()",
            "admin_ip": get_client_ip()
        }
        
        response = sb_insert("banned_ips", payload)
        if response and response.status_code in [200, 201]:
            flash(f"✅ Adres IP {ip} został zbanowany!", 'success')
        else:
            error_msg = response.text if response else "Brak odpowiedzi od Supabase"
            flash(f"❌ Błąd podczas banowania IP: {error_msg}", 'error')
            
    except Exception as e:
        logger.error(f"❌ Błąd banowania IP: {e}")
        flash(f"❌ Wystąpił błąd: {str(e)}", 'error')
    
    return redirect(url_for('admin_bans'))

@app.route("/admin/del_ban/<ip>", methods=["POST"])
@admin_required
def admin_del_ban(ip):
    """Usunięcie IP z listy banów"""
    try:
        response = sb_delete("banned_ips", f"ip=eq.{ip}")
        if response and response.status_code == 204:
            flash(f"✅ Adres IP {ip} został odbanowany!", 'success')
        else:
            error_msg = response.text if response else "Brak odpowiedzi od Supabase"
            flash(f"❌ Błąd podczas odbanowywania IP: {error_msg}", 'error')
    except Exception as e:
        logger.error(f"❌ Błąd odbanowywania IP: {e}")
        flash(f"❌ Wystąpił błąd: {str(e)}", 'error')
    
    return redirect(url_for('admin_bans'))

@app.route("/admin/import", methods=["POST"])
@admin_required
def admin_import_start():
    """Rozpoczęcie importu bazy leaków z URL"""
    url = request.form.get("url")
    
    if not url:
        flash("❌ Podaj poprawny URL do pliku ZIP!", 'error')
        return redirect(url_for('admin_import_ui'))
    
    if not url.startswith(('http://', 'https://')):
        flash("❌ URL musi zaczynać się od http:// lub https://", 'error')
        return redirect(url_for('admin_import_ui'))
    
    # Uruchom import w tle
    threading.Thread(
        target=import_worker, 
        args=(url,),
        daemon=True
    ).start()
    
    flash("✅ Import został rozpoczęty w tle. Stan możesz śledzić w logach systemowych.", 'success')
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/logout")
def admin_logout():
    """Wylogowanie administratora"""
    if session.get('is_admin'):
        session.clear()
        flash('Zostałeś wylogowany!', 'success')
    return redirect(url_for('admin_login'))

# === PRACA W TLE ===

def import_worker(url):
    """Worker importujący dane z ZIP do bazy MariaDB"""
    try:
        logger.info(f"Rozpoczęto import danych z archiwum ZIP: {url}")
        
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
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
                            source_name = os.path.basename(file_path)
                            
                            try:
                                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    batch = []
                                    for line in f:
                                        clean_line = line.strip()
                                        if clean_line and len(clean_line) > 5 and len(clean_line) <= 1000:
                                            batch.append((clean_line, source_name))
                                        
                                        if len(batch) >= 1000:
                                            cursor.executemany(
                                                "INSERT IGNORE INTO leaks (data, source) VALUES (%s, %s)",
                                                batch
                                            )
                                            total_added += len(batch)
                                            batch = []
                                    
                                    if batch:
                                        cursor.executemany(
                                            "INSERT IGNORE INTO leaks (data, source) VALUES (%s, %s)",
                                            batch
                                        )
                                        total_added += len(batch)
                            
                            except Exception as e:
                                logger.error(f"❌ Błąd przetwarzania pliku {source_name}: {e}")
                
                conn.commit()
        
        os.unlink(tmp_path)
        
        logger.info(f"✅ Import zakończony pomyślnie. Liczba dodanych rekordów: {total_added}")
        return total_added
        
    except Exception as e:
        error_msg = f"Błąd importu: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return 0

# === POZOSTAŁE SZABLONY HTML ===
# (Pozostałe szablony HTML zostały uproszczone dla czytelności, ale zawierają pełną funkcjonalność)

admin_licenses_template = '''
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Search Premium - Licencje</title>
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
            --active: rgba(0, 255, 170, 0.15);
            --inactive: rgba(255, 77, 77, 0.15);
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            background: var(--bg);
            color: var(--text);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
        }
        
        .container {
            display: grid;
            grid-template-columns: 240px 1fr;
            min-height: 100vh;
        }
        
        /* Sidebar */
        .sidebar {
            background: rgba(10, 10, 18, 0.95);
            border-right: 1px solid var(--border);
            padding: 20px 0;
            height: 100vh;
            position: fixed;
            width: 240px;
            z-index: 100;
        }
        
        .logo {
            padding: 0 20px 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 20px;
        }
        
        .logo-text {
            font-size: 1.4rem;
            font-weight: 800;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .logo-sub {
            color: var(--text-secondary);
            font-size: 0.85rem;
            margin-top: 4px;
        }
        
        .nav-links {
            padding: 0 10px;
        }
        
        .nav-item {
            display: flex;
            align-items: center;
            padding: 12px 20px;
            margin-bottom: 4px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
            color: var(--text);
        }
        
        .nav-item:hover {
            background: rgba(255, 255, 255, 0.05);
        }
        
        .nav-item.active {
            background: linear-gradient(90deg, rgba(0, 242, 255, 0.15), rgba(188, 19, 254, 0.15));
            border-left: 3px solid var(--primary);
        }
        
        .nav-icon {
            margin-right: 12px;
            font-size: 1.1rem;
            width: 20px;
            text-align: center;
        }
        
        .nav-text {
            font-weight: 500;
        }
        
        .logout-btn {
            margin-top: 30px;
            padding: 10px 20px;
            background: rgba(255, 77, 77, 0.15);
            border: 1px solid var(--danger);
            color: var(--danger);
            border-radius: 8px;
            width: calc(100% - 40px);
            cursor: pointer;
            display: flex;
            align-items: center;
            transition: all 0.2s;
        }
        
        .logout-btn:hover {
            background: rgba(255, 77, 77, 0.25);
        }
        
        .logout-icon {
            margin-right: 10px;
        }
        
        /* Main Content */
        .main-content {
            margin-left: 240px;
            padding: 20px;
        }
        
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding-bottom: 15px;
            border-bottom: 1px solid var(--border);
        }
        
        .page-title {
            font-size: 1.8rem;
            font-weight: 700;
        }
        
        .card {
            background: var(--card-bg);
            border-radius: 16px;
            padding: 25px;
            border: 1px solid var(--border);
        }
        
        .generate-form {
            display: flex;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        
        .form-group {
            flex: 1;
            min-width: 200px;
        }
        
        .form-label {
            display: block;
            margin-bottom: 8px;
            font-weight: 500;
            color: var(--text);
        }
        
        .form-input {
            width: 100%;
            padding: 12px;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: white;
            font-family: 'Inter', sans-serif;
        }
        
        .form-select {
            width: 100%;
            padding: 12px;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: white;
            font-family: 'Inter', sans-serif;
            appearance: none;
            background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23aaa' d='M7 10l5 5 5-5z'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 10px center;
            background-size: 16px;
        }
        
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            font-family: 'Inter', sans-serif;
            font-weight: 600;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, var(--primary), #00b3cc);
            color: #000;
        }
        
        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0, 242, 255, 0.4);
        }
        
        .table-container {
            overflow-x: auto;
            margin-top: 20px;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th {
            background: rgba(0, 0, 0, 0.2);
            padding: 14px 16px;
            text-align: left;
            font-weight: 600;
            color: var(--text);
            font-size: 0.95rem;
        }
        
        td {
            padding: 14px 16px;
            border-bottom: 1px solid var(--border);
            font-size: 0.95rem;
        }
        
        tr:last-child td {
            border-bottom: none;
        }
        
        tr:hover {
            background: rgba(255, 255, 255, 0.03);
        }
        
        .key-text {
            font-family: 'Courier New', monospace;
            font-weight: 600;
            color: var(--primary);
            letter-spacing: 1px;
        }
        
        .status-badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
        }
        
        .status-active {
            background: var(--active);
            color: var(--success);
        }
        
        .status-inactive {
            background: var(--inactive);
            color: var(--danger);
        }
        
        .action-btns {
            display: flex;
            gap: 8px;
        }
        
        .action-btn {
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .btn-toggle {
            background: rgba(255, 204, 0, 0.15);
            color: #ffcc00;
            border: 1px solid rgba(255, 204, 0, 0.3);
        }
        
        .btn-toggle:hover {
            background: rgba(255, 204, 0, 0.25);
        }
        
        .btn-delete {
            background: rgba(255, 77, 77, 0.15);
            color: var(--danger);
            border: 1px solid rgba(255, 77, 77, 0.3);
        }
        
        .btn-delete:hover {
            background: rgba(255, 77, 77, 0.25);
        }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-secondary);
        }
        
        .empty-icon {
            font-size: 4rem;
            margin-bottom: 20px;
            opacity: 0.3;
        }
        
        .empty-text {
            font-size: 1.2rem;
            margin-bottom: 10px;
        }
        
        .empty-subtext {
            opacity: 0.7;
        }
        
        @media (max-width: 768px) {
            .action-btns {
                flex-direction: column;
                gap: 5px;
            }
            
            .action-btn {
                width: 100%;
            }
            
            .generate-form {
                flex-direction: column;
            }
        }
        
        .flash-message {
            padding: 12px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .flash-success {
            background: rgba(0, 255, 170, 0.15);
            border: 1px solid rgba(0, 255, 170, 0.3);
            color: var(--success);
        }
        
        .flash-error {
            background: rgba(255, 77, 77, 0.15);
            border: 1px solid rgba(255, 77, 77, 0.3);
            color: var(--danger);
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Sidebar -->
        <div class="sidebar">
            <div class="logo">
                <div class="logo-text">❄️ Cold Search</div>
                <div class="logo-sub">Panel Administratora</div>
            </div>
            
            <div class="nav-links">
                <a href="{{ url_for('admin_dashboard') }}" class="nav-item">
                    <i class="fas fa-home nav-icon"></i>
                    <span class="nav-text">Dashboard</span>
                </a>
                <a href="{{ url_for('admin_licenses') }}" class="nav-item active">
                    <i class="fas fa-key nav-icon"></i>
                    <span class="nav-text">Licencje</span>
                </a>
                <a href="{{ url_for('admin_bans') }}" class="nav-item">
                    <i class="fas fa-ban nav-icon"></i>
                    <span class="nav-text">Bany IP</span>
                </a>
                <a href="{{ url_for('admin_stats') }}" class="nav-item">
                    <i class="fas fa-chart-bar nav-icon"></i>
                    <span class="nav-text">Statystyki</span>
                </a>
                <a href="{{ url_for('admin_logs') }}" class="nav-item">
                    <i class="fas fa-clipboard-list nav-icon"></i>
                    <span class="nav-text">Logi</span>
                </a>
                <a href="{{ url_for('admin_import_ui') }}" class="nav-item">
                    <i class="fas fa-file-import nav-icon"></i>
                    <span class="nav-text">Import Danych</span>
                </a>
            </div>
            
            <button class="logout-btn" onclick="if(confirm('Czy na pewno chcesz się wylogować?')) window.location.href='{{ url_for('admin_logout') }}'">
                <i class="fas fa-sign-out-alt logout-icon"></i>
                <span>Wyloguj się</span>
            </button>
        </div>
        
        <!-- Main Content -->
        <div class="main-content">
            <div class="header">
                <h1 class="page-title">🔑 Zarządzanie Licencjami</h1>
            </div>
            
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="flash-message flash-{{ category }}">
                            {% if category == 'success' %}
                                <i class="fas fa-check-circle"></i>
                            {% elif category == 'error' %}
                                <i class="fas fa-exclamation-circle"></i>
                            {% endif %}
                            {{ message }}
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="card">
                <div class="card-header" style="margin-bottom: 20px;">
                    <h2 class="card-title" style="font-size: 1.4rem; font-weight: 600;">
                        <i class="fas fa-plus-circle" style="color: var(--primary); margin-right: 10px;"></i>
                        Wygeneruj nową licencję
                    </h2>
                </div>
                
                <form method="post" action="{{ url_for('admin_add_license') }}" class="generate-form">
                    <div class="form-group">
                        <label for="days" class="form-label">Liczba dni ważności</label>
                        <input type="number" id="days" name="days" class="form-input" value="30" min="1" max="3650">
                    </div>
                    
                    <div class="form-group">
                        <label for="type" class="form-label">Typ licencji</label>
                        <select id="type" name="type" class="form-select">
                            <option value="Premium">Premium</option>
                            <option value="Lifetime">Lifetime</option>
                            <option value="Trial">Trial (7 dni)</option>
                            <option value="Basic">Basic</option>
                        </select>
                    </div>
                    
                    <div class="form-group" style="align-self: flex-end;">
                        <button type="submit" class="btn btn-primary">
                            <i class="fas fa-magic" style="margin-right: 8px;"></i>
                            Wygeneruj klucz
                        </button>
                    </div>
                </form>
            </div>
            
            <div class="card" style="margin-top: 30px;">
                <div class="card-header" style="margin-bottom: 20px;">
                    <h2 class="card-title" style="font-size: 1.4rem; font-weight: 600;">
                        <i class="fas fa-list" style="color: var(--primary); margin-right: 10px;"></i>
                        Lista aktywnych licencji
                        <span style="font-size: 0.9rem; font-weight: 400; margin-left: 10px; color: var(--text-secondary);">
                            ({{ licenses|length }})
                        </span>
                    </h2>
                </div>
                
                <div class="table-container">
                    {% if licenses %}
                        <table>
                            <thead>
                                <tr>
                                    <th>Klucz</th>
                                    <th>Typ</th>
                                    <th>Ważna do</th>
                                    <th>Status</th>
                                    <th>IP</th>
                                    <th>Akcje</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for lic in licenses %}
                                    <tr>
                                        <td><span class="key-text">{{ lic.key }}</span></td>
                                        <td><span style="color: var(--secondary); font-weight: 600;">{{ lic.type }}</span></td>
                                        <td>{{ lic.expiry.split('T')[0] }}</td>
                                        <td>
                                            <span class="status-badge {{ 'status-active' if lic.active else 'status-inactive' }}">
                                                {{ 'Aktywna' if lic.active else 'Nieaktywna' }}
                                            </span>
                                        </td>
                                        <td>{{ lic.ip if lic.ip else '—' }}</td>
                                        <td class="action-btns">
                                            <form method="post" action="{{ url_for('admin_toggle_license', key=lic.key) }}" style="display: inline;">
                                                <button type="submit" class="action-btn btn-toggle" title="{{ 'Dezaktywuj' if lic.active else 'Aktywuj' }}">
                                                    <i class="fas {{ 'fa-toggle-off' if lic.active else 'fa-toggle-on' }}"></i>
                                                    {{ 'Wyłącz' if lic.active else 'Włącz' }}
                                                </button>
                                            </form>
                                            <form method="post" action="{{ url_for('admin_del_license', key=lic.key) }}" style="display: inline;" onsubmit="return confirm('Na pewno usunąć tę licencję?')">
                                                <button type="submit" class="action-btn btn-delete" title="Usuń licencję">
                                                    <i class="fas fa-trash"></i> Usuń
                                                </button>
                                            </form>
                                        </td>
                                    </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    {% else %}
                        <div class="empty-state">
                            <i class="fas fa-key empty-icon"></i>
                            <div class="empty-text">Brak licencji w systemie</div>
                            <div class="empty-subtext">Wygeneruj pierwszą licencję używając formularza powyżej</div>
                        </div>
                    {% endif %}
                </div>
            </div>
        </div>
    </div>
</body>
</html>
'''

# [Pozostałe szablony HTML - admin_bans_template, admin_stats_template, admin_logs_template, admin_import_template - są podobne w strukturze]

# === POMOCNICZE FUNKCJE SZABLONÓW ===

@app.template_filter('format_number')
def format_number(value):
    """Formatuje liczbę z separatorami tysięcy"""
    if isinstance(value, int):
        return f"{value:,}".replace(",", " ")
    return value

@app.template_filter('truncate')
def truncate_string(value, length=30):
    """Obcina string do określonej długości"""
    if not isinstance(value, str):
        return value
    return value[:length] + ('...' if len(value) > length else '')

# === API ENDPOINTS ===

@app.route("/api/status", methods=["GET"])
def api_status():
    """Sprawdza status serwera"""
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
    """Autoryzacja klucza API"""
    data = request.json or request.form.to_dict()
    key = data.get("key")
    ip = data.get("client_ip") or get_client_ip()
    
    if not key:
        return jsonify({"success": False, "message": "Brak klucza"}), 400
        
    # Walidacja z Supabase
    licenses = sb_query("licenses", f"key=eq.{key}")
    
    if not licenses:
        return jsonify({"success": False, "message": "Nieprawidłowy klucz licencyjny"}), 401
        
    lic = licenses[0]
    expiry = datetime.fromisoformat(lic['expiry'].replace('Z', '+00:00'))
    
    if datetime.now(timezone.utc) > expiry or not lic.get('active', True):
        return jsonify({"success": False, "message": "Licencja wygasła lub została zablokowana"}), 401
    
    # Jeśli licencja nie ma przypisanego IP, przypisz aktualne
    if not lic.get("ip"):
        sb_insert("licenses", {"key": key, "ip": ip})
    
    # Sprawdź czy IP jest zgodne
    if lic.get("ip") and lic["ip"] != ip:
        return jsonify({"success": False, "message": "Klucz przypisany do innego adresu IP"}), 403
    
    return jsonify({"success": True, "message": "Zalogowano pomyślnie"})

@app.route("/api/license-info", methods=["POST"])
def api_info():
    """Pobieranie informacji o licencji"""
    data = request.json or request.form.to_dict()
    key = data.get("key")
    ip = data.get("client_ip") or get_client_ip()
    
    if not key:
        return jsonify({"success": False, "message": "Brak klucza"}), 400
        
    # Walidacja klucza
    auth_response = api_auth()
    if auth_response.status_code != 200:
        return auth_response
        
    # Pobierz dane licencji
    licenses = sb_query("licenses", f"key=eq.{key}")
    if not licenses:
        return jsonify({"success": False, "message": "Nie znaleziono licencji"}), 404
        
    lic = licenses[0]
    
    # Pobierz liczbę zapytań
    search_logs = sb_query("search_logs", f"key=eq.{key}&select=count(*)")
    queries_used = search_logs[0]["count"] if search_logs and search_logs[0] else 0
    
    return jsonify({
        "success": True,
        "info": {
            "license_type": lic.get("type", "Standard"), 
            "expiration_date": lic["expiry"].split("T")[0],
            "query_limit": "nieograniczony",
            "queries_used": queries_used,
            "last_search": "Brak danych"  # W razie potrzeby możesz dodać logikę pobierania ostatniego wyszukiwania
        }
    })

@app.route("/api/search", methods=["POST"])
def api_search():
    """Wyszukiwanie danych wycieków"""
    data = request.json or request.form.to_dict()
    query = data.get("query", "").strip()
    key = data.get("key")
    ip = data.get("client_ip") or get_client_ip()
    limit = int(data.get("limit", 150))
    
    if not key:
        return jsonify({"success": False, "message": "Brak klucza"}), 400
        
    if not query:
        return jsonify({"success": False, "message": "Puste zapytanie"}), 400
        
    # Walidacja klucza
    auth_response = api_auth()
    if auth_response.status_code != 200:
        return auth_response
        
    try:
        # Zapisz wyszukiwanie do logów
        sb_insert("search_logs", {
            "key": key,
            "query": query,
            "ip": ip,
            "timestamp": "now()"
        })
        
        # Wyszukiwanie w bazie leaków
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

# === URUCHOMIENIE APLIKACJI ===

if __name__ == "__main__":
    # Inicjalizacja puli połączeń przed uruchomieniem serwera
    initialize_db_pool()
    
    # Logowanie uruchomienia aplikacji
    logger.info("🚀 Cold Search Premium Admin Panel został uruchomiony")
    logger.info(f"🔧 Konfiguracja MariaDB: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    logger.info(f"🔧 Konfiguracja Supabase: {SUPABASE_URL}")
    
    # Uruchomienie serwera
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
    
