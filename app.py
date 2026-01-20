# app.py
import os
import sys
import json
import time
import shutil
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta

import requests
import zipfile
from flask import Flask, request, jsonify, render_template_string, redirect, url_for

from license_manager import LicenseManager

# === KONFIGURACJA ===
LEAKS_DIR = Path("leaks")
LOGS_FILE = Path("logs.json")

LEAKS_DIR.mkdir(exist_ok=True)
if not LOGS_FILE.exists():
    LOGS_FILE.write_text("[]", encoding="utf-8")

# Logowanie
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ColdSearch")

license_manager = LicenseManager()
ADMIN_PASSWORD = "wyciek12"

app = Flask(__name__)


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

def get_active_users(hours=24):
    """Zwraca liczbę unikalnych IP z ostatnich X godzin."""
    logs = load_logs()
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    active_ips = set()
    for log in logs:
        try:
            log_time = datetime.fromisoformat(log["timestamp"].replace("Z", "+00:00"))
            if log_time >= cutoff and log["event"] == "auth":
                active_ips.add(log["ip"])
        except:
            continue
    return len(active_ips)

def download_and_extract_from_url(url):
    """Pobiera i rozpakowuje archiwum ZIP z podanego URL."""
    try:
        logger.info(f"📥 Rozpoczynam pobieranie z: {url}")
        response = requests.get(url, stream=True, timeout=120)
        response.raise_for_status()
        
        archive_path = Path("temp_download.zip")
        with open(archive_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        if LEAKS_DIR.exists():
            shutil.rmtree(LEAKS_DIR)
        LEAKS_DIR.mkdir()
        
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(LEAKS_DIR)
        
        archive_path.unlink()
        file_count = len(list(LEAKS_DIR.rglob("*")))
        logger.info(f"✅ Załadowano {file_count} plików z: {url}")
    except Exception as e:
        logger.error(f"❌ Błąd podczas ładowania danych: {e}")


# === ENDPOINTY PUBLICZNE ===

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "name": "Cold Search Premium API",
        "version": "3.1",
        "status": "online"
    })

@app.route("/api/status", methods=["GET"])
def api_status():
    active_24h = get_active_users(24)
    active_1h = get_active_users(1)
    total = len(license_manager.licenses)
    active_keys = sum(1 for v in license_manager.licenses.values() if v["active"])
    return jsonify({
        "active_users_1h": active_1h,
        "active_users_24h": active_24h,
        "total_licenses": total,
        "active_licenses": active_keys
    })

@app.route("/auth", methods=["POST"])
def auth():
    data = request.json
    key = data.get("key")
    client_ip = data.get("client_ip")
    if not key or not client_ip:
        return jsonify({"success": False, "message": "Brak klucza lub adresu IP"}), 400
    result = license_manager.validate_license(key, client_ip)
    save_log("auth", key=key, ip=client_ip)
    return jsonify(result)

@app.route("/search", methods=["POST"])
def search():
    data = request.json
    key = data.get("key")
    query = data.get("query")
    client_ip = data.get("client_ip")
    if not key or not query or not client_ip:
        return jsonify({"success": False, "message": "Brak danych"}), 400
    auth = license_manager.validate_license(key, client_ip)
    if not auth["success"]:
        save_log("search_denied", key=key, ip=client_ip, query=query)
        return jsonify(auth), 403
    save_log("search", key=key, ip=client_ip, query=query)
    results = []
    query_lower = query.lower()
    extensions = {".txt", ".csv", ".json", ".yml", ".yaml", ".sql", ".cfg", ".log", ".xml", ".ini"}
    if not LEAKS_DIR.exists():
        return jsonify({
            "success": True,
            "count": 0,
            "results": [],
            "warning": "Folder 'leaks' nie istnieje."
        })
    for file_path in LEAKS_DIR.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in extensions:
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="latin-1")
            except Exception:
                continue
        relative_path = file_path.relative_to(LEAKS_DIR).as_posix()
        for line_num, line in enumerate(content.splitlines(), 1):
            if query_lower in line.lower():
                results.append({
                    "file": relative_path,
                    "line": line_num,
                    "content": line.strip()
                })
    return jsonify({
        "success": True,
        "count": len(results),
        "results": results
    })


