# app.py
import os
import sys
import json
import time
import shutil
import logging
import threading
import sqlite3
import zipfile
import uuid
import requests  # ←←← KLUCZOWY IMPORT!
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
ADMIN_PASSWORD = "wyciek12"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_premium_secret_2026")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ColdSearch")


# === INICJALIZACJA BAZY SQLITE ===
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

init_db()


# === IMPORT ZIP DO SQLITE ===
def import_zip_to_sql(url):
    try:
        logger.info(f"📥 Pobieranie bazy z: {url}")
        r = requests.get(url, stream=True)
        zip_path = "temp_leaks.zip"
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        with zipfile.ZipFile(zip_path, 'r') as z:
            for filename in z.namelist():
                if filename.endswith(('.txt', '.log', '.csv')):
                    logger.info(f"⚙️ Importowanie: {filename}")
                    with z.open(filename) as f:
                        lines = f.read().decode('utf-8', errors='ignore').splitlines()
                        batch = [(filename, line.strip()) for line in lines if line.strip()]
                        cursor.executemany("INSERT INTO leaks (source, data) VALUES (?, ?)", batch)
        
        conn.commit()
        conn.close()
        os.remove(zip_path)
        logger.info("✅ Import zakończony. Dane są w tabeli 'leaks'.")
    except Exception as e:
        logger.error(f"❌ Błąd importu: {e}")


# === LICENCJE Z SUPABASE ===
class LicenseManager:
    def validate(self, key, ip):
        try:
            url = f"{SUPABASE_URL}/rest/v1/licenses"
            r = requests.get(url, headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
            data = r.json()
            if not data:
                return {"success": False, "message": "Nieprawidłowy klucz"}
            lic = data[0]
            if not lic["active"]:
                return {"success": False, "message": "Klucz został zablokowany"}
            
            if not lic["ip"]:
                requests.patch(url, headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"}, json={"ip": ip})
                return {"success": True, "message": "IP przypisane"}
            
            return {"success": True, "message": "OK"} if lic["ip"] == ip else {"success": False, "message": "Klucz przypisany do innego adresu IP"}
        except Exception as e:
            logger.error(f"Błąd walidacji licencji: {e}")
            return {"success": False, "message": "Błąd serwera"}

    def generate(self, days):
        new_key = str(uuid.uuid4()).replace("-", "").upper()
        exp = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat() if days > 0 else None
        requests.post(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, json={"key": new_key, "active": True, "expiry": exp})
        return new_key

lic_mgr = LicenseManager()


# === PANEL ADMINA Z WYKRESEM ===

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
        .license-item.expired { border-left-color: var(--danger); }
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
        .status-expired { color: var(--danger); }
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

            <!-- Ładowanie danych -->
            <div class="form-group">
                <h2>📥 Załaduj dane z URL (.zip)</h2>
                <form method="POST" action="/admin/load_data">
                    <input type="url" name="url" placeholder="https://dropbox.com/.../leaks.zip?dl=1" required>
                    <button type="submit">Pobierz i zaimportuj do SQLite</button>
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
                    </div>
                {% endfor %}
            </div>
        {% endif %}
    </div>

    {% if authenticated %}
    <script>
        const ctx = document.getElementById('statsChart').getContext('2d');
        new Chart(ctx, {
            type: 'bar',
             {
                labels: ['Wpisy w bazie', 'Licencji', 'Aktywne'],
                datasets: [{
                    label: 'Statystyki',
                     [{{ db_count }}, {{ licenses|length }}, {{ active_keys }}],
                    backgroundColor: [
                        'rgba(76, 201, 240, 0.7)',
                        'rgba(67, 97, 238, 0.6)',
                        'rgba(74, 222, 128, 0.6)'
                    ],
                    borderColor: [
                        'rgba(76, 201, 240, 1)',
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


def get_db_count():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM leaks").fetchone()[0]
    conn.close()
    return count

def render_admin_panel(error=False, new_key=None):
    db_count = get_db_count()
    licenses = {}
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/licenses",
            headers=SUPABASE_HEADERS,
            params={"select": "*"}
        )
        if response.status_code == 200:
            records = response.json()
            licenses = {r["key"]: r for r in records}
    except Exception as e:
        logger.error(f"Błąd pobierania licencji: {e}")

    active_keys = sum(1 for v in licenses.values() if v["active"])
    return render_template_string(
        ADMIN_TEMPLATE,
        authenticated=True,
        licenses=licenses,
        db_count=db_count,
        active_keys=active_keys,
        new_key=new_key
    )


@app.route("/admin")
def admin_index():
    if session.get("logged_in"):
        return render_admin_panel()
    else:
        return render_template_string(ADMIN_TEMPLATE, authenticated=False, error=False)

@app.route("/admin/login", methods=["POST"])
def admin_login():
    password = request.form.get("password")
    if password == ADMIN_PASSWORD:
        session["logged_in"] = True
        return redirect("/admin")
    else:
        return render_template_string(ADMIN_TEMPLATE, authenticated=False, error=True)

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect("/admin")

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    if not session.get("logged_in"):
        return redirect("/admin")
    days_str = request.form.get("days", "7")
    try:
        days = int(days_str)
        if days < 0: days = 0
    except ValueError:
        days = 7
    key = lic_mgr.generate(days)
    return redirect("/admin?new_key=" + key)

@app.route("/admin/load_data", methods=["POST"])
def admin_load_data():
    if not session.get("logged_in"):
        return redirect("/admin")
    url = request.form.get("url")
    if url:
        thread = threading.Thread(target=import_zip_to_sql, args=(url,))
        thread.start()
    return redirect("/admin")


# === ENDPOINTY PUBLICZNE ===

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "name": "Cold Search Premium API",
        "version": "6.1",
        "status": "online"
    })

@app.route("/api/status", methods=["GET"])
def api_status():
    db_count = get_db_count()
    return jsonify({
        "total_entries": db_count,
        "database": "SQLite (leaks table)"
    })

@app.route("/auth", methods=["POST"])
def auth():
    data = request.json
    key = data.get("key")
    client_ip = data.get("client_ip")
    if not key or not client_ip:
        return jsonify({"success": False, "message": "Brak klucza lub adresu IP"}), 400
    result = lic_mgr.validate(key, client_ip)
    return jsonify(result)

@app.route("/search", methods=["POST"])
def search():
    data = request.json
    key = data.get("key")
    query = data.get("query")
    client_ip = data.get("client_ip")
    if not key or not query or not client_ip:
        return jsonify({"success": False, "message": "Brak danych"}), 400
    
    auth = lic_mgr.validate(key, client_ip)
    if not auth["success"]:
        return jsonify(auth), 403

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT source, data FROM leaks WHERE data LIKE ? LIMIT 150", (f'%{query}%',))
    rows = cursor.fetchall()
    conn.close()

    results = [{"source": r[0], "line": r[1]} for r in rows]
    return jsonify({
        "success": True,
        "results": results,
        "count": len(results)
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
