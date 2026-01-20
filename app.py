# app.py
from flask import Flask, request, jsonify, render_template_string, redirect
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from license_manager import LicenseManager

app = Flask(__name__)

LEAKS_DIR = Path("leaks")
LOGS_FILE = Path("logs.json")

LEAKS_DIR.mkdir(exist_ok=True)
if not LOGS_FILE.exists():
    LOGS_FILE.write_text("[]", encoding="utf-8")

license_manager = LicenseManager()
ADMIN_PASSWORD = "wyciek12"


def get_client_ip():
    """Pobiera prawdziwe IP klienta (obsługuje proxy Render)."""
    if request.headers.getlist("X-Forwarded-For"):
        ip = request.headers.get("X-Forwarded-For").split(",")[0].strip()
        return ip
    return request.remote_addr


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
        "ip": ip or get_client_ip(),
        "query": query
    }
    logs.append(entry)
    LOGS_FILE.write_text(json.dumps(logs, indent=2, ensure_ascii=False), encoding="utf-8")


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "name": "Cold Search Premium API",
        "version": "2.1",
        "status": "online"
    })


@app.route("/auth", methods=["POST"])
def auth():
    data = request.json
    key = data.get("key")
    if not key:
        return jsonify({"success": False, "message": "Brak klucza"}), 400

    client_ip = get_client_ip()
    result = license_manager.validate_license(key, client_ip)
    save_log("auth", key=key, ip=client_ip)
    return jsonify(result)


@app.route("/search", methods=["POST"])
def search():
    data = request.json
    key = data.get("key")
    query = data.get("query")
    if not key or not query:
        return jsonify({"success": False, "message": "Brak danych"}), 400

    client_ip = get_client_ip()
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


# === PANEL ADMINA ===

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
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', system-ui, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 20px;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
        }
        header {
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            border-bottom: 1px solid var(--border);
        }
        h1 {
            font-size: 2.2rem;
            background: linear-gradient(90deg, var(--primary), #a663cc);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
            margin-bottom: 8px;
        }
        .form-group {
            background: var(--card-bg);
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }
        h2 {
            margin-bottom: 15px;
            color: var(--primary);
        }
        input, button {
            width: 100%;
            padding: 12px;
            margin: 8px 0;
            border: none;
            border-radius: 8px;
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
        button:hover { opacity: 0.9; }
        .license-item {
            background: #25253a;
            padding: 15px;
            border-radius: 10px;
            margin: 10px 0;
            border-left: 4px solid var(--success);
        }
        .license-item.expired { border-left-color: var(--danger); }
        .license-item.revoked { border-left-color: var(--warning); }
        .license-key {
            font-family: monospace;
            font-weight: bold;
            word-break: break-all;
            margin-bottom: 8px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }
        th {
            color: var(--primary);
            font-weight: 600;
        }
        tr:hover {
            background: rgba(67, 97, 238, 0.1);
        }
        .status-active { color: var(--success); }
        .status-expired { color: var(--danger); }
        .status-revoked { color: var(--warning); }
        .logout-link {
            display: inline-block;
            margin-top: 15px;
            color: var(--danger);
            text-decoration: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Cold Search Premium — Admin Panel</h1>
            {% if authenticated %}
                <p>Zalogowany jako administrator</p>
                <a href="/admin" class="logout-link">→ Wyloguj się (odśwież stronę)</a>
            {% endif %}
        </header>

        {% if not authenticated %}
            <div class="form-group">
                <h2>🔒 Logowanie</h2>
                <form method="POST" action="/admin">
                    <input type="password" name="password" placeholder="Hasło panelu" required>
                    <button type="submit">Zaloguj się</button>
                </form>
                {% if error %}
                    <p style="color: var(--danger); margin-top: 10px;">Nieprawidłowe hasło.</p>
                {% endif %}
            </div>
        {% else %}
            <!-- Generowanie klucza -->
            <div class="form-group">
                <h2>➕ Wygeneruj nowy klucz</h2>
                <form method="POST" action="/admin/generate">
                    <input type="hidden" name="password" value="{{ password }}">
                    <label for="days">Ważność (dni):</label>
                    <input type="number" id="days" name="days" min="0" value="7" placeholder="Liczba dni (0 = bezterminowy)" required>
                    <small style="color:#aaa;">Wpisz 0 dla klucza bezterminowego</small><br><br>
                    <button type="submit">Generuj klucz premium</button>
                </form>
                {% if new_key %}
                    <div style="background:#162e22; padding:15px; border-radius:8px; margin-top:15px; color:var(--success);">
                        <strong>Nowy klucz:</strong><br>
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
                    <input type="hidden" name="password" value="{{ password }}">
                    <input type="text" name="key" placeholder="Klucz do zablokowania..." required>
                    <button type="submit" style="background:var(--danger);">Zablokuj klucz</button>
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


@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    password = request.form.get("password") if request.method == "POST" else request.args.get("password")
    
    if password == ADMIN_PASSWORD:
        logs = load_logs()
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return render_template_string(
            ADMIN_TEMPLATE,
            authenticated=True,
            password=ADMIN_PASSWORD,
            licenses=license_manager.licenses,
            logs=logs,
            new_key=None,
            now=now
        )
    else:
        return render_template_string(
            ADMIN_TEMPLATE,
            authenticated=False,
            error=(request.method == "POST")
        )


@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    password = request.form.get("password")
    if password != ADMIN_PASSWORD:
        return redirect("/admin")

    days_str = request.form.get("days", "7")
    try:
        days = int(days_str)
        if days < 0:
            days = 0
    except ValueError:
        days = 7

    key = license_manager.generate_key(valid_days=days if days > 0 else None)
    expiry = license_manager.licenses[key].get("expiry", "Bezterminowa")
    
    logs = load_logs()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    return render_template_string(
        ADMIN_TEMPLATE,
        authenticated=True,
        password=ADMIN_PASSWORD,
        licenses=license_manager.licenses,
        logs=logs,
        new_key=key,
        expiry=expiry,
        now=now
    )


@app.route("/admin/revoke", methods=["POST"])
def admin_revoke():
    password = request.form.get("password")
    if password != ADMIN_PASSWORD:
        return redirect("/admin")

    key = request.form.get("key")
    if key:
        license_manager.revoke_key(key)
    return redirect("/admin")


if __name__ == "__main__":
    app.run(debug=True)
