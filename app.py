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
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

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
def log_activity(action, details=None):
    """Rejestruje aktywność administratora i wysyła do Discorda jeśli skonfigurowano"""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Administrator ({get_client_ip()}) - {action}"
    if details:
        log_entry += f" | {details}"
    
    logger.info(log_entry)
    
    if DISCORD_WEBHOOK_URL:
        threading.Thread(target=send_discord_notification, args=(action, details), daemon=True).start()

def send_discord_notification(action, details=None):
    """Wysyła powiadomienie do Discorda o aktywności administratora"""
    try:
        if not DISCORD_WEBHOOK_URL.startswith("https://discord.com/api/webhooks/"):
            return
            
        embed = {
            "title": "👮 Aktywność Administratora",
            "color": 3066993,
            "fields": [
                {"name": "🔧 Akcja", "value": action, "inline": False},
                {"name": "🌐 IP Administratora", "value": get_client_ip(), "inline": True},
                {"name": "🕒 Czas", "value": datetime.now().strftime("%H:%M:%S"), "inline": True}
            ],
            "footer": {"text": "Cold Search Premium Admin Panel"}
        }
        
        if details:
            embed["fields"].append({"name": "📋 Szczegóły", "value": str(details)[:1024], "inline": False})
        
        payload = {
            "username": "Cold Search Admin Monitor",
            "avatar_url": "https://i.imgur.com/ZXj3PcP.png",
            "embeds": [embed]
        }
        
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"❌ Błąd wysyłania powiadomienia do Discorda: {e}")

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
            log_activity("Zalogowanie do panelu", f"IP: {get_client_ip()}")
            flash('Zalogowano pomyślnie!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            log_activity("Nieudana próba logowania", f"IP: {get_client_ip()}")
            flash('Nieprawidłowe hasło!', 'error')
    
    return render_template_string('''
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
    ''')

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

        # Statystyki z Supabase
        licenses = sb_query("licenses", "order=created_at.desc")
        active_licenses = len([lic for lic in licenses if lic.get('active', False)])
        
        banned_ips = sb_query("banned_ips")
        
        # Liczba zapytań z Supabase (jeśli tabela istnieje)
        search_logs = sb_query("search_logs", "select=count(*)")
        total_searches = search_logs[0].get('count', 0) if search_logs else 0
        
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
            session_duration=str(session_duration).split('.')[0],
            get_client_ip=get_client_ip  # Przekazujemy funkcję do szablonu
        )
    except Exception as e:
        logger.error(f"❌ Błąd ładowania dashboardu: {e}")
        flash(f"Wystąpił błąd podczas ładowania danych: {str(e)}", 'error')
        return redirect(url_for('admin_login'))

# [Pozostała część kodu pozostaje bez zmian, ale dla pełnej funkcjonalności dodaję pozostałe endpointy]

@app.route("/admin/licenses")
@admin_required
def admin_licenses():
    """Zarządzanie licencjami"""
    try:
        licenses = sb_query("licenses", "order=created_at.desc")
        return render_template_string(
            admin_licenses_template,
            licenses=licenses
        )
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
        return render_template_string(
            admin_bans_template,
            banned_ips=banned_ips
        )
    except Exception as e:
        logger.error(f"❌ Błąd ładowania banów: {e}")
        flash(f"Wystąpił błąd podczas ładowania listy banów: {str(e)}", 'error')
        return redirect(url_for('admin_dashboard'))

@app.route("/admin/logs")
@admin_required
def admin_logs():
    """Przeglądanie logów systemowych"""
    try:
        # Ostatnie 50 logów z Supabase
        logs = sb_query("search_logs", "order=timestamp.desc&limit=50")
        return render_template_string(
            admin_logs_template,
            logs=logs
        )
    except Exception as e:
        logger.error(f"❌ Błąd ładowania logów: {e}")
        flash(f"Wystąpił błąd podczas ładowania logów: {str(e)}", 'error')
        return redirect(url_for('admin_dashboard'))

