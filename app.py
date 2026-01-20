# app.py
import os
import sys
import json
import time
import shutil
import logging
import threading
import sqlite3
import uuid
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template_string, redirect, session

# === KONFIGURACJA ===
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wcshypmsurncfufbojvp.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_secret_Ci0yyib3FCJW3GMivhX3XA_D2vHmhpP")
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

DB_PATH = "leaks.db"
LOGS_FILE = Path("logs.json")
ADMIN_PASSWORD = "wyciek12"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_premium_secret_2026")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ColdSearch")


# === INICJALIZACJA BAZY I LOGÓW ===
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            data TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_data ON leaks(data)')
    conn.commit()
    conn.close()

if not LOGS_FILE.exists():
    LOGS_FILE.write_text("[]", encoding="utf-8")

init_db()


def load_logs():
    try:
        return json.loads(LOGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_log(event_type, key=None, ip=None, query=None):
    logs = load_logs()
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "event": event_type,
        "key": key,
        "ip": ip,
        "query": query
    }
    logs.append(entry)
    LOGS_FILE.write_text(json.dumps(logs, indent=2, ensure_ascii=False), encoding="utf-8")


# === IMPORT Z FOLDERU LEAKS ===
def import_leaks_to_sqlite():
    try:
        logger.info("📥 Importuję dane z folderu 'leaks/'...")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        total = 0
        for file_path in Path("leaks").rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {".txt", ".csv", ".log"}:
                continue
            rel_path = file_path.relative_to("leaks").as_posix()
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = file_path.read_text(encoding="latin-1")
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            batch = [(rel_path, line) for line in lines]
            if batch:
                cursor.executemany("INSERT INTO leaks (source, data) VALUES (?, ?)", batch)
                total += len(batch)
        conn.commit()
        conn.close()
        logger.info(f"✅ Zaimportowano {total} wpisów.")
    except Exception as e:
        logger.error(f"❌ Błąd importu: {e}")


# === LICENCJE Z SUPABASE ===
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
                requests.patch(url, headers=SUPABASE_HEADERS, json={"ip": ip})
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
        requests.post(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, json={"key": new_key, "active": True, "expiry": exp})
        return new_key

lic_mgr = LicenseManager()


# === BEZPIECZNE ODPOWIEDZI Z SQLITE ===
def safe_db_query(query, params=()):
    for _ in range(3):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute(query, params)
            result = cursor.fetchall()
            conn.close()
            return result
        except sqlite3.OperationalError:
            time.sleep(0.5)
    return []

def get_leaks_count():
    result = safe_db_query("SELECT COUNT(*) FROM leaks")
    return result[0][0] if result else 0

def get_active_users(hours=24):
    logs = load_logs()
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    ips = set()
    for log in logs:
        try:
            ts = datetime.fromisoformat(log["timestamp"].replace("Z", "+00:00"))
            if ts >= cutoff and log["event"] == "auth":
                ips.add(log["ip"])
        except:
            continue
    return len(ips)


