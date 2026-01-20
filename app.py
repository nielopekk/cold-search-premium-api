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
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {message}"
    with LOGS_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
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
        # Pobierz plik
        response = requests.get(zip_url, stream=True)
        response.raise_for_status()

        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "data.zip"
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Rozpakuj
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(tmp_dir)

            log_activity("📦 ZIP rozpakowany — przetwarzam pliki...")

            total = 0
            batch = []
            BATCH_SIZE = 1000

            # Przeszukaj wszystkie pliki w rozpakowanym katalogu
            for file_path in Path(tmp_dir).rglob("*"):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in {".txt", ".csv", ".log"}:
                    continue

                # Ścieżka relatywna (bez ścieżki temp)
                rel_path = file_path.relative_to(tmp_dir).as_posix()

                try:
                    content = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = file_path.read_text(encoding="latin-1")

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

            msg = f"✅ Import zakończony — dodano {total} wpisów do Supabase."
            log_activity(msg)

    except Exception as e:
        msg = f"❌ Błąd podczas importu z ZIP: {str(e)}"
        log_activity(msg)


def _send_batch_to_supabase(batch):
    url = f"{SUPABASE_URL}/rest/v1/leaks"
    response = requests.post(url, headers=SUPABASE_HEADERS, json=batch)
    if response.status_code not in (200, 201):
        raise Exception(f"Supabase error: {response.status_code} – {response.text}")


