import os
import uuid
import requests
import zipfile
import tempfile
import threading
import gc
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template_string, redirect, session

# === KONFIGURACJA ===
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wcshypmsurncfufbojvp.supabase.co").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_secret_Ci0yyib3FCJW3GMivhX3XA_D2vHmhpP").strip()
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

# Konfiguracja Discord Webhooka
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1463453306860081183/1JVeZuCP3Mg0EJi7salIGYxXvvY4618i59SMbHKGj4df2uRow5PQqbQ-AFmlvEdAZ5Ty"

ADMIN_PASSWORD = "wyciek12"
LOGS_FILE = Path("activity.log")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_ultra_2026_fixed")

# === POMOCNICZE FUNKCJE ===
def log_activity(message):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    try:
        with LOGS_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass
    # Wysyłaj ważne logi do Discorda
    if any(keyword in message.lower() for keyword in ["błąd", "error", "niepowodzenie", "wygasł", "usunięto", "nowy klucz"]):
        send_discord_notification(f"🚨 Log systemowy: {message}")

def load_activity_logs():
    if not LOGS_FILE.exists():
        return []
    try:
        return LOGS_FILE.read_text(encoding="utf-8").strip().split("\n")
    except:
        return []

def safe_get_json():
    try:
        data = request.get_json(force=True)
        return data if isinstance(data, dict) else {}
    except:
        return {}

def get_active_users_count():
    """Pobiera liczbę aktywnych użytkowników w ciągu ostatnich 5 minut"""
    try:
        five_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
        r = requests.head(
            f"{SUPABASE_URL}/rest/v1/search_logs",
            headers={**SUPABASE_HEADERS, "Prefer": "count=exact"},
            params={"timestamp": f"gte.{five_minutes_ago}"}
        )
        return int(r.headers.get("content-range", "0-0/0").split("/")[-1])
    except:
        return 0

def send_discord_notification(message, title="Cold Search Premium Alert", color=3447003):
    """Wysyła powiadomienie do Discord webhooka"""
    try:
        embed = {
            "title": title,
            "description": message,
            "color": color,  # Domyślny niebieski kolor
            "footer": {
                "text": f"Cold Search Premium | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            }
        }
        
        payload = {
            "username": "Cold Search System",
            "avatar_url": "https://i.imgur.com/8Y6XJpC.png",
            "embeds": [embed]
        }
        
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        # Nie przerywaj działania aplikacji jeśli Discord jest niedostępny
        log_activity(f"Błąd wysyłania do Discorda: {str(e)}")

def send_user_activity_notification(action, user_data=None):
    """Wysyła szczegółowe powiadomienie o aktywności użytkownika"""
    try:
        active_users = get_active_users_count()
        current_time = datetime.now().strftime("%H:%M:%S")
        
        embed_color = 3066993  # Zielony dla pozytywnych zdarzeń
        if "błąd" in action.lower() or "error" in action.lower() or "niepowodzenie" in action.lower():
            embed_color = 15158332  # Czerwony dla błędów
        
        embed = {
            "title": "📊 Aktywność Systemu",
            "color": embed_color,
            "fields": [
                {"name": "⏰ Czas", "value": current_time, "inline": True},
                {"name": "👥 Aktywni użytkownicy", "value": f"{active_users} (ost. 5 min)", "inline": True},
                {"name": "🔧 Akcja", "value": action, "inline": False},
            ],
            "footer": {
                "text": "Cold Search Premium Monitoring System"
            }
        }
        
        if user_data:
            if "key" in user_data:
                embed["fields"].append({"name": "🔑 Klucz", "value": user_data["key"][:8] + "..." if user_data["key"] else "N/A", "inline": True})
            if "ip" in user_data:
                embed["fields"].append({"name": "🌐 IP", "value": user_data["ip"], "inline": True})
            if "query" in user_data:
                embed["fields"].append({"name": "🔍 Zapytanie", "value": user_data["query"][:50] + "..." if len(user_data["query"]) > 50 else user_data["query"], "inline": False})
        
        payload = {
            "username": "Cold Search Monitor",
            "avatar_url": "https://i.imgur.com/dR5GqRf.png",
            "embeds": [embed]
        }
        
        threading.Thread(target=lambda: requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)).start()
    except Exception as e:
        log_activity(f"Błąd wysyłania szczegółowego powiadomienia: {str(e)}")

