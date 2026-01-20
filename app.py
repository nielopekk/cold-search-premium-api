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

# === KONFIGURACJA SYSTEMU ===
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wcshypmsurncfufbojvp.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_secret_Ci0yyib3FCJW3GMivhX3XA_D2vHmhpP")
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

ADMIN_PASSWORD = "wyciek12"
LOGS_FILE = Path("activity.log")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_2026_ultra_secret_key")

# === LOGI I FUNKCJE POMOCNICZE ===
def log_activity(message):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    try:
        with LOGS_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except: pass

def load_activity_logs():
    if not LOGS_FILE.exists(): return []
    try:
        return LOGS_FILE.read_text(encoding="utf-8").strip().split("\n")
    except: return []

def get_time_left(expiry_str):
    try:
        expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        if now > expiry: return "WYGASŁO"
        diff = expiry - now
        if diff.days > 0: return f"{diff.days}d {diff.seconds // 3600}h"
        return f"{diff.seconds // 3600}h {(diff.seconds // 60) % 60}m"
    except: return "---"

# === LOGIKA BAZY DANYCH I LICENCJI ===
class LicenseManager:
    def validate(self, key, ip):
        try:
            r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
            data = r.json()
            if not data: return {"success": False, "message": "Błędny klucz"}
            lic = data[0]
            expiry = datetime.fromisoformat(lic["expiry"].replace('Z', '+00:00'))
            if not lic.get("active") or datetime.now(timezone.utc) > expiry:
                return {"success": False, "message": "Klucz wygasł lub jest nieaktywny"}
            if not lic.get("ip"):
                requests.patch(f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}", headers=SUPABASE_HEADERS, json={"ip": ip})
                return {"success": True, "message": "IP powiązane"}
            if lic["ip"] != ip: return {"success": False, "message": "Klucz przypisany do innego IP"}
            return {"success": True, "message": "OK"}
        except: return {"success": False, "message": "Błąd serwera bazy"}

    def generate(self, days):
        new_key = "COLD-" + str(uuid.uuid4()).hex.upper()[:12]
        expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        requests.post(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, 
                      json={"key": new_key, "active": True, "expiry": expiry, "created_at": datetime.now(timezone.utc).isoformat()})
        return new_key

lic_mgr = LicenseManager()

