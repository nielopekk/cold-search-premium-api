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
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Indjc2h5cG1zdXJuY2Z1ZmJvanZwIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTAzMjQ2OCwiZXhwIjoyMDg0NjA4NDY4fQ.Dqy9y1w7j1u8q5m7Y2lK9V0HfZ1x8N5j9mF6Y9v2Y7I").strip()
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
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_ultra_2026_fixed_secret_random_key_here")

# === AUTOMATYCZNA INICJALIZACJA TABEL ===
def initialize_tables():
    """Tworzy wszystkie potrzebne tabele w bazach danych przy starcie aplikacji"""
    # Inicjalizacja tabel w MariaDB
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Tabela search_logs - naprawiony błąd składni SQL (słowo kluczowe key było zastrzeżone)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            query VARCHAR(255) NOT NULL,
            ip VARCHAR(45) NOT NULL,
            `key` VARCHAR(50) NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_ip (ip),
            INDEX idx_key (`key`),
            INDEX idx_timestamp (timestamp),
            INDEX idx_query (query),
            INDEX idx_key_timestamp (`key`, timestamp)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        # Tabela leaks z unikalnym kluczem
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaks (
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
        """)
        
        # Dodanie przykładowych danych jeśli tabela jest pusta
        cursor.execute("SELECT COUNT(*) as count FROM leaks")
        result = cursor.fetchone()
        if result and result[0] == 0:
            cursor.execute("""
            INSERT INTO leaks (data, source) VALUES
            ('test@example.com', 'test_data'),
            ('admin123', 'test_data'),
            ('user_2024', 'test_data')
            ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
            """)
            logger.info("✅ Dodano przykładowe dane do tabeli 'leaks'")
        
        conn.commit()
        logger.info("✅ Tabele w MariaDB zostały pomyślnie zainicjalizowane")
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Błąd podczas inicjalizacji tabel w MariaDB: {e}")
    
    # Inicjalizacja tabel w Supabase
    try:
        # Sprawdzenie czy tabela licenses istnieje
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses?limit=1", headers=SUPABASE_HEADERS, timeout=10)
        if r.status_code == 404:
            logger.info("🔧 Tworzenie tabel w Supabase...")
            # Tabele są tworzone przez skrypt SQL powyżej
            logger.info("✅ Tabele w Supabase wymagają ręcznej inicjalizacji przez SQL Editor")
    except Exception as e:
        logger.error(f"❌ Błąd podczas inicjalizacji tabel w Supabase: {e}")

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
                initialize_tables()  # Inicjalizacja tabel przy starcie
            return True
        except Exception as e:
            logger.error(f"❌ Błąd połączenia z MariaDB (próba {attempt + 1}): {e}")
            attempt += 1
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    logger.error("❌ Krytyczny błąd: nie udało się połączyć z MariaDB po wielu próbach")
    raise SystemExit("Nie można kontynuować bez połączenia z bazą danych leaków")

@contextlib.contextmanager
def get_db():
    conn = None
    try:
        if db_pool is None:
            initialize_db_pool()
        conn = db_pool.get_connection()
        yield conn
    finally:
        if conn and conn.is_connected():
            conn.close()

# === FUNKCJE POMOCNICZE ===
def sb_query(table, params=""):
    """Bezpieczne zapytanie do Supabase"""
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=SUPABASE_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 404:
            logger.warning(f"⚠️ Tabela '{table}' nie istnieje w Supabase")
            return []
        else:
            logger.error(f"❌ Błąd Supabase ({r.status_code}): {r.text}")
            return []
    except Exception as e:
        logger.error(f"❌ Błąd zapytania do Supabase ({table}): {e}")
        return []

def sb_insert(table, data):
    """Bezpieczne wstawianie do Supabase"""
    try:
        return requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SUPABASE_HEADERS, json=data, timeout=10)
    except Exception as e:
        logger.error(f"❌ Błąd wstawiania do Supabase ({table}): {e}")
        return None

def sb_update(table, data, condition):
    """Bezpieczna aktualizacja w Supabase"""
    try:
        return requests.patch(f"{SUPABASE_URL}/rest/v1/{table}?{condition}", headers=SUPABASE_HEADERS, json=data, timeout=10)
    except Exception as e:
        logger.error(f"❌ Błąd aktualizacji w Supabase ({table}): {e}")
        return None

def sb_delete(table, condition):
    """Bezpieczne usuwanie z Supabase"""
    try:
        return requests.delete(f"{SUPABASE_URL}/rest/v1/{table}?{condition}", headers=SUPABASE_HEADERS, timeout=10)
    except Exception as e:
        logger.error(f"❌ Błąd usuwania z Supabase ({table}): {e}")
        return None

def get_client_ip():
    """Pobranie klienta IP z nagłówków"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'

def is_valid_ip(ip):
    """Walidacja adresu IP"""
    pattern = re.compile(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$')
    return pattern.match(ip) is not None

def format_datetime(dt):
    """Formatowanie daty do czytelnej postaci"""
    if isinstance(dt, datetime):
        return dt.strftime("%d.%m.%Y %H:%M")
    return str(dt)

# === IMPORT WORKER ===
def import_worker(url):
    """Wątek importujący dane z archiwum ZIP"""
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
                                                    "INSERT IGNORE INTO leaks (data, source) VALUES (%s, %s)",
                                                    batch
                                                )
                                                total_added += cursor.rowcount
                                                batch = []
                                    if batch:
                                        cursor.executemany(
                                            "INSERT IGNORE INTO leaks (data, source) VALUES (%s, %s)",
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

# === GŁÓWNY ENDPOINT: / (panel admina) ===
@app.route("/", methods=["GET", "POST"])
def admin_panel():
    """Główny panel administracyjny"""
    if request.method == "POST" and not session.get('is_admin'):
        if request.form.get("password") == ADMIN_PASSWORD:
            session['is_admin'] = True
            session['login_time'] = datetime.now(timezone.utc).isoformat()
            flash('✅ Zalogowano pomyślnie!', 'success')
        else:
            flash('❌ Nieprawidłowe hasło!', 'error')
        return redirect(url_for('admin_panel'))
    
    if not session.get('is_admin'):
        return render_template_string(ADMIN_LOGIN_TEMPLATE)
    
    action = request.form.get("action")
    if action == "add_license":
        try:
            days = int(request.form.get("days", 30))
            daily_limit = int(request.form.get("daily_limit", 100))
            total_limit = int(request.form.get("total_limit", 1000))
            license_type = request.form.get("license_type", "standard")
            
            new_key = "COLD-" + uuid.uuid4().hex.upper()[:12]
            expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            payload = {
                "key": new_key,
                "active": True,
                "expiry": expiry,
                "daily_limit": daily_limit,
                "total_limit": total_limit,
                "license_type": license_type,
                "ip": None,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            r = sb_insert("licenses", payload)
            if r and r.status_code in (200, 201):
                flash(f"✅ Licencja: {new_key} (limit dzienny: {daily_limit}, całkowity: {total_limit})", 'success')
            else:
                flash(f"❌ Błąd generowania licencji: {r.text if r else 'Błąd połączenia'}", 'error')
        except Exception as e:
            logger.error(f"❌ Błąd generowania licencji: {e}")
            flash(f"❌ Błąd generowania licencji: {str(e)}", 'error')
    elif action == "toggle_license":
        try:
            key = request.form.get("key")
            licenses = sb_query("licenses", f"key=eq.{key}")
            if licenses:
                new_status = not licenses[0].get('active', False)
                sb_update("licenses", {"active": new_status}, f"key=eq.{key}")
                flash(f"{'Włączono' if new_status else 'Wyłączono'} licencję", 'success')
        except Exception as e:
            logger.error(f"❌ Błąd przełączania licencji: {e}")
            flash("❌ Błąd przełączania licencji", 'error')
    elif action == "del_license":
        try:
            key = request.form.get("key")
            sb_delete("licenses", f"key=eq.{key}")
            flash("✅ Licencja usunięta", 'success')
        except Exception as e:
            logger.error(f"❌ Błąd usuwania licencji: {e}")
            flash("❌ Błąd usuwania licencji", 'error')
    elif action == "add_ban":
        try:
            ip = request.form.get("ip", "").strip()
            if is_valid_ip(ip):
                existing = sb_query("banned_ips", f"ip=eq.{ip}")
                if not existing:
                    sb_insert("banned_ips", {
                        "ip": ip, 
                        "reason": request.form.get("reason", "—"), 
                        "admin_ip": get_client_ip(),
                        "created_at": datetime.now(timezone.utc).isoformat()
                    })
                    flash(f"✅ Zbanowano IP: {ip}", 'success')
                else:
                    flash("❌ IP już zbanowane", 'error')
            else:
                flash("❌ Nieprawidłowe IP", 'error')
        except Exception as e:
            logger.error(f"❌ Błąd banowania IP: {e}")
            flash("❌ Błąd banowania IP", 'error')
    elif action == "del_ban":
        try:
            ip = request.form.get("ip")
            sb_delete("banned_ips", f"ip=eq.{ip}")
            flash("✅ Odbanowano IP", 'success')
        except Exception as e:
            logger.error(f"❌ Błąd odbanowywania IP: {e}")
            flash("❌ Błąd odbanowywania IP", 'error')
    elif action == "import_start":
        try:
            url = request.form.get("import_url")
            if url and url.startswith(('http://', 'https://')):
                threading.Thread(target=import_worker, args=(url,), daemon=True).start()
                flash("✅ Import uruchomiony w tle", 'info')
            else:
                flash("❌ Nieprawidłowy URL", 'error')
        except Exception as e:
            logger.error(f"❌ Błąd uruchamiania importu: {e}")
            flash("❌ Błąd uruchamiania importu", 'error')
    
    # Ładowanie danych do panelu
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
            
            # Statystyki dla wykresu (ostatnie 7 dni)
            cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM leaks
            WHERE created_at >= CURDATE() - INTERVAL 7 DAY
            GROUP BY DATE(created_at)
            ORDER BY date ASC
            """)
            daily_stats = cursor.fetchall()
            max_count = max([stat['count'] for stat in daily_stats]) if daily_stats else 1
            
            # Statystyki użytkowników i wyszukiwań
            total_searches = 0
            active_users_24h = 0
            try:
                logs = sb_query("search_logs", "select=count(*)")
                total_searches = logs[0]['count'] if logs and logs[0] else 0
                
                yesterday = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                active_users = sb_query("search_logs", f"timestamp=gte.{yesterday}&select=count(distinct ip)")
                active_users_24h = active_users[0]['count'] if active_users and active_users[0] else 0
            except Exception as e:
                logger.error(f"❌ Błąd pobierania statystyk: {e}")
            
            licenses = sb_query("licenses", "order=created_at.desc")
            banned_ips = sb_query("banned_ips", "order=created_at.desc")
            active_licenses = sum(1 for lic in licenses if lic.get('active', True))
            
            login_time = datetime.fromisoformat(session['login_time'])
            session_duration = str(datetime.now(timezone.utc) - login_time).split('.')[0]
            now = datetime.now(timezone.utc)
            
            return render_template_string(
                ADMIN_TEMPLATE,
                total_leaks=total_leaks,
                source_count=source_count,
                recent_leaks=recent_leaks,
                top_sources=top_sources,
                daily_stats=daily_stats,
                max_count=max_count,
                licenses=licenses,
                banned_ips=banned_ips,
                active_licenses=active_licenses,
                total_searches=total_searches,
                active_users_24h=active_users_24h,
                session_duration=session_duration,
                client_ip=get_client_ip(),
                format_datetime=format_datetime,
                now=now,
                total_leaks_formatted="{:,}".format(total_leaks).replace(",", " ")
            )
    except Exception as e:
        logger.error(f"💥 Błąd ładowania panelu: {e}")
        flash(f"❌ Błąd serwera: {str(e)}", 'error')
        return redirect(url_for('admin_panel'))

@app.route("/logout")
def admin_logout():
    session.clear()
    flash("✅ Wylogowano", 'success')
    return redirect(url_for('admin_panel'))

@app.route("/api/status", methods=["GET"])
def api_status():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            db_status = cursor.fetchone()[0] == 1
    except Exception as e:
        logger.error(f"❌ Błąd połączenia z bazą: {e}")
        db_status = False
    
    return jsonify({
        "success": True,
        "status": "online",
        "version": "3.1.3",
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
    if not licenses or len(licenses) == 0:
        return jsonify({"success": False, "message": "Nieprawidłowy klucz licencyjny"}), 401
    
    lic = licenses[0]
    expiry = datetime.fromisoformat(lic['expiry'].replace('Z', '+00:00'))
    
    if datetime.now(timezone.utc) > expiry or not lic.get('active', True):
        return jsonify({"success": False, "message": "Licencja wygasła lub została zablokowana"}), 401
    
    if lic.get("ip") and lic["ip"].strip() != "" and lic["ip"].strip() != ip.strip():
        return jsonify({"success": False, "message": "Klucz przypisany do innego adresu IP"}), 403
    
    # Jeśli IP nie jest ustawione, aktualizuj je
    if not lic.get("ip"):
        sb_update("licenses", {"ip": ip}, f"key=eq.{key}")
    
    return jsonify({"success": True, "message": "Zalogowano pomyślnie"})

@app.route("/api/license-info", methods=["POST"])
def api_info():
    data = request.json or request.form.to_dict()
    key = data.get("key")
    ip = data.get("client_ip") or get_client_ip()
    
    if not key:
        return jsonify({"success": False, "message": "Brak klucza"}), 400
    
    # Weryfikacja autoryzacji
    auth_response = api_auth()
    if auth_response.status_code != 200:
        return auth_response
    
    licenses = sb_query("licenses", f"key=eq.{key}")
    if not licenses or len(licenses) == 0:
        return jsonify({"success": False, "message": "Nie znaleziono licencji"}), 404
    
    lic = licenses[0]
    
    # Pobierz statystyki użycia
    today_count, total_count = 0, 0
    last_search = "Nigdy"
    try:
        # Dzienne użycie
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_logs = sb_query("search_logs", f"key=eq.{key}&timestamp=gte.{today}T00:00:00.000Z")
        today_count = len(today_logs)
        
        # Całkowite użycie
        total_logs = sb_query("search_logs", f"key=eq.{key}")
        total_count = len(total_logs)
        
        # Ostatnie wyszukiwanie
        if total_logs:
            last_search = max(log['timestamp'] for log in total_logs)
    except Exception as e:
        logger.error(f"Błąd pobierania statystyk użycia: {e}")
    
    return jsonify({
        "success": True,
        "info": {
            "license_type": lic.get("license_type", "Standard").capitalize(),
            "expiration_date": lic["expiry"].split("T")[0],
            "daily_limit": lic.get("daily_limit", 100),
            "daily_used": today_count,
            "total_limit": lic.get("total_limit", 1000),
            "total_used": total_count,
            "ip_bound": lic.get("ip", "Nie przypisano"),
            "last_search": last_search
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
    
    # Sprawdzenie autoryzacji
    auth_response = api_auth()
    if auth_response.status_code != 200:
        return auth_response
    
    try:
        # Pobranie licencji
        licenses = sb_query("licenses", f"key=eq.{key}")
        if not licenses or len(licenses) == 0:
            return jsonify({"success": False, "message": "Nieprawidłowa licencja"}), 401
        
        lic = licenses[0]
        
        # Sprawdzenie dziennego limitu
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_logs = sb_query("search_logs", f"key=eq.{key}&timestamp=gte.{today}T00:00:00.000Z")
        daily_count = len(today_logs)
        
        if daily_count >= lic.get("daily_limit", 100):
            return jsonify({"success": False, "message": "Przekroczono dzienny limit wyszukiwań"}), 429
        
        # Sprawdzenie całkowitego limitu
        total_logs = sb_query("search_logs", f"key=eq.{key}")
        total_count = len(total_logs)
        
        if total_count >= lic.get("total_limit", 1000):
            return jsonify({"success": False, "message": "Przekroczono całkowity limit wyszukiwań"}), 429
        
        # Zapisanie logu wyszukiwania
        sb_insert("search_logs", {
            "key": key,
            "query": query,
            "ip": ip,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        # Wyszukiwanie w bazie danych
        with get_db() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT data, source, created_at as timestamp
                FROM leaks
                WHERE MATCH(data) AGAINST (%s IN BOOLEAN MODE)
                LIMIT %s
            """, (f"+{query}*", limit))
            results = cursor.fetchall()
            
            return jsonify({"success": True, "results": results})
    
    except Exception as e:
        logger.error(f"Błąd wyszukiwania: {e}")
        return jsonify({"success": False, "message": f"Błąd bazy danych: {str(e)}"}), 500

# === SZABLONY HTML ===
ADMIN_LOGIN_TEMPLATE = '''<!DOCTYPE html>
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
        }
        .login-container {
            max-width: 450px;
            width: 100%;
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
        }
        .alert-success {
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid var(--success);
            color: var(--success);
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
            <form method="POST" action="/">
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
</html>'''

ADMIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Search Premium — Panel Admina</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
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
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            overflow-x: hidden;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
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
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: var(--card-bg);
            border-radius: 16px;
            padding: 20px;
            border: 1px solid var(--border);
            text-align: center;
            transition: all 0.3s ease;
        }
        .stat-card:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 20px rgba(0, 0, 0, 0.2);
            border-color: rgba(99, 102, 241, 0.5);
        }
        .stat-card:nth-child(1) { background: linear-gradient(135deg, #6366f1, #8b5cf6); }
        .stat-card:nth-child(2) { background: linear-gradient(135deg, #10b981, #0ea5e9); }
        .stat-card:nth-child(3) { background: linear-gradient(135deg, #f59e0b, #ef4444); }
        .stat-card:nth-child(4) { background: linear-gradient(135deg, #3b82f6, #8b5cf6); }
        .stat-icon {
            font-size: 2rem;
            margin-bottom: 10px;
            opacity: 0.9;
        }
        .stat-value {
            font-size: 1.8rem;
            font-weight: 800;
            color: white;
            font-family: 'JetBrains Mono', monospace;
            margin: 8px 0;
            text-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }
        .stat-label {
            font-size: 0.9rem;
            color: rgba(255,255,255,0.9);
            font-weight: 500;
        }
        .section {
            background: var(--card-bg);
            border-radius: 16px;
            padding: 25px;
            margin-bottom: 25px;
            border: 1px solid var(--border);
            transition: all 0.3s ease;
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
            padding: 12px;
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid var(--border);
            border-radius: 10px;
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
            padding: 12px 24px;
            background: linear-gradient(90deg, #6366f1, #8b5cf6);
            color: white;
            border: none;
            border-radius: 10px;
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
        .table-container {
            overflow-x: auto;
            margin-top: 15px;
            border-radius: 12px;
            border: 1px solid var(--border);
        }
        .table {
            width: 100%;
            border-collapse: collapse;
            min-width: 800px;
        }
        .table th, .table td {
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }
        .table th {
            font-weight: 700;
            background: rgba(0,0,0,0.2);
            color: var(--text);
            font-size: 0.95rem;
            white-space: nowrap;
        }
        .table td {
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
            border-radius: 12px;
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
            height: 8px;
            background: rgba(56, 189, 248, 0.1);
            border-radius: 4px;
            margin-top: 6px;
            overflow: hidden;
        }
        .usage-fill {
            height: 100%;
            border-radius: 4px;
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
        .chart-container {
            height: 200px;
            margin-top: 20px;
            position: relative;
        }
        .source-bar {
            height: 8px;
            background: rgba(56, 189, 248, 0.1);
            border-radius: 4px;
            margin-top: 4px;
            overflow: hidden;
        }
        .source-fill {
            height: 100%;
            border-radius: 4px;
            background: var(--primary);
        }
        .license-type {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.8rem;
            font-weight: 600;
        }
        .type-standard {
            background: rgba(59, 130, 246, 0.2);
            color: #93c5fd;
        }
        .type-premium {
            background: rgba(168, 85, 247, 0.2);
            color: #c4b5fd;
        }
        .type-enterprise {
            background: rgba(239, 68, 68, 0.2);
            color: #fca5a5;
        }
        .footer {
            text-align: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid var(--border);
            color: var(--text-secondary);
            font-size: 0.9rem;
            padding-bottom: 20px;
        }
        @media (max-width: 768px) {
            .form-row { 
                flex-direction: column; 
            }
            .stats-grid {
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            }
            .section {
                padding: 15px;
            }
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
            <a href="{{ url_for('admin_logout') }}" class="logout-btn">
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
                <div class="stat-value">{{ total_leaks_formatted }}</div>
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
                    <i class="fas fa-users"></i>
                </div>
                <div class="stat-value">{{ "{:,}".format(active_users_24h).replace(",", " ") }}</div>
                <div class="stat-label">Aktywni użytkownicy (24h)</div>
            </div>
        </div>
        
        <!-- Najczęstsze źródła -->
        <div class="section">
            <div class="section-title">
                <i class="fas fa-layer-group"></i> Najczęstsze źródła danych
            </div>
            {% for source in top_sources %}
                <div style="margin-bottom: 15px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                        <span style="font-weight: 500;">{{ source.source }}</span>
                        <span style="color: var(--text-secondary);">{{ source.count }}</span>
                    </div>
                    <div class="source-bar">
                        <div class="source-fill" style="width: {{ (source.count / (total_leaks or 1) * 100) | int }}%;"></div>
                    </div>
                </div>
            {% else %}
                <div style="text-align: center; padding: 20px; color: var(--text-secondary);">
                    Brak źródeł danych w bazie
                </div>
            {% endfor %}
        </div>
        
        <!-- Zarządzanie licencjami -->
        <div class="section">
            <div class="section-title">
                <i class="fas fa-key"></i> Zarządzanie licencjami ({{ licenses|length }})
            </div>
            
            <form method="post" style="margin-bottom:25px;">
                <input type="hidden" name="action" value="add_license">
                <div class="form-row">
                    <div class="form-group">
                        <label>Liczba dni ważności</label>
                        <input type="number" name="days" value="30" min="1" max="3650" class="form-input">
                    </div>
                    <div class="form-group">
                        <label>Limit wyszukiwań dziennych</label>
                        <input type="number" name="daily_limit" value="100" min="1" max="10000" class="form-input">
                    </div>
                    <div class="form-group">
                        <label>Limit wyszukiwań całkowitych</label>
                        <input type="number" name="total_limit" value="1000" min="10" max="100000" class="form-input">
                    </div>
                    <div class="form-group">
                        <label>Typ licencji</label>
                        <select name="license_type" class="form-select">
                            <option value="standard">Standardowa</option>
                            <option value="premium">Premium</option>
                            <option value="enterprise">Enterprise</option>
                        </select>
                    </div>
                    <div class="form-group" style="align-self: flex-end;">
                        <button type="submit" class="btn">
                            <i class="fas fa-plus"></i> Generuj licencję
                        </button>
                    </div>
                </div>
            </form>
            
            <div class="table-container">
                <table class="table">
                    <thead>
                        <tr>
                            <th>Klucz dostępu</th>
                            <th>Ważność</th>
                            <th>IP</th>
                            <th>Typ</th>
                            <th>Limit dzienny</th>
                            <th>Status</th>
                            <th>Data utworzenia</th>
                            <th>Akcje</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for lic in licenses %}
                        <tr>
                            <td>
                                <span class="key">{{ lic.key }}</span>
                                <div style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 4px;">
                                    {% if lic.ip and lic.ip.strip() != "" %}
                                    <i class="fas fa-lock" style="margin-right: 4px;"></i>Przypisany do: {{ lic.ip }}
                                    {% else %}
                                    <i class="fas fa-unlock" style="margin-right: 4px; color: var(--warning);"></i>Bez ograniczeń IP
                                    {% endif %}
                                </div>
                            </td>
                            <td>{{ lic.expiry.split('T')[0] }}</td>
                            <td>
                                {% if lic.ip and lic.ip.strip() != "" %}
                                <span class="ip-badge">{{ lic.ip }}</span>
                                {% else %}
                                <span style="color: var(--warning);">Brak ograniczeń</span>
                                {% endif %}
                            </td>
                            <td>
                                <span class="license-type type-{{ lic.license_type.lower() if lic.license_type else 'standard' }}">
                                    {{ lic.license_type.capitalize() if lic.license_type else 'Standard' }}
                                </span>
                            </td>
                            <td>{{ lic.daily_limit }}/{{ lic.total_limit }}</td>
                            <td>
                                <span class="{{ 'status-active' if lic.active else 'status-inactive' }}">
                                    {{ 'Aktywna' if lic.active else 'Nieaktywna' }}
                                </span>
                            </td>
                            <td>{{ format_datetime(lic.created_at) if lic.created_at else '—' }}</td>
                            <td>
                                <div style="display: flex; gap: 8px;">
                                    <form method="post" style="display:inline;" onsubmit="return confirm('Na pewno chcesz {{ '' if lic.active else 'w' }}yłączyć tę licencję?')">
                                        <input type="hidden" name="action" value="toggle_license">
                                        <input type="hidden" name="key" value="{{ lic.key }}">
                                        <button type="submit" class="btn {% if lic.active %}btn-danger{% else %}btn{% endif %}" style="padding: 6px 12px; font-size: 0.85rem;">
                                            {% if lic.active %}<i class="fas fa-power-off"></i> Wyłącz{% else %}<i class="fas fa-power-off"></i> Włącz{% endif %}
                                        </button>
                                    </form>
                                    <form method="post" style="display:inline;" onsubmit="return confirm('Na pewno usunąć tę licencję?')">
                                        <input type="hidden" name="action" value="del_license">
                                        <input type="hidden" name="key" value="{{ lic.key }}">
                                        <button type="submit" class="btn btn-danger" style="padding: 6px 12px; font-size: 0.85rem;">
                                            <i class="fas fa-trash"></i> Usuń
                                        </button>
                                    </form>
                                </div>
                            </td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="8" style="text-align: center; padding: 30px; color: var(--text-secondary);">
                                <i class="fas fa-database" style="font-size: 2rem; margin-bottom: 10px; opacity: 0.5;"></i>
                                <div>Brak licencji w systemie</div>
                                <div style="font-size: 0.9rem; margin-top: 8px;">Wygeneruj pierwszą licencję za pomocą formularza powyżej</div>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
        
        <!-- Zbanowane adresy IP -->
        <div class="section">
            <div class="section-title">
                <i class="fas fa-ban"></i> Zbanowane adresy IP ({{ banned_ips|length }})
            </div>
            <form method="post" style="margin-bottom:25px;">
                <input type="hidden" name="action" value="add_ban">
                <div class="form-row">
                    <div class="form-group">
                        <label>Adres IP do zbanowania</label>
                        <input type="text" name="ip" placeholder="np. 192.168.1.1" class="form-input" required>
                    </div>
                    <div class="form-group">
                        <label>Powód bana (opcjonalnie)</label>
                        <input type="text" name="reason" placeholder="Naruszenie regulaminu" class="form-input">
                    </div>
                    <div class="form-group" style="align-self: flex-end;">
                        <button type="submit" class="btn">
                            <i class="fas fa-ban"></i> Zbanuj adres IP
                        </button>
                    </div>
                </div>
            </form>
            
            <div class="table-container">
                <table class="table">
                    <thead>
                        <tr>
                            <th>Adres IP</th>
                            <th>Powód bana</th>
                            <th>Data bana</th>
                            <th>Zbanował</th>
                            <th>Akcje</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for ban in banned_ips %}
                        <tr>
                            <td><span class="ip-badge">{{ ban.ip }}</span></td>
                            <td>{{ ban.reason or 'Brak informacji' }}</td>
                            <td>{{ format_datetime(ban.created_at) if ban.created_at else '—' }}</td>
                            <td>{{ ban.admin_ip or 'System' }}</td>
                            <td>
                                <form method="post" style="display:inline;" onsubmit="return confirm('Na pewno chcesz odbanować ten adres?')">
                                    <input type="hidden" name="action" value="del_ban">
                                    <input type="hidden" name="ip" value="{{ ban.ip }}">
                                    <button type="submit" class="btn btn-danger" style="padding: 6px 12px; font-size: 0.85rem;">
                                        <i class="fas fa-user-check"></i> Odbanuj
                                    </button>
                                </form>
                            </td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="5" style="text-align: center; padding: 30px; color: var(--text-secondary);">
                                <i class="fas fa-user-slash" style="font-size: 2rem; margin-bottom: 10px; opacity: 0.5;"></i>
                                <div>Brak zbanowanych adresów IP</div>
                                <div style="font-size: 0.9rem; margin-top: 8px;">Dodaj pierwszy zbanowany adres IP za pomocą formularza powyżej</div>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
        
        <!-- Import danych -->
        <div class="section">
            <div class="section-title">
                <i class="fas fa-file-import"></i> Import bazy danych
            </div>
            <div style="background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 12px; padding: 15px; margin-bottom: 20px;">
                <p style="margin: 0; color: #bae6fd; line-height: 1.6;">
                    <strong><i class="fas fa-info-circle" style="margin-right: 8px;"></i>Uwaga:</strong> Import danych może zająć kilka minut w zależności od rozmiaru archiwum ZIP.
                    Proces odbywa się w tle - możesz kontynuować pracę w panelu.
                </p>
            </div>
            <form method="post">
                <input type="hidden" name="action" value="import_start">
                <div class="form-row">
                    <div class="form-group" style="flex: 3;">
                        <label>URL do archiwum ZIP z danymi</label>
                        <input type="url" name="import_url" placeholder="https://example.com/dane.zip" class="form-input" required>
                    </div>
                    <div class="form-group" style="flex: 1; align-self: flex-end;">
                        <button type="submit" class="btn" style="width: 100%;">
                            <i class="fas fa-cloud-download-alt"></i> Importuj dane
                        </button>
                    </div>
                </div>
            </form>
            <div style="margin-top: 15px; padding: 12px; background: rgba(15, 23, 42, 0.7); border-radius: 12px; border: 1px solid var(--border);">
                <p style="margin: 0; color: var(--text-secondary); line-height: 1.6;">
                    <strong><i class="fas fa-file-archive" style="margin-right: 8px; color: var(--warning);"></i>Wymagania:</strong>
                    Archiwum ZIP powinno zawierać pliki tekstowe (.txt, .csv, .log) z danymi wyciekowymi.
                    Każda linia w pliku powinna zawierać pojedynczy rekord (email, login, etc.).
                </p>
            </div>
        </div>
        
        <!-- Ostatnie dane wyciekowe -->
        <div class="section">
            <div class="section-title">
                <i class="fas fa-history"></i> Ostatnie dane wyciekowe
            </div>
            {% for leak in recent_leaks %}
                <div class="leak-item">
                    <div class="leak-data">{{ leak.data | truncate(70) }}</div>
                    <small>
                        <span class="ip-badge">{{ leak.source }}</span>
                        <span style="color: var(--text-secondary); margin-left: 10px;">• {{ format_datetime(leak.created_at) }}</span>
                    </small>
                </div>
            {% else %}
                <div style="text-align: center; padding: 20px; color: var(--text-secondary);">
                    Brak danych wyciekowych w bazie
                </div>
            {% endfor %}
        </div>
        
        <!-- Informacje systemowe -->
        <div class="section">
            <div class="section-title">
                <i class="fas fa-server"></i> Informacje systemowe
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px;">
                <div style="background: rgba(15, 23, 42, 0.6); border-radius: 12px; padding: 15px; border: 1px solid var(--border);">
                    <h4 style="font-size: 1rem; font-weight: 600; margin-bottom: 12px; color: var(--text); display: flex; align-items: center; gap: 10px;">
                        <i class="fas fa-clock" style="color: var(--primary);"></i> Sesja administratora
                    </h4>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                        <div style="padding: 8px;">
                            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Czas trwania</div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: var(--text);">{{ session_duration }}</div>
                        </div>
                        <div style="padding: 8px;">
                            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Twój adres IP</div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: var(--text);">
                                <span class="ip-badge">{{ client_ip }}</span>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div style="background: rgba(15, 23, 42, 0.6); border-radius: 12px; padding: 15px; border: 1px solid var(--border);">
                    <h4 style="font-size: 1rem; font-weight: 600; margin-bottom: 12px; color: var(--text); display: flex; align-items: center; gap: 10px;">
                        <i class="fas fa-database" style="color: var(--primary);"></i> Baza danych leaków
                    </h4>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                        <div style="padding: 8px;">
                            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Status</div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: var(--success);">
                                <i class="fas fa-circle" style="font-size: 0.6rem; margin-right: 6px;"></i> Online
                            </div>
                        </div>
                        <div style="padding: 8px;">
                            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Liczba rekordów</div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: var(--text);">{{ total_leaks_formatted }} rekordów</div>
                        </div>
                        <div style="padding: 8px; grid-column: span 2;">
                            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Ostatnia aktualizacja</div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: var(--text);">{{ format_datetime(now) }}</div>
                        </div>
                    </div>
                </div>
                
                <div style="background: rgba(15, 23, 42, 0.6); border-radius: 12px; padding: 15px; border: 1px solid var(--border);">
                    <h4 style="font-size: 1rem; font-weight: 600; margin-bottom: 12px; color: var(--text); display: flex; align-items: center; gap: 10px;">
                        <i class="fas fa-cloud" style="color: var(--primary);"></i> Supabase API
                    </h4>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                        <div style="padding: 8px;">
                            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Status</div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: var(--success);">
                                <i class="fas fa-circle" style="font-size: 0.6rem; margin-right: 6px;"></i> Online
                            </div>
                        </div>
                        <div style="padding: 8px;">
                            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Aktywne licencje</div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: var(--text);">{{ active_licenses }}</div>
                        </div>
                        <div style="padding: 8px;">
                            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Zbanowane IP</div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: var(--text);">{{ "{:,}".format(banned_ips|length).replace(",", " ") }}</div>
                        </div>
                        <div style="padding: 8px;">
                            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 4px;">Wszystkie wyszukiwania</div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: var(--text);">{{ "{:,}".format(total_searches).replace(",", " ") }}</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="footer">
            <p>❄️ Cold Search Premium Admin Panel &copy; {{ now.year }} | Wersja 3.1.3</p>
            <p style="margin-top: 6px; font-size: 0.85rem; color: var(--text-secondary);">
                Panel jest chroniony hasłem i dostępny wyłącznie dla upoważnionych administratorów
            </p>
        </div>
    </div>
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Formatowanie liczb
            const statValues = document.querySelectorAll('.stat-value');
            statValues.forEach(el => {
                const numStr = el.textContent.replace(/\\s/g, '').replace(/\s/g, '');
                const num = parseInt(numStr.replace(/[^0-9]/g, ''));
                if (!isNaN(num)) {
                    el.textContent = num.toLocaleString('pl-PL');
                }
            });
        });
    </script>
</body>
</html>'''

# === URUCHOMIENIE SERWERA ===
if __name__ == "__main__":
    initialize_db_pool()
    logger.info("🚀 Cold Search Premium — Panel admina gotowy")
    port = int(os.environ.get('PORT', 10000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