@app.route("/admin/import-ui")
@admin_required
def admin_import_ui():
    """Interfejs do importowania bazy leaków"""
    return render_template_string(admin_import_template)

# [Pozostałe akcje admina - add_license, toggle_license, del_license, add_ban, del_ban, import itp. pozostają bez zmian]

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
            log_activity("Wygenerowano nową licencję", f"Klucz: {new_key}, dni: {days}, typ: {license_type}")
            flash(f"✅ Licencja wygenerowana pomyślnie! Klucz: {new_key}", 'success')
        else:
            error_msg = response.text if response else "Brak odpowiedzi od Supabase"
            log_activity("Błąd generowania licencji", error_msg)
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
        # Pobierz aktualny status licencji
        licenses = sb_query("licenses", f"key=eq.{key}")
        if licenses:
            current_status = licenses[0].get('active', False)
            new_status = not current_status
            
            # Zaktualizuj status
            response = requests.patch(
                f"{SUPABASE_URL}/rest/v1/licenses",
                headers=SUPABASE_HEADERS,
                json={"active": new_status},
                params={"key": f"eq.{key}"}
            )
            
            action = "Aktywowano" if new_status else "Dezaktywowano"
            if response.status_code in [200, 204]:
                log_activity(f"{action} licencję", f"Klucz: {key}, nowy status: {new_status}")
                flash(f"✅ {action} licencję pomyślnie!", 'success')
            else:
                log_activity(f"Błąd {action.lower()} licencji", response.text)
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
            log_activity("Usunięto licencję", f"Klucz: {key}")
            flash(f"✅ Licencja {key} została usunięta!", 'success')
        else:
            error_msg = response.text if response else "Brak odpowiedzi od Supabase"
            log_activity("Błąd usuwania licencji", error_msg)
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
        
        # Sprawdź, czy IP nie jest już zbanowane
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
            log_activity("Zbanowano adres IP", f"IP: {ip}, powód: {reason}")
            flash(f"✅ Adres IP {ip} został zbanowany!", 'success')
        else:
            error_msg = response.text if response else "Brak odpowiedzi od Supabase"
            log_activity("Błąd banowania IP", error_msg)
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
            log_activity("Odbanowano adres IP", f"IP: {ip}")
            flash(f"✅ Adres IP {ip} został odbanowany!", 'success')
        else:
            error_msg = response.text if response else "Brak odpowiedzi od Supabase"
            log_activity("Błąd odbanowywania IP", error_msg)
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
    
    log_activity("Rozpoczęto import bazy leaków", f"URL: {url}")
    flash("✅ Import został rozpoczęty w tle. Stan możesz śledzić w logach systemowych.", 'success')
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/logout")
def admin_logout():
    """Wylogowanie administratora"""
    if session.get('is_admin'):
        log_activity("Wylogowanie z panelu", f"IP: {get_client_ip()}")
        session.clear()
        flash('Zostałeś wylogowany!', 'success')
    return redirect(url_for('admin_login'))

# === PRACA W TLE ===

def import_worker(url):
    """Worker importujący dane z ZIP do bazy MariaDB"""
    try:
        log_activity("Rozpoczęto import danych z archiwum ZIP", f"URL: {url}")
        
        # Pobierz plik ZIP
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        
        # Utwórz plik tymczasowy
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    tmp_file.write(chunk)
            tmp_path = tmp_file.name
        
        # Wyciągnij i przetwórz dane
        total_added = 0
        with tempfile.TemporaryDirectory() as tmp_dir:
            with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                zip_ref.extractall(tmp_dir)
            
            # Połącz się z bazą
            with get_db() as conn:
                cursor = conn.cursor()
                
                # Przetwórz każdy plik
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
                                    
                                    # Wstaw pozostałe rekordy
                                    if batch:
                                        cursor.executemany(
                                            "INSERT IGNORE INTO leaks (data, source) VALUES (%s, %s)",
                                            batch
                                        )
                                        total_added += len(batch)
                            
                            except Exception as e:
                                logger.error(f"❌ Błąd przetwarzania pliku {source_name}: {e}")
                                log_activity("Błąd przetwarzania pliku podczas importu", f"Plik: {source_name}, błąd: {str(e)}")
                
                conn.commit()
        
        # Usuń plik tymczasowy
        os.unlink(tmp_path)
        
        log_activity("Import zakończony pomyślnie", f"Liczba dodanych rekordów: {total_added}")
        return total_added
        
    except Exception as e:
        error_msg = f"Błąd importu: {str(e)}"
        logger.error(f"❌ {error_msg}")
        log_activity("Błąd krytyczny podczas importu danych", error_msg)
        return 0