# === PANEL ADMINA — PEŁNA WERSJA ===

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
        .chart-container {
            background: var(--card-bg);
            padding: 20px;
            border-radius: 14px;
            margin-bottom: 24px;
            box-shadow: 0 6px 16px rgba(0,0,0,0.4);
            height: 250px;
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
        .license-item {
            background: #25253a;
            padding: 16px;
            border-radius: 12px;
            margin: 12px 0;
            border-left: 4px solid var(--success);
        }
        .license-item.revoked { border-left-color: var(--warning); }
        .license-key {
            font-family: monospace;
            font-weight: bold;
            word-break: break-all;
            margin-bottom: 10px;
            font-size: 1.1em;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
        }
        th, td {
            padding: 13px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }
        th {
            color: var(--primary);
            font-weight: 600;
        }
        tr:hover {
            background: rgba(67, 97, 238, 0.12);
        }
        .status-active { color: var(--success); }
        .status-revoked { color: var(--warning); }
        .logout-link {
            display: inline-block;
            margin-top: 18px;
            color: var(--danger);
            text-decoration: none;
            font-weight: bold;
        }
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

            <!-- Wykres -->
            <div class="chart-container">
                <canvas id="statsChart"></canvas>
            </div>

            <!-- Import folderu -->
            <div class="form-group">
                <h2>📁 Zaimportuj dane z folderu leaks/</h2>
                <form method="POST" action="/admin/import_leaks">
                    <button type="submit">Importuj do SQLite</button>
                </form>
            </div>

            <!-- Generowanie klucza -->
            <div class="form-group">
                <h2>➕ Wygeneruj nowy klucz</h2>
                <form method="POST" action="/admin/generate">
                    <label for="days">Ważność (dni):</label>
                    <input type="number" id="days" name="days" min="0" value="7" placeholder="0 = bezterminowy" required>
                    <button type="submit">Generuj klucz</button>
                </form>
                {% if new_key %}
                    <div class="alert alert-success">
                        <strong>✅ Nowy klucz:</strong><br>
                        <span style="font-family:monospace; font-size:18px;">{{ new_key }}</span>
                    </div>
                {% endif %}
            </div>

            <!-- Licencje -->
            <div class="form-group">
                <h2>📋 Lista licencji ({{ licenses|length }})</h2>
                {% for key, data in licenses.items() %}
                    <div class="license-item {% if not data.active %}revoked{% endif %}">
                        <div class="license-key">{{ key }}</div>
                        <div>
                            <strong>Status:</strong> 
                            {% if not data.active %}
                                <span class="status-revoked">Zablokowana</span>
                            {% elif data.ip %}
                                <span class="status-active">Aktywna</span>
                            {% else %}
                                <span class="status-active">Nieaktywowana</span>
                            {% endif %}
                        </div>
                        <div><strong>IP:</strong> {{ data.ip or "—" }}</div>
                        <div><strong>Aktywowana:</strong> {{ data.activated or "—" }}</div>
                    </div>
                {% endfor %}
            </div>

            <!-- Logi -->
            <div class="form-group">
                <h2>📜 Ostatnie logi</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Czas (UTC)</th>
                            <th>Typ</th>
                            <th>IP</th>
                            <th>Klucz</th>
                            <th>Zapytanie</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for log in logs[-50:] | reverse %}
                        <tr>
                            <td>{{ log.timestamp[:19] }}</td>
                            <td>{{ log.event }}</td>
                            <td>{{ log.ip }}</td>
                            <td>{{ log.key or "—" }}</td>
                            <td>{{ log.query or "—" }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        {% endif %}
    </div>

    {% if authenticated %}
    <script>
        const ctx = document.getElementById('statsChart').getContext('2d');
        new Chart(ctx, {
            type: 'bar',
             {
                labels: ['Aktywni (24h)', 'Wpisy', 'Licencji', 'Aktywne'],
                datasets: [{
                    label: 'Statystyki',
                     [{{ active_24h }}, {{ db_count }}, {{ licenses|length }}, {{ active_keys }}],
                    backgroundColor: [
                        'rgba(76, 201, 240, 0.7)',
                        'rgba(67, 97, 238, 0.6)',
                        'rgba(67, 97, 238, 0.6)',
                        'rgba(74, 222, 128, 0.6)'
                    ],
                    borderColor: [
                        'rgba(76, 201, 240, 1)',
                        'rgba(67, 97, 238, 1)',
                        'rgba(67, 97, 238, 1)',
                        'rgba(74, 222, 128, 1)'
                    ],
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { color: '#e6e6ff' },
                        grid: { color: 'rgba(255,255,255,0.1)' }
                    },
                    x: {
                        ticks: { color: '#e6e6ff' },
                        grid: { color: 'rgba(255,255,255,0.1)' }
                    }
                },
                plugins: {
                    legend: { labels: { color: '#e6e6ff' } }
                }
            }
        });
    </script>
    {% endif %}
</body>
</html>
"""


def render_admin(error=False, new_key=None):
    logs = load_logs()
    db_count = get_leaks_count()
    active_24h = get_active_users(24)
    
    licenses = {}
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"select": "*"})
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
        return redirect("/admin")
    return render_admin(error=True)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect("/admin")

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    if not session.get("logged_in"):
        return redirect("/admin")
    days = int(request.form.get("days", 7))
    key = lic_mgr.generate(days if days > 0 else None)
    return render_admin(new_key=key)

@app.route("/admin/import_leaks", methods=["POST"])
def admin_import_leaks():
    if not session.get("logged_in"):
        return redirect("/admin")
    thread = threading.Thread(target=import_leaks_to_sqlite)
    thread.start()
    return render_admin()


# === ENDPOINTY PUBLICZNE ===

@app.route("/")
def index():
    return jsonify({"status": "online", "name": "Cold Search Premium API"})

@app.route("/api/status")
def api_status():
    return jsonify({
        "active_users_24h": get_active_users(24),
        "total_entries": get_leaks_count(),
        "database": "SQLite (leaks table)"
    })

@app.route("/auth", methods=["POST"])
def auth():
    data = request.json
    key = data.get("key")
    ip = data.get("client_ip")
    if not key or not ip:
        return jsonify({"success": False, "message": "Brak klucza lub IP"}), 400
    result = lic_mgr.validate(key, ip)
    save_log("auth", key=key, ip=ip)
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
        save_log("search_denied", key=key, ip=ip, query=query)
        return jsonify(auth), 403

    save_log("search", key=key, ip=ip, query=query)
    results = []
    rows = safe_db_query("SELECT source, data FROM leaks WHERE data LIKE ? LIMIT 150", (f"%{query}%",))
    for row in rows:
        results.append({"file": row[0], "content": row[1]})

    return jsonify({
        "success": True,
        "count": len(results),
        "results": results
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