def send_startup_notification():
    """Wysyła powiadomienie o uruchomieniu aplikacji"""
    try:
        server_info = {
            "port": os.environ.get("PORT", 5000),
            "environment": os.environ.get("ENV", "production"),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        message = (
            f"🚀 **System Cold Search Premium został uruchomiony**\n"
            f"🕒 Czas: `{server_info['time']}`\n"
            f"🔧 Port: `{server_info['port']}`\n"
            f"🌍 Środowisko: `{server_info['environment'].upper()}`"
        )
        
        send_discord_notification(message, title="✅ System Online", color=3066993)  # Zielony
    except Exception as e:
        log_activity(f"Błąd wysyłania powiadomienia startowego: {str(e)}")

# === LICENCJE ===
class LicenseManager:
    def generate(self, days):
        new_key = "COLD-" + uuid.uuid4().hex.upper()[:12]
        expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        payload = {"key": new_key, "active": True, "expiry": expiry}
        r = requests.post(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, json=payload)
        
        # Powiadomienie o nowym kluczu
        if r.status_code in [200, 201, 204]:
            threading.Thread(target=lambda: send_user_activity_notification(
                f"Nowy klucz wygenerowany ({days} dni)",
                {"key": new_key}
            )).start()
            return new_key
        return f"Error: {r.status_code}"

    def validate(self, key, ip):
        try:
            r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
            data = r.json()
            if not data:
                send_user_activity_notification(f"Próba dostępu z nieistniejącym kluczem", {"key": key, "ip": ip})
                return {"success": False, "message": "Klucz nie istnieje"}
            lic = data[0]
            expiry = datetime.fromisoformat(lic["expiry"].replace('Z', '+00:00'))
            if not lic["active"] or datetime.now(timezone.utc) > expiry:
                send_user_activity_notification(f"Próba dostępu z wygasłym kluczem", {"key": key, "ip": ip})
                return {"success": False, "message": "Klucz wygasł"}
            if not lic.get("ip"):
                requests.patch(f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}", headers=SUPABASE_HEADERS, json={"ip": ip})
                send_user_activity_notification(f"Nowe IP powiązane z kluczem", {"key": key, "ip": ip})
                return {"success": True, "message": "IP powiązane"}
            if lic["ip"] != ip:
                send_user_activity_notification(f"Próba dostępu z niepowiązanym IP", {"key": key, "ip": ip, "bound_ip": lic["ip"]})
                return {"success": False, "message": "Inne IP przypisane"}
            return {"success": True, "message": "OK"}
        except Exception as e:
            log_activity(f"⚠️ Błąd walidacji: {e}")
            return {"success": False, "message": "Błąd bazy danych"}

lic_mgr = LicenseManager()

# === IMPORT Z ZIP (ZOPTYMALIZOWANY) ===
def import_leaks_worker(zip_url):
    log_activity(f"📥 Start importu: {zip_url}")
    try:
        response = requests.get(zip_url, stream=True, timeout=60)
        response.raise_for_status()
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "data.zip"
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(tmp_dir)
            
            total_added = 0
            batch = []
            import_headers = {**SUPABASE_HEADERS, "Prefer": "resolution=ignore-duplicates"}

            for file_path in Path(tmp_dir).rglob("*"):
                if not file_path.is_file() or file_path.suffix.lower() not in {".txt", ".csv", ".log"}:
                    continue
                
                source_name = file_path.name
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            clean_line = line.strip()
                            if clean_line and len(clean_line) <= 1000:
                                batch.append({"data": clean_line, "source": source_name})
                            
                            if len(batch) >= 500:
                                resp = requests.post(
                                    f"{SUPABASE_URL}/rest/v1/leaks",
                                    headers=import_headers,
                                    json=batch
                                )
                                if resp.status_code in (200, 201, 204):
                                    total_added += len(batch)
                                batch = []
                except Exception as e:
                    log_activity(f"⚠️ Błąd pliku {source_name}: {e}")
                    continue
            
            if batch:
                resp = requests.post(
                    f"{SUPABASE_URL}/rest/v1/leaks",
                    headers=import_headers,
                    json=batch
                )
                if resp.status_code in (200, 201, 204):
                    total_added += len(batch)
            
            log_activity(f"✅ Import zakończony. Dodano: {total_added} rekordów.")
            # Powiadomienie o ukończonym imporcie
            send_discord_notification(
                f"✅ Import zakończony pomyślnie\n"
                f"🔗 URL: {zip_url[:50]}...\n"
                f"📈 Dodano rekordów: {total_added}",
                title="📥 Import Bazy Zakończony",
                color=3066993
            )
    except Exception as e:
        error_msg = f"❌ Błąd importu: {str(e)}"
        log_activity(error_msg)
        send_discord_notification(error_msg, title="🚨 Błąd Importu", color=15158332)

# === ENDPOINT: /license-info (DODANY DLA KLIENTA) ===
@app.route("/license-info", methods=["POST"])
def api_license_info():
    d = safe_get_json()
    key = d.get("key")
    ip = d.get("client_ip")

    if not key or not ip:
        return jsonify({"success": False, "message": "Brak klucza lub IP"}), 400

    # Walidacja (opcjonalna, ale zalecana)
    auth = lic_mgr.validate(key, ip)
    if not auth["success"]:
        return jsonify({"success": False, "message": "Nieautoryzowany"}), 403

    try:
        # Pobierz dane licencji
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/licenses",
            headers=SUPABASE_HEADERS,
            params={"key": f"eq.{key}"}
        )
        data = r.json()
        if not data or len(data) == 0:
            return jsonify({"success": False, "message": "Licencja nie znaleziona"}), 404

        lic = data[0]
        expiry = lic["expiry"]
        active = lic["active"]
        ip_bound = lic.get("ip", None)

        # Oblicz zużycie zapytań (liczba logów dla tego klucza)
        try:
            count_resp = requests.head(
                f"{SUPABASE_URL}/rest/v1/search_logs",
                headers={**SUPABASE_HEADERS, "Prefer": "count=exact"},
                params={"key": f"eq.{key}"}
            )
            queries_used = int(count_resp.headers.get("content-range", "0-0/0").split("/")[-1])
        except:
            queries_used = 0

        # Ostatnie wyszukiwanie
        last_search = "Nigdy"
        try:
            search_resp = requests.get(
                f"{SUPABASE_URL}/rest/v1/search_logs",
                headers=SUPABASE_HEADERS,
                params={
                    "key": f"eq.{key}",
                    "order": "timestamp.desc",
                    "limit": 1,
                    "select": "timestamp"
                }
            )
            logs = search_resp.json()
            if logs and isinstance(logs, list) and len(logs) > 0:
                last_search = logs[0].get("timestamp", "Nigdy")
        except:
            pass

        return jsonify({
            "success": True,
            "info": {
                "license_type": "Premium" if active else "Wygasła",
                "expiration_date": expiry.split("T")[0] if expiry else "Nieznana",
                "query_limit": "nieograniczony",
                "queries_used": queries_used,
                "last_search": last_search
            }
        })

    except Exception as e:
        log_activity(f"⚠️ Błąd /license-info: {e}")
        return jsonify({"success": False, "message": "Błąd serwera"}), 500