# === TEMPLATE UI (HTML + CSS) ===
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Search | Premium Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: #00f2ff; --secondary: #bc13fe; --bg: #050508;
            --card-bg: rgba(15, 15, 25, 0.75); --border: rgba(255, 255, 255, 0.1);
            --text: #e0e0e0; --success: #00ff88; --danger: #ff0055;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background-color: var(--bg);
            background-image: radial-gradient(circle at 10% 20%, rgba(0, 242, 255, 0.05) 0%, transparent 40%),
                              radial-gradient(circle at 90% 80%, rgba(188, 19, 254, 0.05) 0%, transparent 40%);
            color: var(--text); font-family: 'Inter', sans-serif; min-height: 100vh;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 40px 20px; }
        
        header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 30px; padding: 25px; background: var(--card-bg);
            backdrop-filter: blur(15px); border-radius: 24px; border: 1px solid var(--border);
        }
        .logo { font-size: 1.6rem; font-weight: 800; background: linear-gradient(90deg, var(--primary), var(--secondary));
                -webkit-background-clip: text; -webkit-text-fill-color: transparent; }

        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card {
            background: var(--card-bg); backdrop-filter: blur(10px); padding: 30px;
            border-radius: 24px; border: 1px solid var(--border); transition: 0.3s;
        }
        .stat-card:hover { transform: translateY(-5px); border-color: var(--primary); }
        .stat-label { font-size: 0.7rem; color: #888; text-transform: uppercase; letter-spacing: 1.5px; }
        .stat-value { font-size: 2.2rem; font-weight: 700; color: #fff; font-family: 'JetBrains Mono'; margin-top: 10px; }

        .layout { display: grid; grid-template-columns: 400px 1fr; gap: 30px; }
        @media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }

        .card { background: var(--card-bg); backdrop-filter: blur(10px); padding: 30px; border-radius: 24px; border: 1px solid var(--border); }
        h3 { font-size: 0.9rem; margin-bottom: 25px; color: var(--primary); text-transform: uppercase; letter-spacing: 1px; display: flex; align-items: center; gap: 10px; }

        input { width: 100%; background: rgba(0,0,0,0.4); border: 1px solid var(--border); padding: 15px; color: #fff; 
                border-radius: 12px; margin-bottom: 15px; font-family: 'JetBrains Mono'; transition: 0.3s; }
        input:focus { border-color: var(--primary); outline: none; }
        
        button { width: 100%; padding: 16px; border: none; border-radius: 12px; font-weight: 800; cursor: pointer;
                 background: linear-gradient(135deg, var(--primary), var(--secondary)); color: #000; transition: 0.3s; }
        button:hover { filter: brightness(1.1); transform: scale(0.98); }

        .table-wrap { background: var(--card-bg); border-radius: 24px; border: 1px solid var(--border); overflow: hidden; }
        table { width: 100%; border-collapse: collapse; }
        th { padding: 20px; text-align: left; color: #555; font-size: 0.7rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }
        td { padding: 18px 20px; font-size: 0.85rem; border-bottom: 1px solid var(--border); font-family: 'JetBrains Mono'; }
        tr:hover td { background: rgba(255,255,255,0.03); }

        .status { padding: 5px 12px; border-radius: 8px; font-size: 0.7rem; font-weight: 800; }
        .status-active { background: rgba(0, 255, 136, 0.1); color: var(--success); }
        .status-expired { background: rgba(255, 0, 85, 0.1); color: var(--danger); }

        .logs-box { grid-column: 1 / -1; background: #000; border-radius: 24px; padding: 25px; height: 300px;
                    overflow-y: auto; font-family: 'JetBrains Mono'; font-size: 11px; border: 1px solid var(--border); }
        .log-line { margin-bottom: 6px; color: #444; }
        .log-line b { color: var(--primary); }
        
        .logout-btn { background: transparent; border: 1px solid var(--danger); color: var(--danger); width: auto; padding: 10px 20px; }
        .logout-btn:hover { background: var(--danger); color: #fff; }
    </style>
</head>
<body>
    <div class="container">
        {% if not authenticated %}
            <div style="max-width:450px; margin: 150px auto;" class="card">
                <h2 style="text-align:center; margin-bottom:30px; letter-spacing:-1px;">SECURE ACCESS</h2>
                <form method="POST" action="/admin/login">
                    <input type="password" name="password" placeholder="Master Password" required autofocus>
                    <button type="submit">AUTHORIZE SYSTEM</button>
                </form>
            </div>
        {% else %}
            <header>
                <div class="logo">COLD SEARCH PREMIUM</div>
                <a href="/admin/logout"><button class="logout-btn">TERMINATE SESSION</button></a>
            </header>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Baza Rekordów</div>
                    <div class="stat-value">{{ "{:,}".format(db_count).replace(",", " ") }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Aktywne Licencje</div>
                    <div class="stat-value" style="color:var(--primary)">{{ active_keys }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Wszystkie Klucze</div>
                    <div class="stat-value">{{ licenses|length }}</div>
                </div>
            </div>

            <div class="layout">
                <div class="card">
                    <h3>➕ Generuj Licencję</h3>
                    <form method="POST" action="/admin/generate">
                        <label style="font-size:0.65rem; color:#666; display:block; margin-bottom:10px;">DNI WAŻNOŚCI</label>
                        <input type="number" name="days" value="30" min="1">
                        <button type="submit">UTWÓRZ NOWY KLUCZ</button>
                    </form>
                    {% if new_key %}
                        <div style="margin-top:20px; padding:15px; border:1px dashed var(--primary); border-radius:12px; background:rgba(0,242,255,0.05);">
                            <small style="color:var(--primary)">OSTATNI KLUCZ:</small><br>
                            <code style="font-size:1rem; color:#fff;">{{ new_key }}</code>
                        </div>
                    {% endif %}
                </div>

                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>Klucz Licencyjny</th>
                                <th>Status</th>
                                <th>Przypisane IP</th>
                                <th>Wygasa za</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for lic in licenses | reverse %}
                            <tr>
                                <td style="color:var(--primary)">{{ lic.key }}</td>
                                <td>
                                    {% if lic.is_active %}
                                        <span class="status status-active">ACTIVE</span>
                                    {% else %}
                                        <span class="status status-expired">EXPIRED</span>
                                    {% endif %}
                                </td>
                                <td><span style="color:#777">{{ lic.ip if lic.ip else '---' }}</span></td>
                                <td style="font-size:0.8rem;">{{ lic.time_left }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <div class="logs-box">
                    <div style="color:var(--primary); margin-bottom:15px; font-size:0.75rem; font-weight:800;">[ SYSTEM CORE LOGS ]</div>
                    {% for line in logs[-50:] | reverse %}
                        <div class="log-line"><b>></b> {{ line }}</div>
                    {% endfor %}
                </div>
            </div>
        {% endif %}
    </div>
</body>
</html>
"""

# === ROUTING FLASK ===
@app.route("/admin")
def admin_index():
    if not session.get("logged_in"): return render_template_string(ADMIN_HTML, authenticated=False)
    
    db_count = 0
    try:
        r = requests.head(f"{SUPABASE_URL}/rest/v1/leaks", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"})
        db_count = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
    except: pass

    licenses = []
    active_keys = 0
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"order": "created_at.desc"})
        raw_lics = r.json()
        now = datetime.now(timezone.utc)
        for l in raw_lics:
            expiry = datetime.fromisoformat(l["expiry"].replace('Z', '+00:00'))
            is_active = l["active"] and now < expiry
            if is_active: active_keys += 1
            licenses.append({
                "key": l["key"], "ip": l.get("ip"), "is_active": is_active,
                "time_left": get_time_left(l["expiry"])
            })
    except: pass

    return render_template_string(ADMIN_HTML, authenticated=True, db_count=db_count, 
                                 licenses=licenses, active_keys=active_keys, 
                                 logs=load_activity_logs(), new_key=session.pop("new_key", None))

@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True
        log_activity("Admin authorized")
    return redirect("/admin")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin")

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    if not session.get("logged_in"): return redirect("/admin")
    key = lic_mgr.generate(int(request.form.get("days", 30)))
    session["new_key"] = key
    log_activity(f"Generated key: {key}")
    return redirect("/admin")

# --- API DLA KLIENTA ---
@app.route("/auth", methods=["POST"])
def api_auth():
    d = request.json or {}
    return jsonify(lic_mgr.validate(d.get("key"), d.get("client_ip")))

@app.route("/search", methods=["POST"])
def api_search():
    d = request.json or {}
    auth = lic_mgr.validate(d.get("key"), d.get("client_ip"))
    if not auth["success"]: return jsonify(auth), 403
    
    q = d.get("query", "")
    log_activity(f"Search: {q} by {d.get('key')}")
    params = {"data": f"ilike.%{q}%", "select": "source,data", "limit": 150}
    r = requests.get(f"{SUPABASE_URL}/rest/v1/leaks", headers=SUPABASE_HEADERS, params=params)
    return jsonify({"success": True, "results": r.json()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
