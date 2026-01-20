import os
import sys
import json
import time
import logging
import threading
import uuid
import requests
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template_string, redirect, session

# === KONFIGURACJA ===
# Jeśli używasz na serwerze, najlepiej ustaw te zmienne w środowisku (Environment Variables)
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wcshypmsurncfufbojvp.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_secret_Ci0yyib3FCJW3GMivhX3XA_D2vHmhpP")
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

LOGS_FILE = Path("activity.log")
ADMIN_PASSWORD = "wyciek12"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_premium_secret_2026")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ColdSearch")

# === LOGI AKTYWNOŚCI ===
def log_activity(message):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    try:
        with LOGS_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.error(f"Błąd zapisu logów: {e}")
    logger.info(message)

def load_activity_logs():
    if not LOGS_FILE.exists():
        return []
    try:
        text = LOGS_FILE.read_text(encoding="utf-8").strip()
        return [line for line in text.split("\n") if line]
    except Exception:
        return []

# === IMPORT Z ZIP URL ===
def import_leaks_from_zip_url(zip_url):
    log_activity(f"📥 Rozpoczęto pobieranie ZIP z: {zip_url}")
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

            log_activity("📦 ZIP rozpakowany — przetwarzam pliki...")

            total = 0
            batch = []
            BATCH_SIZE = 1000

            for file_path in Path(tmp_dir).rglob("*"):
                if not file_path.is_file(): continue
                if file_path.suffix.lower() not in {".txt", ".csv", ".log"}: continue

                rel_path = file_path.relative_to(tmp_dir).as_posix()

                try:
                    # Używamy errors='replace', żeby nie wywalało błędu przy dziwnych znakach
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception:
                    continue

                lines = [line.strip() for line in content.splitlines() if line.strip()]
                for line in lines:
                    batch.append({"source": rel_path, "data": line})
                    if len(batch) >= BATCH_SIZE:
                        _send_batch_to_supabase(batch)
                        total += len(batch)
                        batch = []
                        time.sleep(0.1)

            if batch:
                _send_batch_to_supabase(batch)
                total += len(batch)

            log_activity(f"✅ Import zakończony — dodano {total} wpisów.")

    except Exception as e:
        log_activity(f"❌ Błąd podczas importu z ZIP: {str(e)}")

def _send_batch_to_supabase(batch):
    url = f"{SUPABASE_URL}/rest/v1/leaks"
    response = requests.post(url, headers=SUPABASE_HEADERS, json=batch)
    if response.status_code not in (200, 201):
        logger.error(f"Supabase error: {response.status_code} – {response.text}")