# === SZABLONY HTML ===
# [Szablony HTML pozostają bez zmian, ale z poprawionym błędem 'get_ip' is undefined]

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
                        Aktywna sesja
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
                    <div class="stat-footer">Z ostatniego importu</div>
                </div>
                
                <div class="stat-card">
                    <i class="fas fa-file-alt stat-icon"></i>
                    <div class="stat-title">PLIKI ŹRÓDŁOWE</div>
                    <div class="stat-value">{{ "{:,}".format(source_count).replace(",", " ") }}</div>
                    <div class="stat-footer">Unikalne źródła danych</div>
                </div>
                
                <div class="stat-card">
                    <i class="fas fa-key stat-icon"></i>
                    <div class="stat-title">AKTYWNE LICENCJE</div>
                    <div class="stat-value">{{ active_licenses }}</div>
                    <div class="stat-footer">Wszystkie typy</div>
                </div>
                
                <div class="stat-card">
                    <i class="fas fa-search stat-icon"></i>
                    <div class="stat-title">WYSZUKAŃ OGÓŁEM</div>
                    <div class="stat-value">{{ "{:,}".format(total_searches).replace(",", " ") }}</div>
                    <div class="stat-footer">Wszystkie zapytania</div>
                </div>
            </div>
            
            <div class="content-grid">
                <div class="card">
                    <div class="card-header">
                        <h2 class="card-title">
                            <i class="fas fa-history card-icon"></i>
                            Ostatnie dane
                        </h2>
                    </div>
                    
                    {% if recent_leaks %}
                        {% for leak in recent_leaks %}
                            <div class="recent-leak">
                                <div class="leak-data">{{ leak.data | truncate(60) }}</div>
                                <div class="leak-meta">
                                    <span class="leak-source">{{ leak.source }}</span>
                                    <span>{{ (leak.created_at).split('T')[0] }}</span>
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
                            <i class="fas fa-shield-alt card-icon"></i>
                            Informacje sesji
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
                        <span class="session-label">Serwer:</span>
                        <div class="session-value">{{ request.host }}</div>
                    </div>
                    
                    <div class="session-info" style="margin-top: 15px;">
                        <span class="session-label">Status bazy danych:</span>
                        <div class="session-value status-online">
                            <i class="fas fa-circle" style="font-size: 0.6rem; margin-right: 5px;"></i>
                            MariaDB: Online
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // Automatyczne odświeżanie statystyk co 30 sekund
        setTimeout(function() {
            window.location.reload();
        }, 30000);
        
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
    </script>
</body>
</html>
'''

# [Pozostałe szablony HTML bez zmian]

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
    
    # Sprawdź połączenie z bazą na starcie
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            logger.info("✅ Testowe połączenie z bazą danych zakończone pomyślnie.")
            
            # Sprawdź strukturę tabeli leaks
            cursor.execute("DESCRIBE leaks")
            columns = cursor.fetchall()
            logger.info("🔧 Struktura tabeli 'leaks':")
            for column in columns:
                logger.info(f"  • {column[0]} ({column[1]})")
    except Exception as e:
        logger.error(f"❌ Błąd testowego połączenia z bazą: {e}")
    
    # Uruchomienie serwera
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