# === LICENCJE ===
class LicenseManager:
    def validate(self, key, ip):
        try:
            url = f"{SUPABASE_URL}/rest/v1/licenses"
            r = requests.get(url, headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
            data = r.json()
            if not 
                return {"success": False, "message": "Nieprawidłowy klucz"}
            lic = data[0]
            if not lic["active"]:
                return {"success": False, "message": "Klucz został zablokowany"}
            if not lic["ip"]:
                requests.patch(
                    f"{url}?key=eq.{key}",
                    headers=SUPABASE_HEADERS,
                    json={"ip": ip}
                )
                return {"success": True, "message": "IP przypisane"}
            if lic["ip"] != ip:
                return {"success": False, "message": "Klucz przypisany do innego adresu IP"}
            return {"success": True, "message": "OK"}
        except Exception as e:
            logger.error(f"Błąd walidacji: {e}")
            return {"success": False, "message": "Błąd serwera"}

    def generate(self, days):
        new_key = str(uuid.uuid4()).replace("-", "").upper()
        exp = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat() if days > 0 else None
        requests.post(
            f"{SUPABASE_URL}/rest/v1/licenses",
            headers=SUPABASE_HEADERS,
            json={"key": new_key, "active": True, "expiry": exp}
        )
        return new_key


lic_mgr = LicenseManager()


# === FUNKCJE POMOCNICZE ===
def count_leaks():
    url = f"{SUPABASE_URL}/rest/v1/leaks"
    r = requests.head(url, headers=SUPABASE_HEADERS)
    if r.status_code == 200:
        range_header = r.headers.get("content-range", "0-0/0")
        return int(range_header.split("/")[-1])
    return 0

def search_leaks(query, limit=150):
    url = f"{SUPABASE_URL}/rest/v1/leaks"
    params = {
        "data": f"ilike.%{query}%",
        "select": "source,data",
        "limit": limit
    }
    r = requests.get(url, headers=SUPABASE_HEADERS, params=params)
    return r.json() if r.status_code == 200 else []

def get_active_users(hours=24):
    logs = load_activity_logs()
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    ips = set()
    for line in logs:
        if "from" in line and ("Auth success" in line or "IP przypisane" in line):
            try:
                parts = line.split()
                ip_index = parts.index("from") + 1
                ip = parts[ip_index].split("]")[0]
                if "." in ip or ":" in ip:
                    ips.add(ip)
            except:
                pass
    return len(ips)


# === PANEL ADMINA ===
ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Search Premium — Admin Panel</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg: #0f0f1b;
            --card-bg: #1a1a2e;
            --text: #e6e6ff;
            --primary: #4cc9f0;
            --success: #4ade80;
            --warning: #facc15;
            --danger: #f87171;
            --border: #2d2d44;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
        body {
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        header {
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            border-bottom: 1px solid var(--border);
        }
        h1 {
            font-size: 2.4rem;
            background: linear-gradient(90deg, var(--primary), #a663cc);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
            margin-bottom: 10px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-card {
            background: rgba(67, 97, 238, 0.15);
            padding: 16px;
            border-radius: 12px;
            text-align: center;
            border: 1px solid var(--primary);
        }
        .stat-value {
            font-size: 2rem;
            font-weight: bold;
            color: var(--primary);
        }
        .form-group {
            background: var(--card-bg);
            padding: 22px;
            border-radius: 14px;
            margin-bottom: 24px;
            box-shadow: 0 6px 16px rgba(0,0,0,0.4);
        }
        h2 {
            margin-bottom: 16px;
            color: var(--primary);
            font-size: 1.4rem;
        }
        input, button {
            width: 100%;
            padding: 13px;
            margin: 8px 0;
            border: none;
            border-radius: 10px;
            background: #25253a;
            color: white;
            font-size: 16px;
        }
        button {
            background: linear-gradient(90deg, var(--primary), #4361ee);
            color: white;
            font-weight: bold;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        button:hover { opacity: 0.92; }
        .btn-danger { background: linear-gradient(90deg, var(--danger), #b91c1c); }
        .alert {
            padding: 12px;
            border-radius: 8px;
            margin: 15px 0;
            background: rgba(76, 201, 240, 0.15);
            border-left: 4px solid var(--primary);
        }
        .alert-success {
            background: rgba(74, 222, 128, 0.15);
            border-left-color: var(--success);
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
        }
        th, td {
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }
        th {
            color: var(--primary);
        }
        tr:hover {
            background: rgba(67, 97, 238, 0.12);
        }
        .logout-link {
            display: inline-block;
            margin-top: 18px;
            color: var(--danger);
            text-decoration: none;
            font-weight: bold;
        }
        code {
            font-family: monospace;
            background: rgba(0,0,0,0.3);
            padding: 2px 6px;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🔐 Cold Search Premium — Admin Panel</h1>
            {% if authenticated %}
                <p>Zalogowany jako administrator</p>
                <a href="/admin/logout" class="logout-link">→ Wyloguj się</a>
            {% endif %}
        </header>

        {% if not authenticated %}
            <div class="form-group">
                <h2>🔒 Logowanie</h2>
                <form method="POST" action="/admin/login">
                    <input type="password" name="password" placeholder="Hasło panelu" required>
                    <button type="submit">Zaloguj się</button>
                </form>
                {% if error %}
                    <p style="color: var(--danger); margin-top: 12px;">Nieprawidłowe hasło.</p>
                {% endif %}
            </div>
        {% else %}
            <!-- Statystyki -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div>Aktywni (24h)</div>
                    <div class="stat-value">{{ active_24h }}</div>
                </div>
                <div class="stat-card">
                    <div>Wpisy w bazie</div>
                    <div class="stat-value">{{ db_count }}</div>
                </div>
                <div class="stat-card">
                    <div>Licencji</div>
                    <div class="stat-value">{{ licenses|length }}</div>
                </div>
                <div class="stat-card">
                    <div>Aktywne</div>
                    <div class="stat-value">{{ active_keys }}</div>
                </div>
            </div>

            <!-- Import z ZIP URL -->
            <div class="form-group">
                <h2>📥 Import z zewnętrznego ZIP</h2>
                <form method="POST" action="/admin/import_zip">
                    <input type="url" name="zip_url" placeholder="https://example.com/data.zip" required>
                    <button type="submit">Pobierz i zaimportuj</button>
                </form>
            </div>

            <!-- Generowanie klucza -->
            <div class="form-group">
                <h2>➕ Wygeneruj nowy klucz</h2>
                <form method="POST" action="/admin/generate">
                    <label for="days">Ważność (dni):</label>
                    <input type="number" id="days" name="days" min="0" value="7" required>
                    <button type="submit">Generuj</button>
                </form>
                {% if new_key %}
                    <div class="alert alert-success">
                        ✅ Utworzono nowy klucz: <strong>{{ new_key }}</strong>
                    </div>
                {% endif %}
            </div>

            <!-- Logi aktywności -->
            <div class="form-group">
                <h2>📜 Logi aktywności (ostatnie 50)</h2>
                <table>
                    <thead><tr><th>Czas i zdarzenie</th></tr></thead>
                    <tbody>
                        {% for line in logs[-50:] | reverse %}
                        <tr><td><code>{{ line }}</code></td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        {% endif %}
    </div>
</body>
</html>
"""


def render_admin(error=False, new_key=None):
    if new_key:
        log_activity(f"🔑 Utworzono nowy klucz: {new_key}")

    logs = load_activity_logs()
    db_count = count_leaks()
    active_24h = get_active_users(24)

    licenses = {}
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS)
        if r.status_code == 200:
            records = r.json()
            licenses = {rec["key"]: rec for rec in records}
    except Exception as e:
        logger.error(f"Błąd pobierania licencji: {e}")

    active_keys = sum(1 for v in licenses.values() if v["active"])
    return render_template_string(
        ADMIN_TEMPLATE,
        authenticated=session.get("logged_in"),
        error=error,
        new_key=new_key,
        logs=logs,
        db_count=db_count,
        active_24h=active_24h,
        licenses=licenses,
        active_keys=active_keys
    )


# === ENDPOINTY PANELU ===
@app.route("/admin")
def admin_index():
    return render_admin()

@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True
        log_activity("🔓 Administrator zalogował się do panelu")
        return redirect("/admin")
    return render_admin(error=True)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    log_activity("🔒 Administrator wylogował się")
    return redirect("/admin")

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    if not session.get("logged_in"):
        return redirect("/admin")
    days = int(request.form.get("days", 7))
    key = lic_mgr.generate(days)
    return render_admin(new_key=key)

@app.route("/admin/import_zip", methods=["POST"])
def admin_import_zip():
    if not session.get("logged_in"):
        return redirect("/admin")
    zip_url = request.form.get("zip_url")
    if not zip_url:
        return render_admin(error=True)
    thread = threading.Thread(target=import_leaks_from_zip_url, args=(zip_url,))
    thread.start()
    log_activity(f"🔄 Uruchomiono wątek importu z: {zip_url}")
    return render_admin()


# === PUBLICZNE API ===
@app.route("/")
def index():
    return jsonify({"status": "online", "name": "Cold Search Premium API"})

@app.route("/api/status")
def api_status():
    return jsonify({
        "active_users_24h": get_active_users(24),
        "total_entries": count_leaks(),
        "database": "Supabase (leaks table)"
    })

@app.route("/auth", methods=["POST"])
def auth():
    data = request.json
    key = data.get("key")
    ip = data.get("client_ip")
    if not key or not ip:
        return jsonify({"success": False, "message": "Brak klucza lub IP"}), 400
    result = lic_mgr.validate(key, ip)
    if result["success"]:
        log_activity(f"✅ Auth success for key {key} from {ip}")
    else:
        log_activity(f"❌ Auth failed for key {key} from {ip}: {result['message']}")
    return jsonify(result)

@app.route("/search", methods=["POST"])
def search():
    data = request.json
    key = data.get("key")
    query = data.get("query")
    ip = data.get("client_ip")
    if not key or not query or not ip:
        return jsonify({"success": False, "message": "Brak danych"}), 400

    auth = lic_mgr.validate(key, ip)
    if not auth["success"]:
        log_activity(f"🔍 Odmowa wyszukiwania – klucz {key} z {ip}")
        return jsonify(auth), 403

    log_activity(f"🔍 Wyszukiwanie '{query}' przez {key} z {ip}")
    results = []
    rows = search_leaks(query, limit=150)
    for row in rows:
        results.append({"file": row["source"], "content": row["data"]})

    return jsonify({
        "success": True,
        "count": len(results),
        "results": results
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
