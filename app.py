import os
import sys
import uuid
import requests
import zipfile
import tempfile
import threading
import logging
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

ADMIN_PASSWORD = "wyciek12"
LOGS_FILE = Path("activity.log")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_ultra_2026_fixed")

# === LOGIKA SYSTEMOWA ===
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

# === IMPORT Z ZIP (SKIPOWANIE DUPLIKATÓW) ===
def import_leaks_worker(zip_url):
    log_activity(f"📥 Start importu: {zip_url}")
    try:
        response = requests.get(zip_url, stream=True, timeout=60)
        response.raise_for_status()
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "data.zip"
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
            
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(tmp_dir)
            
            total_added = 0
            batch = []
            # Nagłówek do skipowania duplikatów w Supabase
            import_headers = {**SUPABASE_HEADERS, "Prefer": "resolution=ignore-duplicates"}

            for file_path in Path(tmp_dir).rglob("*"):
                if not file_path.is_file() or file_path.suffix.lower() not in {".txt", ".csv", ".log"}:
                    continue
                
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            clean_line = line.strip()
                            if clean_line:
                                batch.append({"source": file_path.name, "data": clean_line})
                            
                            if len(batch) >= 1000:
                                requests.post(f"{SUPABASE_URL}/rest/v1/leaks", headers=import_headers, json=batch)
                                total_added += len(batch)
                                batch = []
                except: continue
            
            if batch:
                requests.post(f"{SUPABASE_URL}/rest/v1/leaks", headers=import_headers, json=batch)
                total_added += len(batch)
            
            log_activity(f"✅ Zakończono import. Przetworzono: {total_added} linii.")
    except Exception as e:
        log_activity(f"❌ Błąd importu: {str(e)}")