# === LICENCJE ===
class LicenseManager:
    def validate(self, key, ip):
        try:
            url = f"{SUPABASE_URL}/rest/v1/licenses"
            r = requests.get(url, headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
            data = r.json()
            
            if not data:
                return {"success": False, "message": "Nieprawidłowy klucz"}
            
            lic = data[0]
            if not lic.get("active"):
                return {"success": False, "message": "Klucz został zablokowany"}
            
            # Sprawdzenie IP
            if not lic.get("ip"):
                requests.patch(f"{url}?key=eq.{key}", headers=SUPABASE_HEADERS, json={"ip": ip})
                return {"success": True, "message": "IP przypisane"}
            
            if lic["ip"] != ip:
                return {"success": False, "message": "Klucz przypisany do innego IP"}
            
            return {"success": True, "message": "OK"}
        except Exception as e:
            logger.error(f"Błąd walidacji: {e}")
            return {"success": False, "message": "Błąd serwera"}

    def generate(self, days):
        new_key = str(uuid.uuid4()).replace("-", "").upper()
        exp = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat() if days > 0 else None
        requests.post(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS,
                      json={"key": new_key, "active": True, "expiry": exp})
        return new_key

lic_mgr = LicenseManager()

# === FUNKCJE POMOCNICZE ===
def count_leaks():
    try:
        url = f"{SUPABASE_URL}/rest/v1/leaks"
        # Specjalny nagłówek Supabase do liczenia rekordów
        headers = {**SUPABASE_HEADERS, "Prefer": "count=exact"}
        r = requests.head(url, headers=headers)
        range_header = r.headers.get("content-range", "0-0/0")
        return int(range_header.split("/")[-1])
    except:
        return 0

def search_leaks(query, limit=150):
    url = f"{SUPABASE_URL}/rest/v1/leaks"
    params = {"data": f"ilike.%{query}%", "select": "source,data", "limit": limit}
    r = requests.get(url, headers=SUPABASE_HEADERS, params=params)
    return r.json() if r.status_code == 200 else []

def get_active_users(hours=24):
    logs = load_activity_logs()
    ips = set()
    for line in logs[-500:]: # Analizujemy ostatnie 500 wpisów
        if "Auth success" in line and "from" in line:
            try:
                ip = line.split("from")[-1].strip()
                ips.add(ip)
            except: pass
    return len(ips)

# === PANEL ADMINA (TEMPLATE) ===
ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <title>Cold Search Premium — Panel</title>
    <style>
        :root { --bg: #0f0f1b; --card: #1a1a2e; --text: #e6e6ff; --primary: #4cc9f0; --danger: #f87171; }
        body { background: var(--bg); color: var(--text); font-family: sans-serif; padding: 20px; }
        .container { max-width: 1000px; margin: 0 auto; }
        .card { background: var(--card); padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #2d2d44; }
        input, button { padding: 10px; border-radius: 5px; border: none; margin: 5px 0; }
        input { width: 100%; background: #25253a; color: white; }
        button { background: var(--primary); color: black; font-weight: bold; cursor: pointer; width: 100%; }
        table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
        th, td { text-align: left; padding: 8px; border-bottom: 1px solid #2d2d44; }
        .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 20px; }
        .stat-item { background: var(--card); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid var(--primary); }
        .logout { color: var(--danger); text-decoration: none; float: right; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔐 Cold Search Admin</h1>
        {% if not authenticated %}
            <div class="card">
                <form method="POST" action="/admin/login">
                    <input type="password" name="password" placeholder="Hasło panelu" required>
                    <button type="submit">Zaloguj się</button>
                </form>
            </div>
        {% else %}
            <a href="/admin/logout" class="logout">Wyloguj się</a>
            <div class="stat-grid">
                <div class="stat-item">Aktywni (24h)<br><strong>{{ active_24h }}</strong></div>
                <div class="stat-item">Baza danych<br><strong>{{ db_count }}</strong></div>
                <div class="stat-item">Licencji<br><strong>{{ licenses|length }}</strong></div>
                <div class="stat-item">Aktywne<br><strong>{{ active_keys }}</strong></div>
            </div>

            <div class="card">
                <h3>📥 Import z URL (.zip)</h3>
                <form method="POST" action="/admin/import_zip">
                    <input type="url" name="zip_url" placeholder="https://example.com/data.zip" required>
                    <button type="submit">Rozpocznij import</button>
                </form>
            </div>

            <div class="card">
                <h3>➕ Generuj Klucz</h3>
                <form method="POST" action="/admin/generate">
                    <input type="number" name="days" value="7" placeholder="Dni ważności">
                    <button type="submit">Generuj</button>
                </form>
                {% if new_key %}<p style="color: #4ade80">Klucz: <code>{{ new_key }}</code></p>{% endif %}
            </div>

            <div class="card">
                <h3>📜 Ostatnie Logi</h3>
                <div style="max-height: 300px; overflow-y: auto; background: black; padding: 10px; font-family: monospace; font-size: 12px;">
                    {% for line in logs[-30:] | reverse %}
                        <div>{{ line }}</div>
                    {% endfor %}
                </div>
            </div>
        {% endif %}
    </div>
</body>
</html>
"""

def render_admin(error=False, new_key=None):
    logs = load_activity_logs()
    db_count = count_leaks()
    active_24h = get_active_users(24)
    licenses = []
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS)
        if r.status_code == 200: licenses = r.json()
    except: pass
    
    active_keys_count = sum(1 for lic in licenses if lic.get("active"))

    return render_template_string(
        ADMIN_TEMPLATE,
        authenticated=session.get("logged_in"),
        new_key=new_key,
        logs=logs,
        db_count=db_count,
        active_24h=active_24h,
        licenses=licenses,
        active_keys=active_keys_count
    )

# === ENDPOINTY PANELU ===
@app.route("/admin")
def admin_index():
    return render_admin()

@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True
        log_activity("🔓 Administrator zalogował się")
        return redirect("/admin")
    return render_admin(error=True)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect("/admin")

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    if not session.get("logged_in"): return redirect("/admin")
    days = int(request.form.get("days", 7))
    key = lic_mgr.generate(days)
    return render_admin(new_key=key)

@app.route("/admin/import_zip", methods=["POST"])
def admin_import_zip():
    if not session.get("logged_in"): return redirect("/admin")
    zip_url = request.form.get("zip_url")
    if zip_url:
        threading.Thread(target=import_leaks_from_zip_url, args=(zip_url,)).start()
        log_activity(f"🔄 Import w tle: {zip_url}")
    return redirect("/admin")

# === PUBLICZNE API ===
@app.route("/")
def index():
    return jsonify({"status": "online", "service": "Cold Search Premium"})

@app.route("/auth", methods=["POST"])
def auth():
    data = request.json or {}
    key, ip = data.get("key"), data.get("client_ip")
    if not key or not ip:
        return jsonify({"success": False, "message": "Brak danych"}), 400
    
    res = lic_mgr.validate(key, ip)
    if res["success"]:
        log_activity(f"✅ Auth success: {key} from {ip}")
    else:
        log_activity(f"❌ Auth failed: {key} from {ip} ({res['message']})")
    return jsonify(res)

@app.route("/search", methods=["POST"])
def search():
    data = request.json or {}
    key, query, ip = data.get("key"), data.get("query"), data.get("client_ip")
    
    if not all([key, query, ip]):
        return jsonify({"success": False, "message": "Brak parametrów"}), 400

    check = lic_mgr.validate(key, ip)
    if not check["success"]:
        return jsonify(check), 403

    log_activity(f"🔍 Search: '{query}' by {key} from {ip}")
    rows = search_leaks(query)
    results = [{"file": r["source"], "content": r["data"]} for r in rows]

    return jsonify({"success": True, "count": len(results), "results": results})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
