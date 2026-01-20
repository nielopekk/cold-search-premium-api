# app.py
from flask import Flask, request, jsonify, render_template_string
import json
import os
from datetime import datetime
from pathlib import Path
from license_manager import LicenseManager

app = Flask(__name__)

LEAKS_DIR = Path("leaks")
LOGS_FILE = Path("logs.json")
LICENSE_FILE = Path("licenses.json")

# Inicjalizacja
LEAKS_DIR.mkdir(exist_ok=True)
if not LOGS_FILE.exists():
    LOGS_FILE.write_text("[]", encoding="utf-8")

license_manager = LicenseManager()

# 🔒 HASŁO PANELU ADMINA — NA STAŁE
ADMIN_PASSWORD = "wyciek12"


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
        "ip": ip or request.remote_addr,
        "query": query
    }
    logs.append(entry)
    LOGS_FILE.write_text(json.dumps(logs, indent=2, ensure_ascii=False), encoding="utf-8")


# === PUBLICZNE ENDPOINTY ===

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "name": "Cold Search Premium API",
        "status": "online"
    })

@app.route("/auth", methods=["POST"])
def auth():
    data = request.json
    key = data.get("key")
    if not key:
        return jsonify({"success": False, "message": "Brak klucza"}), 400

    client_ip = request.remote_addr
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

    client_ip = request.remote_addr
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


# === PANEL ADMINA (BEZ SESJI) ===

ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <title>Cold Search Admin Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: 'Segoe UI', Tahoma, sans-serif; background: #111; color: #eee; margin: 0; padding: 20px; }
        .container { max-width: 1000px; margin: auto; background: #222; padding: 25px; border-radius: 10px; box-shadow: 0 0 15px rgba(0,0,0,0.7); }
        h1, h2 { color: #4fc3f7; margin-bottom: 15px; }
        input, button { padding: 10px; margin: 6px 0; width: 100%; border: none; border-radius: 5px; font-size: 16px; }
        input[type="password"] { background: #333; color: white; }
        button { background: #29b6f6; color: black; font-weight: bold; cursor: pointer; }
        button:hover { opacity: 0.9; }
        .key-box { background: #333; padding: 12px; margin: 10px 0; border-radius: 6px; word-break: break-all; }
        .log-entry { background: #333; padding: 10px; margin: 8px 0; border-left: 4px solid #66bb6a; font-size: 14px; }
        .log-denied { border-left-color: #ef5350; }
        .success { color: #66bb6a; }
        .error { color: #ef5350; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #444; }
        th { color: #4fc3f7; }
        tr:hover { background: #2a2a2a; }
        pre { background: #000; padding: 10px; border-radius: 5px; overflow-x: auto; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔐 Cold Search Admin Panel</h1>

        {% if not authenticated %}
            <form method="POST" action="/admin">
                <input type="password" name="password" placeholder="Hasło panelu" required>
                <button type="submit">Zaloguj się</button>
            </form>
            {% if error %}
                <p class="error">❌ Nieprawidłowe hasło.</p>
            {% endif %}
        {% else %}
            <p><strong>✅ Zalogowany</strong> | <a href="/admin" style="color:#ff7043;">Wyloguj → odśwież stronę</a></p>

            <!-- Generowanie klucza -->
            <h2>➕ Wygeneruj nowy klucz premium</h2>
            <form method="POST" action="/admin/generate">
                <input type="hidden" name="password" value="{{ password }}">
                <button type="submit">Generuj klucz</button>
            </form>
            {% if new_key %}
                <div class="key-box success">✅ Nowy klucz: <strong>{{ new_key }}</strong></div>
            {% endif %}

            <!-- Zablokuj klucz -->
            <h2>🗑️ Zablokuj klucz</h2>
            <form method="POST" action="/admin/revoke">
                <input type="hidden" name="password" value="{{ password }}">
                <input type="text" name="key" placeholder="Wklej klucz do zablokowania..." required>
                <button type="submit">Zablokuj</button>
            </form>

            <!-- Licencje -->
            <h2>📋 Aktywne licencje</h2>
            {% for key, data in licenses.items() %}
                <div class="key-box">
                    <strong>{{ key }}</strong><br>
                    Status: {% if data.active %}✅ Aktywny{% else %}❌ Zablokowany{% endif %}<br>
                    Przypisany do IP: <strong>{{ data.ip or "— nieaktywowany —" }}</strong>
                </div>
            {% endfor %}

            <!-- Logi -->
            <h2>📜 Logi aktywności klientów (ostatnie 200 wpisów)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Czas (UTC)</th>
                        <th>Typ</th>
                        <th>IP klienta</th>
                        <th>Klucz</th>
                        <th>Zapytanie</th>
                    </tr>
                </thead>
                <tbody>
                    {% for log in logs[-200:] | reverse %}
                    <tr class="{% if log.event == 'search_denied' %}log-denied{% else %}log-entry{% endif %}">
                        <td>{{ log.timestamp[:19] }}</td>
                        <td>{{ log.event }}</td>
                        <td><strong>{{ log.ip }}</strong></td>
                        <td>{{ log.key or "—" }}</td>
                        <td>{{ log.query or "—" }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>

            <!-- Wyczyść logi -->
            <form method="POST" action="/admin/clear_logs" style="margin-top:20px;">
                <input type="hidden" name="password" value="{{ password }}">
                <button type="submit" style="background:#ef5350;" onclick="return confirm('Na pewno wyczyścić WSZYSTKIE logi?')">Wyczyść logi</button>
            </form>
        {% endif %}
    </div>
</body>
</html>
"""


@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    password = None
    if request.method == "POST":
        password = request.form.get("password")
    else:
        # GET — próbujemy pobrać z URL (dla wygody)
        password = request.args.get("password")

    if password == ADMIN_PASSWORD:
        logs = load_logs()
        return render_template_string(
            ADMIN_TEMPLATE,
            authenticated=True,
            password=ADMIN_PASSWORD,
            licenses=license_manager.licenses,
            logs=logs,
            new_key=None
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

    key = license_manager.generate_key()
    logs = load_logs()
    return render_template_string(
        ADMIN_TEMPLATE,
        authenticated=True,
        password=ADMIN_PASSWORD,
        licenses=license_manager.licenses,
        logs=logs,
        new_key=key
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


@app.route("/admin/clear_logs", methods=["POST"])
def admin_clear_logs():
    password = request.form.get("password")
    if password != ADMIN_PASSWORD:
        return redirect("/admin")

    LOGS_FILE.write_text("[]", encoding="utf-8")
    return redirect("/admin")


if __name__ == "__main__":
    app.run(debug=True)