# === ZARZĄDZANIE LICENCJAMI ===
class LicenseManager:
    def generate(self, days):
        # NAPRAWIONO: uuid.uuid4().hex zamiast str().hex
        new_key = "COLD-" + uuid.uuid4().hex.upper()[:12]
        expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        
        payload = {
            "key": new_key, 
            "active": True, 
            "expiry": expiry, 
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        r = requests.post(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, json=payload)
        return new_key if r.status_code in [200, 201, 204] else f"Error: {r.status_code}"

    def validate(self, key, ip):
        try:
            r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
            data = r.json()
            if not data: return {"success": False, "message": "Klucz nie istnieje"}
            
            lic = data[0]
            expiry = datetime.fromisoformat(lic["expiry"].replace('Z', '+00:00'))
            
            if not lic["active"] or datetime.now(timezone.utc) > expiry:
                return {"success": False, "message": "Klucz wygasł"}
            
            if not lic.get("ip"):
                requests.patch(f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}", headers=SUPABASE_HEADERS, json={"ip": ip})
                return {"success": True, "message": "IP powiązane"}
            
            if lic["ip"] != ip:
                return {"success": False, "message": "Inne IP przypisane"}
                
            return {"success": True, "message": "OK"}
        except:
            return {"success": False, "message": "Błąd bazy danych"}

lic_mgr = LicenseManager()

# === UI DASHBOARD (CSS + HTML) ===
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <title>Cold Search | Premium Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <style>
        :root { --p: #00f2ff; --s: #bc13fe; --bg: #050508; --c: rgba(20, 20, 30, 0.8); --b: rgba(255,255,255,0.1); }
        * { margin:0; padding:0; box-sizing:border-box; }
        body { background: var(--bg); color:#e0e0e0; font-family:'Inter',sans-serif; min-height:100vh; background-image: radial-gradient(circle at 2% 2%, #101025 0%, transparent 50%); }
        .container { max-width: 1400px; margin: 0 auto; padding: 40px 20px; }
        
        header { display:flex; justify-content:space-between; align-items:center; background:var(--c); backdrop-filter:blur(10px); padding:20px 30px; border-radius:20px; border:1px solid var(--b); margin-bottom:30px; }
        .logo { font-size:1.5rem; font-weight:800; background:linear-gradient(90deg, var(--p), var(--s)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
        
        .stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(250px, 1fr)); gap:20px; margin-bottom:30px; }
        .stat-card { background:var(--c); padding:25px; border-radius:20px; border:1px solid var(--b); border-left:4px solid var(--p); }
        .stat-val { font-size:2rem; font-weight:700; font-family:'JetBrains Mono'; color:#fff; display:block; margin-top:5px; }
        
        .layout { display:grid; grid-template-columns: 380px 1fr; gap:25px; }
        .card { background:var(--c); padding:30px; border-radius:24px; border:1px solid var(--b); }
        
        input { width:100%; background:rgba(0,0,0,0.5); border:1px solid var(--b); padding:14px; color:#fff; border-radius:12px; margin-bottom:15px; font-family:'JetBrains Mono'; }
        button { width:100%; padding:15px; border:none; border-radius:12px; font-weight:800; cursor:pointer; background:linear-gradient(135deg, var(--p), var(--s)); color:#000; transition:0.3s; }
        button:hover { transform:scale(0.98); opacity:0.9; }

        table { width:100%; border-collapse:collapse; background:var(--c); border-radius:20px; overflow:hidden; border:1px solid var(--b); }
        th { padding:15px; text-align:left; color:#555; font-size:0.7rem; text-transform:uppercase; border-bottom:1px solid var(--b); }
        td { padding:15px; border-bottom:1px solid var(--b); font-family:'JetBrains Mono'; font-size:0.85rem; }
        .status-active { color: #00ff88; background:rgba(0,255,136,0.1); padding:4px 8px; border-radius:6px; }

        .logs { grid-column:1/-1; background:#000; border-radius:20px; padding:20px; height:250px; overflow-y:auto; border:1px solid var(--b); font-family:'JetBrains Mono'; font-size:11px; color:#666; }
        .log-line b { color: var(--p); }
        .btn-logout { background:transparent; border:1px solid #ff0055; color:#ff0055; width:auto; padding:8px 15px; }
    </style>
</head>
<body>
    <div class="container">
        {% if not authenticated %}
            <div style="max-width:400px; margin:150px auto;" class="card">
                <h2 style="text-align:center; margin-bottom:20px;">SECURE LOGIN</h2>
                <form method="POST" action="/admin/login">
                    <input type="password" name="password" placeholder="Hasło" required autofocus>
                    <button type="submit">ZALOGUJ</button>
                </form>
            </div>
        {% else %}
            <header>
                <div class="logo">COLD SEARCH PREMIUM</div>
                <a href="/admin/logout"><button class="btn-logout">LOGOUT</button></a>
            </header>

            <div class="stats">
                <div class="stat-card">
                    <small style="color:#888">REKORDY W BAZIE</small>
                    <span class="stat-val">{{ "{:,}".format(db_count).replace(",", " ") }}</span>
                </div>
                <div class="stat-card" style="border-left-color: var(--s)">
                    <small style="color:#888">AKTYWNE KLUCZE</small>
                    <span class="stat-val">{{ active_keys }}</span>
                </div>
            </div>

            <div class="layout">
                <div class="card">
                    <h3>➕ NOWA LICENCJA</h3>
                    <form method="POST" action="/admin/generate">
                        <input type="number" name="days" value="30" placeholder="Dni">
                        <button type="submit">GENERUJ</button>
                    </form>
                    {% if new_key %}
                    <div style="margin-top:15px; padding:10px; border:1px dashed var(--p); border-radius:10px; text-align:center;">
                        <code style="color:var(--p)">{{ new_key }}</code>
                    </div>
                    {% endif %}
                    
                    <h3 style="margin-top:30px;">📥 IMPORT BAZY</h3>
                    <form method="POST" action="/admin/import_zip">
                        <input type="url" name="zip_url" placeholder="URL do .zip" required>
                        <button type="submit" style="background:var(--s); color:#fff;">START IMPORT</button>
                    </form>
                </div>

                <div style="overflow-x:auto;">
                    <table>
                        <thead>
                            <tr><th>Klucz</th><th>Status</th><th>IP</th><th>Pozostało</th></tr>
                        </thead>
                        <tbody>
                            {% for lic in licenses %}
                            <tr>
                                <td style="color:var(--p)">{{ lic.key }}</td>
                                <td><span class="status-active">{{ 'OK' if lic.is_active else 'EXP' }}</span></td>
                                <td>{{ lic.ip if lic.ip else '---' }}</td>
                                <td>{{ lic.time_left }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <div class="logs">
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

# === ROUTING ===
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
        now = datetime.now(timezone.utc)
        for l in r.json():
            exp = datetime.fromisoformat(l["expiry"].replace('Z', '+00:00'))
            active = l["active"] and now < exp
            if active: active_keys += 1
            licenses.append({"key": l["key"], "ip": l.get("ip"), "is_active": active, "time_left": "Aktualny"})
    except: pass

    return render_template_string(ADMIN_HTML, authenticated=True, db_count=db_count, 
                                 licenses=licenses, active_keys=active_keys, 
                                 logs=load_activity_logs(), new_key=session.pop("new_key", None))

@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True
        log_activity("Zalogowano do panelu")
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
    log_activity(f"Nowy klucz: {key}")
    return redirect("/admin")

@app.route("/admin/import_zip", methods=["POST"])
def admin_import_zip():
    if not session.get("logged_in"): return redirect("/admin")
    url = request.form.get("zip_url")
    threading.Thread(target=import_leaks_worker, args=(url,)).start()
    return redirect("/admin")

@app.route("/auth", methods=["POST"])
def api_auth():
    d = request.json or {}
    return jsonify(lic_mgr.validate(d.get("key"), d.get("client_ip")))

@app.route("/search", methods=["POST"])
def api_search():
    d = request.json or {}
    auth = lic_mgr.validate(d.get("key"), d.get("client_ip"))
    if not auth["success"]: return jsonify(auth), 403
    
    params = {"data": f"ilike.%{d.get('query')}%", "select": "source,data", "limit": 150}
    r = requests.get(f"{SUPABASE_URL}/rest/v1/leaks", headers=SUPABASE_HEADERS, params=params)
    return jsonify({"success": True, "results": r.json()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