# === NOWOCZESNY PANEL ADMINA (REACT-LIKE UI) ===
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Search | Admin Panel</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: #00f2ff;
            --secondary: #bc13fe;
            --bg: #0a0a12;
            --card-bg: rgba(15, 15, 25, 0.7);
            --border: rgba(255, 255, 255, 0.08);
            --text: #eaeaff;
            --text-muted: #8888aa;
            --success: #00ffaa;
            --danger: #ff3366;
            --warning: #ffcc00;
            --discord: #5865F2;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            background-image:
                radial-gradient(circle at 10% 20%, rgba(0, 242, 255, 0.05) 0%, transparent 20%),
                radial-gradient(circle at 90% 80%, rgba(188, 19, 254, 0.05) 0%, transparent 20%);
            padding: 20px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        /* LOGIN */
        .login-card {
            max-width: 420px;
            margin: 120px auto;
            background: var(--card-bg);
            padding: 40px;
            border-radius: 24px;
            border: 1px solid var(--border);
            backdrop-filter: blur(12px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
        }
        .login-card h2 {
            text-align: center;
            margin-bottom: 30px;
            font-weight: 800;
            font-size: 1.8rem;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .login-card input {
            width: 100%;
            padding: 16px;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--border);
            border-radius: 14px;
            color: white;
            font-family: 'JetBrains Mono';
            margin-bottom: 20px;
            font-size: 1rem;
        }
        .login-card button {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: #000;
            font-weight: 700;
            border: none;
            border-radius: 14px;
            cursor: pointer;
            font-size: 1.05rem;
            transition: transform 0.2s, opacity 0.2s;
        }
        .login-card button:hover {
            transform: translateY(-2px);
            opacity: 0.95;
        }

        /* HEADER */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 24px 32px;
            background: var(--card-bg);
            border-radius: 24px;
            border: 1px solid var(--border);
            margin-bottom: 32px;
            backdrop-filter: blur(12px);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.3);
        }
        .logo {
            font-size: 1.8rem;
            font-weight: 800;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .btn-logout {
            padding: 10px 24px;
            background: transparent;
            border: 1px solid var(--danger);
            color: var(--danger);
            border-radius: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-logout:hover {
            background: rgba(255, 51, 102, 0.1);
        }

        /* STATS */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 24px;
            margin-bottom: 32px;
        }
        .stat-card {
            background: var(--card-bg);
            padding: 28px;
            border-radius: 20px;
            border: 1px solid var(--border);
            display: flex;
            flex-direction: column;
        }
        .stat-label {
            font-size: 0.9rem;
            color: var(--text-muted);
            margin-bottom: 8px;
        }
        .stat-value {
            font-size: 2.2rem;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
            color: white;
        }

        /* MAIN LAYOUT */
        .main-layout {
            display: grid;
            grid-template-columns: 1fr;
            gap: 28px;
        }
        @media (min-width: 992px) {
            .main-layout {
                grid-template-columns: 380px 1fr;
            }
        }

        .card {
            background: var(--card-bg);
            padding: 32px;
            border-radius: 24px;
            border: 1px solid var(--border);
            backdrop-filter: blur(10px);
        }
        .card h3 {
            font-size: 1.3rem;
            margin-bottom: 24px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .card h3 svg {
            width: 20px;
            height: 20px;
            fill: var(--primary);
        }

        input[type="number"],
        input[type="url"] {
            width: 100%;
            padding: 14px;
            background: rgba(0, 0, 0, 0.35);
            border: 1px solid var(--border);
            border-radius: 12px;
            color: white;
            font-family: 'JetBrains Mono';
            margin-bottom: 18px;
            font-size: 1rem;
        }

        .btn-primary {
            width: 100%;
            padding: 15px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: #000;
            font-weight: 700;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-size: 1rem;
            transition: transform 0.2s, opacity 0.2s;
        }
        .btn-primary:hover {
            transform: translateY(-2px);
            opacity: 0.95;
        }

        .btn-secondary {
            background: linear-gradient(135deg, var(--secondary), #8a00d4);
        }
        
        .btn-discord {
            background: linear-gradient(135deg, var(--discord), #4752c4);
            color: white;
        }

        .generated-key {
            margin-top: 20px;
            padding: 16px;
            background: rgba(0, 0, 0, 0.4);
            border: 1px dashed var(--primary);
            border-radius: 12px;
            text-align: center;
            font-family: 'JetBrains Mono';
            font-size: 1.1rem;
            color: var(--primary);
            word-break: break-all;
        }

        /* TABLE */
        .table-container {
            overflow-x: auto;
            border-radius: 20px;
            border: 1px solid var(--border);
            background: var(--card-bg);
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-family: 'JetBrains Mono';
            font-size: 0.95rem;
        }
        th {
            padding: 16px;
            text-align: left;
            color: var(--text-muted);
            font-weight: 600;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid var(--border);
        }
        td {
            padding: 16px;
            border-bottom: 1px solid var(--border);
        }
        tr:last-child td {
            border-bottom: none;
        }
        .status-active {
            color: var(--success);
            background: rgba(0, 255, 170, 0.1);
            padding: 4px 10px;
            border-radius: 8px;
            font-weight: 600;
        }
        .status-expired, .status-inactive {
            color: var(--danger);
            background: rgba(255, 51, 102, 0.1);
            padding: 4px 10px;
            border-radius: 8px;
            font-weight: 600;
        }

        .actions {
            display: flex;
            gap: 8px;
        }
        .action-btn {
            padding: 4px 10px;
            border: none;
            border-radius: 6px;
            font-size: 0.8rem;
            cursor: pointer;
            font-weight: 600;
        }
        .btn-toggle {
            background: var(--warning);
            color: #000;
        }
        .btn-delete {
            background: var(--danger);
            color: white;
        }

        /* RECENT SEARCHES */
        .recent-searches {
            grid-column: 1 / -1;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 24px;
        }
        .recent-searches h3 {
            margin-bottom: 20px;
        }
        .search-item {
            display: grid;
            grid-template-columns: 1fr 2fr 1fr 100px;
            gap: 12px;
            padding: 12px 0;
            border-bottom: 1px solid var(--border);
            font-family: 'JetBrains Mono';
            font-size: 0.9rem;
        }
        .search-item:last-child {
            border-bottom: none;
        }
        .search-key { color: var(--primary); }
        .search-query { color: white; }
        .search-ip { color: #aaa; }
        .search-time { color: var(--text-muted); }

        /* LOGS */
        .logs-card {
            grid-column: 1 / -1;
            background: rgba(0, 0, 0, 0.4);
            border-radius: 20px;
            padding: 24px;
            height: 280px;
            overflow-y: auto;
            border: 1px solid var(--border);
            font-family: 'JetBrains Mono';
            font-size: 12px;
            color: #aaa;
        }
        .log-line {
            margin-bottom: 6px;
            line-height: 1.4;
        }
        .log-timestamp {
            color: var(--primary);
            margin-right: 8px;
        }
        .log-action {
            color: var(--text);
        }
        .log-error {
            color: var(--danger);
        }
        .log-success {
            color: var(--success);
        }
        
        /* DISCORD NOTIFICATION CARD */
        .discord-card {
            background: linear-gradient(135deg, #5865F2, #4752C4);
            color: white;
            border: none;
            margin-bottom: 24px;
        }
        .discord-card h3 {
            color: white;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .discord-card h3 svg {
            fill: white;
        }
        .discord-status {
            display: flex;
            gap: 20px;
            margin-top: 16px;
        }
        .status-item {
            text-align: center;
        }
        .status-value {
            font-size: 1.8rem;
            font-weight: 800;
            margin: 8px 0;
        }
        .status-label {
            font-size: 0.9rem;
            opacity: 0.9;
        }

        /* LOADING INDICATOR */
        .loading {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.7);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            color: white;
            font-size: 1.2rem;
        }
        .loading.active {
            display: flex;
        }

        /* NOTIFICATION */
        .notification {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 8px;
            background: var(--card-bg);
            border: 1px solid var(--border);
            z-index: 1001;
            transform: translateX(200%);
            transition: transform 0.3s ease;
        }
        .notification.show {
            transform: translateX(0);
        }
        .notification.success { border-left: 4px solid var(--success); }
        .notification.error { border-left: 4px solid var(--danger); }

        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.1); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.2); }
    </style>
</head>
<body>
    <div class="container">
        {% if not authenticated %}
            <div class="login-card">
                <h2>🔐 Secure Login</h2>
                <form id="loginForm">
                    <input type="password" name="password" placeholder="Wprowadź hasło administratora" required autofocus autocomplete="off">
                    <button type="submit">ZALOGUJ SIĘ</button>
                </form>
            </div>
        {% else %}
            <header>
                <div class="logo">❄️ Cold Search Premium</div>
                <button class="btn-logout" id="logoutBtn">WYLOGUJ</button>
            </header>

            <div class="discord-card card">
                <h3>
                    <svg viewBox="0 0 24 24"><path d="M19.54 9.27a6.69 6.69 0 0 1 .46 2.47c0 2.48-1.7 4.49-4.13 5.25a7.21 7.21 0 0 1-3.37.43 7.4 7.4 0 0 1-3.38-.43c-2.58-.81-3.96-2.74-4-5.17.71.4 1.52.63 2.39.65a4.5 4.5 0 0 0 2.37-.7 2 2 0 0 1-1.6-1.93c.1-1.32.86-2.45 1.8-3.09a1.8 1.8 0 0 1 .89-.22c-.28 1.04.2 2.02 1.2 2.62a3.6 3.6 0 0 1 1.04.83 2.01 2.01 0 0 1-2.79 2.98c1.24-.69 2.05-1.98 1.86-3.34a2 2 0 0 1 2.45-2.21 3.47 3.47 0 0 0 1.33-.25c1.23-.5 2.16-1.61 2.37-2.93a2 2 0 0 1 2.49 2.14c-.03.69-.32 1.25-.78 1.65a3.5 3.5 0 0 1 1.75.39c-.61.91-1.77 1.4-3.05 1.4c-.48 0-.94-.08-1.4-.22a2 2 0 0 1-.75 3.84h.01Z"/><circle cx="8.53" cy="11.03" r="1.03"/><circle cx="15.4" cy="11.03" r="1.03"/></svg>
                    Monitor Discord
                </h3>
                <p style="margin-bottom: 20px; opacity: 0.9; line-height: 1.5;">
                    System automatycznie wysyła powiadomienia do Discorda o ważnych zdarzeniach w systemie, takich jak:
                    logowania użytkowników, próby dostępu z nieautoryzowanych adresów IP, generowanie nowych kluczy,
                    oraz błędy systemowe. Poniżej aktualny stan monitoringu.
                </p>
                <div class="discord-status">
                    <div class="status-item">
                        <div class="status-value">{{ active_users }}</div>
                        <div class="status-label">Aktywni użytkownicy</div>
                    </div>
                    <div class="status-item">
                        <div class="status-value">{{ total_searches_today }}</div>
                        <div class="status-label">Wyszukiwań dzisiaj</div>
                    </div>
                    <div class="status-item">
                        <div class="status-value">✅</div>
                        <div class="status-label">Status webhooka</div>
                    </div>
                </div>
                <button class="btn-primary btn-discord" id="sendReportBtn" style="margin-top: 20px; width: auto;">
                    WYŚLIJ RĘCZNY RAPORT DO DISCORDA
                </button>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">REKORDY W BAZIE</div>
                    <div class="stat-value" id="dbCount">{{ "{:,}".format(db_count).replace(",", " ") }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">AKTYWNE LICENCJE</div>
                    <div class="stat-value" id="activeKeys">{{ active_keys }}</div>
                </div>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">WYSZUKAŃ OGÓŁEM</div>
                    <div class="stat-value" id="totalSearches">{{ "{:,}".format(total_searches).replace(",", " ") }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">UNIKALNE IP</div>
                    <div class="stat-value" id="uniqueIps">{{ "{:,}".format(unique_ips).replace(",", " ") }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">WYSZUKAŃ DZIŚ</div>
                    <div class="stat-value" id="searchesToday">{{ "{:,}".format(searches_today).replace(",", " ") }}</div>
                </div>
            </div>

            <div class="main-layout">
                <div class="card">
                    <h3>
                        <svg viewBox="0 0 24 24"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/></svg>
                        Nowa Licencja
                    </h3>
                    <form id="generateForm">
                        <input type="number" name="days" value="30" min="1" placeholder="Liczba dni ważności">
                        <button type="submit" class="btn-primary">GENERUJ KLUCZ</button>
                    </form>
                    <div id="generatedKey" class="generated-key" style="display:none;"></div>

                    <h3 style="margin-top: 36px;">
                        <svg viewBox="0 0 24 24"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
                        Import Bazy ZIP
                    </h3>
                    <form id="importForm">
                        <input type="url" name="zip_url" placeholder="https://example.com/data.zip" required>
                        <button type="submit" class="btn-primary btn-secondary">ROZPOCZNIJ IMPORT</button>
                    </form>
                </div>

                <div class="table-container">
                    <table id="licensesTable">
                        <thead>
                            <tr>
                                <th>Klucz</th>
                                <th>Status</th>
                                <th>IP</th>
                                <th>Akcje</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for lic in licenses %}
                            <tr data-key="{{ lic.key }}">
                                <td style="color:var(--primary); font-weight:600;">{{ lic.key }}</td>
                                <td>
                                    {% if lic.is_active %}
                                        <span class="status-active">Aktywny</span>
                                    {% else %}
                                        <span class="status-expired">Wygasły</span>
                                    {% endif %}
                                    {% if not lic.active %}
                                        <span class="status-inactive">Nieaktywny</span>
                                    {% endif %}
                                </td>
                                <td>{{ lic.ip or '—' }}</td>
                                <td class="actions">
                                    <button class="action-btn btn-toggle" onclick="toggleLicense('{{ lic.key }}')">
                                        {{ "DEZAKTYWUJ" if lic.active else "AKTYWUJ" }}
                                    </button>
                                    <button class="action-btn btn-delete" onclick="deleteLicense('{{ lic.key }}')">USUŃ</button>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <div class="recent-searches">
                    <h3>🔍 Ostatnie wyszukiwania</h3>
                    <div id="recentSearches">
                        {% if recent_searches %}
                            {% for s in recent_searches %}
                            <div class="search-item">
                                <div class="search-key">{{ s.key }}</div>
                                <div class="search-query">{{ s.query }}</div>
                                <div class="search-ip">{{ s.ip }}</div>
                                <div class="search-time">{{ s.time }}</div>
                            </div>
                            {% endfor %}
                        {% else %}
                            <div style="color:var(--text-muted); font-style:italic;">Brak ostatnich wyszukiwań</div>
                        {% endif %}
                    </div>
                </div>

                <div class="logs-card">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                        <h3>📋 Logi systemowe</h3>
                        <button class="action-btn btn-danger" onclick="clearLogs()">WYCZYŚĆ LOGI</button>
                    </div>
                    <div id="logsContent">
                        {% for line in logs[-100:] | reverse %}
                            {% set parts = line.split('] ', 1) %}
                            {% if parts|length == 2 %}
                                <div class="log-line">
                                    <span class="log-timestamp">{{ parts[0][1:] }}</span>
                                    <span class="log-action">
                                        {% if "✅" in parts[1] or "Zalogowano" in parts[1] or "Start importu" in parts[1] %}
                                            <span class="log-success">{{ parts[1] }}</span>
                                        {% elif "❌" in parts[1] or "Błąd" in parts[1] or "🚨" in parts[1] or "⚠️" in parts[1] %}
                                            <span class="log-error">{{ parts[1] }}</span>
                                        {% else %}
                                            {{ parts[1] }}
                                        {% endif %}
                                    </span>
                                </div>
                            {% else %}
                                <div class="log-line">{{ line }}</div>
                            {% endif %}
                        {% endfor %}
                    </div>
                </div>
            </div>
        {% endif %}
    </div>

    <!-- LOADING & NOTIFICATIONS -->
    <div class="loading" id="loading">Trwa przetwarzanie...</div>
    <div class="notification" id="notification"></div>

    <script>
        // Helper functions
        const showLoading = () => document.getElementById('loading').classList.add('active');
        const hideLoading = () => document.getElementById('loading').classList.remove('active');
        const showNotification = (message, type = 'success') => {
            const notif = document.getElementById('notification');
            notif.textContent = message;
            notif.className = `notification ${type}`;
            notif.classList.add('show');
            setTimeout(() => notif.classList.remove('show'), 3000);
        };

        // Form handlers
        document.getElementById('loginForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const res = await fetch('/admin/login', {
                method: 'POST',
                body: formData
            });
            if (res.redirected) window.location.reload();
        });

        document.getElementById('generateForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            showLoading();
            const formData = new FormData(e.target);
            const res = await fetch('/admin/generate', {
                method: 'POST',
                body: formData
            });
            hideLoading();
            if (res.redirected) window.location.reload();
        });

        document.getElementById('importForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            showLoading();
            const formData = new FormData(e.target);
            const res = await fetch('/admin/import_zip', {
                method: 'POST',
                body: formData
            });
            hideLoading();
            if (res.redirected) {
                showNotification('Import został uruchomiony w tle!', 'success');
                e.target.reset();
            }
        });

        document.getElementById('logoutBtn')?.addEventListener('click', () => {
            fetch('/admin/logout').then(() => window.location.reload());
        });
        
        document.getElementById('sendReportBtn')?.addEventListener('click', async () => {
            if (!confirm('Na pewno wysłać raport do Discorda?')) return;
            showLoading();
            try {
                const res = await fetch('/admin/send_discord_report', { method: 'POST' });
                if (res.ok) {
                    showNotification('Raport został wysłany do Discorda!', 'success');
                } else {
                    showNotification('Błąd podczas wysyłania raportu', 'error');
                }
            } catch (error) {
                showNotification('Błąd połączenia', 'error');
            }
            hideLoading();
        });

        // Action handlers
        async function toggleLicense(key) {
            if (!confirm(`Na pewno ${key.startsWith('DEZ') ? 'dezaktywować' : 'aktywować'} klucz?`)) return;
            showLoading();
            const res = await fetch(`/admin/toggle/${key}`, { method: 'POST' });
            hideLoading();
            if (res.redirected) window.location.reload();
        }

        async function deleteLicense(key) {
            if (!confirm('Na pewno usunąć ten klucz?')) return;
            showLoading();
            const res = await fetch(`/admin/delete/${key}`, { method: 'POST' });
            hideLoading();
            if (res.redirected) window.location.reload();
        }

        async function clearLogs() {
            if (!confirm('Na pewno wyczyścić wszystkie logi?')) return;
            showLoading();
            const res = await fetch('/admin/clear_logs', { method: 'POST' });
            hideLoading();
            if (res.redirected) window.location.reload();
        }
    </script>