# === PANEL ADMINA — BEZPIECZNY (POST-REDIRECT-GET) ===

ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Search Premium — Admin Panel</title>
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
                    <div>Aktywni (24h)</div>
                    <div class="stat-value">{{ active_24h }}</div>
                </div>
                <div class="stat-card">
                    <div>Aktywni (1h)</div>
                    <div class="stat-value">{{ active_1h }}</div>
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

            <!-- Ładowanie danych -->
            <div class="form-group">
                <h2>📥 Załaduj dane z URL (.zip)</h2>
                <form method="POST" action="/admin/load_data">
                    <input type="url" name="url" placeholder="https://dropbox.com/.../leaks.zip?dl=1" required>
                    <button type="submit">Pobierz i rozpakuj</button>
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
                        <span style="font-family:monospace; font-size:18px;">{{ new_key }}</span><br>
                        {% if expiry != "Bezterminowa" %}
                            <small>Ważny do: {{ expiry }}</small>
                        {% endif %}
                    </div>
                {% endif %}
            </div>

            <!-- Zablokuj klucz -->
            <div class="form-group">
                <h2>🗑️ Zablokuj klucz</h2>
                <form method="POST" action="/admin/revoke">
                    <input type="text" name="key" placeholder="Klucz do zablokowania..." required>
                    <button type="submit" class="btn-danger">Zablokuj</button>
                </form>
            </div>

            <!-- Licencje -->
            <div class="form-group">
                <h2>📋 Lista licencji ({{ licenses|length }})</h2>
                {% for key, data in licenses.items() %}
                    <div class="license-item {% if not data.active %}revoked{% elif data.expiry and data.expiry < now %}expired{% endif %}">
                        <div class="license-key">{{ key }}</div>
                        <div>
                            <strong>Status:</strong> 
                            {% if not data.active %}
                                <span class="status-revoked">Zablokowana</span>
                            {% elif data.expiry and data.expiry < now %}
                                <span class="status-expired">Wygasła ({{ data.expiry[:10] }})</span>
                            {% elif data.expiry %}
                                <span class="status-active">Aktywna (wygasa {{ data.expiry[:10] }})</span>
                            {% else %}
                                <span class="status-active">Bezterminowa</span>
                            {% endif %}
                        </div>
                        <div><strong>IP:</strong> {{ data.ip or "— nieaktywowany —" }}</div>
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
</body>
</html>
"""


def render_admin_panel(error=False, new_key=None, expiry=None):
    logs = load_logs()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    active_24h = get_active_users(24)
    active_1h = get_active_users(1)
    active_keys = sum(1 for v in license_manager.licenses.values() if v["active"])
    return render_template_string(
        ADMIN_TEMPLATE,
        authenticated=True,
        licenses=license_manager.licenses,
        logs=logs,
        new_key=new_key,
        expiry=expiry,
        now=now,
        active_24h=active_24h,
        active_1h=active_1h,
        active_keys=active_keys
    )


@app.route("/admin")
def admin_index():
    password = request.args.get("password")
    if password == ADMIN_PASSWORD:
        return render_admin_panel()
    else:
        return render_template_string(ADMIN_TEMPLATE, authenticated=False, error=False)

@app.route("/admin/login", methods=["POST"])
def admin_login():
    password = request.form.get("password")
    if password == ADMIN_PASSWORD:
        return redirect(url_for('admin_index') + "?password=" + ADMIN_PASSWORD)
    else:
        return render_template_string(ADMIN_TEMPLATE, authenticated=False, error=True)

@app.route("/admin/logout")
def admin_logout():
    return redirect("/admin")

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    password = request.args.get("password")
    if password != ADMIN_PASSWORD:
        return redirect("/admin")
    days_str = request.form.get("days", "7")
    try:
        days = int(days_str)
        if days < 0: days = 0
    except ValueError:
        days = 7
    key = license_manager.generate_key(valid_days=days if days > 0 else None)
    expiry = license_manager.licenses[key].get("expiry", "Bezterminowa")
    return redirect(url_for('admin_index') + "?password=" + ADMIN_PASSWORD + "&new_key=" + key + "&expiry=" + expiry)

@app.route("/admin/revoke", methods=["POST"])
def admin_revoke():
    password = request.args.get("password")
    if password != ADMIN_PASSWORD:
        return redirect("/admin")
    key = request.form.get("key")
    if key:
        license_manager.revoke_key(key)
    return redirect(url_for('admin_index') + "?password=" + ADMIN_PASSWORD)

@app.route("/admin/load_data", methods=["POST"])
def admin_load_data():
    password = request.args.get("password")
    if password != ADMIN_PASSWORD:
        return redirect("/admin")
    url = request.form.get("url")
    if url:
        thread = threading.Thread(target=download_and_extract_from_url, args=(url,))
        thread.start()
    return redirect(url_for('admin_index') + "?password=" + ADMIN_PASSWORD)


if __name__ == "__main__":
    app.run(debug=True)