</body>
</html>
"""

# === ROUTING ===
@app.route("/admin")
def admin_index():
    if not session.get("logged_in"):
        return render_template_string(ADMIN_HTML, authenticated=False)
    
    # Pobierz aktywnych użytkowników
    active_users = get_active_users_count()
    
    # Pobierz wyszukiwania dzisiaj
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_searches_today = 0
    try:
        r = requests.head(
            f"{SUPABASE_URL}/rest/v1/search_logs",
            headers={**SUPABASE_HEADERS, "Prefer": "count=exact"},
            params={"timestamp": f"gte.{today}T00:00:00Z"}
        )
        total_searches_today = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
    except:
        pass

    db_count = 0
    try:
        r = requests.head(f"{SUPABASE_URL}/rest/v1/leaks", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"})
        db_count = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
    except:
        pass

    licenses = []
    active_keys = 0
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"order": "created_at.desc"})
        now = datetime.now(timezone.utc)
        for l in r.json():
            exp = datetime.fromisoformat(l["expiry"].replace('Z', '+00:00'))
            is_active = l["active"] and now < exp
            if is_active:
                active_keys += 1
            licenses.append({
                "key": l["key"],
                "ip": l.get("ip"),
                "active": l["active"],
                "is_active": is_active,
                "time_left": "Aktualny"
            })
    except:
        pass

    total_searches = 0
    unique_ips = 0
    searches_today = 0
    try:
        r = requests.head(f"{SUPABASE_URL}/rest/v1/search_logs", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"})
        total_searches = int(r.headers.get("content-range", "0-0/0").split("/")[-1])

        r = requests.get(f"{SUPABASE_URL}/rest/v1/search_logs", headers=SUPABASE_HEADERS, params={"select": "ip"})
        data = r.json()
        unique_ips = len(set(item.get("ip") for item in data if item.get("ip")))

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r = requests.head(f"{SUPABASE_URL}/rest/v1/search_logs", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"}, params={"timestamp": f"gte.{today}T00:00:00Z"})
        searches_today = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
    except:
        pass

    recent_searches = []
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/search_logs",
            headers=SUPABASE_HEADERS,
            params={"order": "timestamp.desc", "limit": 10, "select": "key,query,ip,timestamp"}
        )
        data = r.json()
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "timestamp" in item:
                    ts = datetime.fromisoformat(item["timestamp"].replace('Z', '+00:00'))
                    recent_searches.append({
                        "key": item.get("key", "—"),
                        "query": item.get("query", "—"),
                        "ip": item.get("ip", "—"),
                        "time": ts.strftime("%H:%M:%S")
                    })
    except:
        pass

    return render_template_string(
        ADMIN_HTML,
        authenticated=True,
        db_count=db_count,
        licenses=licenses,
        active_keys=active_keys,
        logs=load_activity_logs(),
        new_key=session.pop("new_key", None),
        total_searches=total_searches,
        unique_ips=unique_ips,
        searches_today=searches_today,
        recent_searches=recent_searches,
        active_users=active_users,
        total_searches_today=total_searches_today
    )

@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True
        log_activity("Zalogowano do panelu")
        # Powiadomienie o logowaniu admina
        threading.Thread(target=lambda: send_discord_notification(
            f"🔒 Administrator zalogował się do panelu kontrolnego",
            title="✅ Logowanie Administratora",
            color=3066993
        )).start()
    return redirect("/admin")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    log_activity("Wylogowano z panelu")
    return redirect("/admin")

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    if not session.get("logged_in"):
        return redirect("/admin")
    days = int(request.form.get("days", 30))
    key = lic_mgr.generate(days)
    session["new_key"] = key
    log_activity(f"Nowy klucz: {key} ({days} dni)")
    return redirect("/admin")

@app.route("/admin/import_zip", methods=["POST"])
def admin_import_zip():
    if not session.get("logged_in"):
        return redirect("/admin")
    url = request.form.get("zip_url")
    threading.Thread(target=import_leaks_worker, args=(url,), daemon=True).start()
    log_activity(f"Rozpoczęto import: {url}")
    return redirect("/admin")

@app.route("/admin/toggle/<key>", methods=["POST"])
def admin_toggle(key):
    if not session.get("logged_in"):
        return redirect("/admin")
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
        data = r.json()
        if data:
            current = data[0]["active"]
            new_state = not current
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}",
                headers=SUPABASE_HEADERS,
                json={"active": new_state}
            )
            action = f"{'Aktywowano' if new_state else 'Dezaktywowano'} klucz: {key}"
            log_activity(action)
            # Powiadomienie do Discorda
            threading.Thread(target=lambda: send_discord_notification(
                f"🔑 **{action}**\n"
                f"🔑 Klucz: `{key}`\n"
                f"🔄 Nowy status: `{'Aktywny' if new_state else 'Nieaktywny'}`",
                title="🔄 Zmiana statusu licencji",
                color=3447003
            )).start()
    except Exception as e:
        log_activity(f"⚠️ Błąd przełączania klucza: {e}")
    return redirect("/admin")

@app.route("/admin/delete/<key>", methods=["POST"])
def admin_delete(key):
    if not session.get("logged_in"):
        return redirect("/admin")
    try:
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}",
            headers=SUPABASE_HEADERS
        )
        action = f"Usunięto klucz: {key}"
        log_activity(action)
        # Powiadomienie do Discorda
        threading.Thread(target=lambda: send_discord_notification(
            f"🗑️ **Usunięto licencję**\n"
            f"🔑 Klucz: `{key}`\n"
            f"⚠️ Licencja została trwale usunięta z systemu",
            title="🗑️ Usunięcie licencji",
            color=15158332
        )).start()
    except Exception as e:
        log_activity(f"⚠️ Błąd usuwania klucza: {e}")
    return redirect("/admin")

@app.route("/admin/clear_logs", methods=["POST"])
def admin_clear_logs():
    if not session.get("logged_in"):
        return redirect("/admin")
    try:
        if LOGS_FILE.exists():
            LOGS_FILE.unlink()
        log_activity("Wyczyszczono logi systemowe")
        # Powiadomienie do Discorda
        threading.Thread(target=lambda: send_discord_notification(
            "🧹 **Wyczyszczono logi systemowe**\n"
            "🗂️ Wszystkie logi zostały usunięte z serwera",
            title="🧹 Czyszczenie logów",
            color=10181046
        )).start()
    except Exception as e:
        pass
    return redirect("/admin")

# === NOWY ENDPOINT: RĘCZNE WYSŁANIE RAPORTU DO DISCORDA ===
@app.route("/admin/send_discord_report", methods=["POST"])
def admin_send_discord_report():
    if not session.get("logged_in"):
        return jsonify({"success": False}), 403
    
    try:
        # Pobierz statystyki
        db_count = 0
        try:
            r = requests.head(f"{SUPABASE_URL}/rest/v1/leaks", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"})
            db_count = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
        except:
            pass
            
        active_users = get_active_users_count()
        
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total_searches_today = 0
        try:
            r = requests.head(
                f"{SUPABASE_URL}/rest/v1/search_logs",
                headers={**SUPABASE_HEADERS, "Prefer": "count=exact"},
                params={"timestamp": f"gte.{today}T00:00:00Z"}
            )
            total_searches_today = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
        except:
            pass
        
        # Przygotuj raport
        report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"📊 **Raport Systemowy - {report_time}**\n\n"
            f"📈 **Statystyki Systemu:**\n"
            f"• Rekordów w bazie: `{db_count:,}`\n"
            f"• Aktywni użytkownicy (ost. 5 min): `{active_users}`\n"
            f"• Wyszukiwań dzisiaj: `{total_searches_today}`\n\n"
            f"⚙️ **Stan Serwera:**\n"
            f"• Serwer: `{os.getenv('ENV', 'production').upper()}`\n"
            f"• Port: `{os.getenv('PORT', '5000')}`\n"
            f"• Ostatnie logi: `{len(load_activity_logs())} wpisów`\n\n"
            f"✅ Raport wygenerowany ręcznie przez administratora"
        )
        
        send_discord_notification(message, title="📊 Ręczny Raport Systemowy", color=3066993)
        log_activity("Administrator wysłał ręczny raport do Discorda")
        return jsonify({"success": True})
    except Exception as e:
        log_activity(f"Błąd ręcznego raportu: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/auth", methods=["POST"])
def api_auth():
    d = safe_get_json()
    key = d.get("key")
    ip = d.get("client_ip")
    result = lic_mgr.validate(key, ip)
    
    # Logowanie aktywności użytkownika
    if result["success"]:
        threading.Thread(target=lambda: send_user_activity_notification(
            "Udane logowanie",
            {"key": key, "ip": ip}
        )).start()
    else:
        threading.Thread(target=lambda: send_user_activity_notification(
            f"Nieudane logowanie: {result['message']}",
            {"key": key, "ip": ip}
        )).start()
    
    return jsonify(result)

@app.route("/search", methods=["POST"])
def api_search():
    d = safe_get_json()
    key = d.get("key")
    query = d.get("query", "")
    ip = d.get("client_ip")

    if not key or not ip:
        return jsonify({"success": False, "message": "Brak klucza lub IP"}), 400

    auth = lic_mgr.validate(key, ip)
    if not auth["success"]:
        threading.Thread(target=lambda: send_discord_notification(
            f"⚠️ **Odrzucone wyszukiwanie**\n"
            f"🔑 Klucz: `{key[:8]}...`\n"
            f"🌐 IP: `{ip}`\n"
            f"🔍 Zapytanie: `{query[:30]}...`\n"
            f"❌ Powód: `{auth['message']}`",
            title="🚫 Odrzucone zapytanie",
            color=15158332
        )).start()
        return jsonify(auth), 403

    try:
        log_payload = {"key": key, "query": str(query)[:200], "ip": str(ip)}
        requests.post(f"{SUPABASE_URL}/rest/v1/search_logs", headers=SUPABASE_HEADERS, json=log_payload)
        
        # Powiadomienie o wyszukiwaniu (tylko dla ważnych zapytań)
        if len(query) > 3 and not query.isdigit():  # Ignoruj krótkie i numeryczne zapytania
            threading.Thread(target=lambda: send_user_activity_notification(
                f"Wyszukiwanie danych",
                {"key": key, "ip": ip, "query": query}
            )).start()
    except:
        pass

    params = {"data": f"ilike.%{query}%", "select": "source,data", "limit": 150}
    r = requests.get(f"{SUPABASE_URL}/rest/v1/leaks", headers=SUPABASE_HEADERS, params=params)
    return jsonify({"success": True, "results": r.json()})

# === URUCHOMIENIE APLIKACJI ===
if __name__ == "__main__":
    # Wyślij powiadomienie o uruchomieniu aplikacji
    threading.Thread(target=send_startup_notification).start()
    log_activity("Aplikacja została uruchomiona")
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
