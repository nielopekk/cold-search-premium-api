import os
import uuid
import requests
import zipfile
import tempfile
import threading
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

ADMIN_PASSWORD = "wyciek12"
LOGS_FILE = Path("activity.log")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_ultra_pro_2026")

# === POMOCNICZE FUNKCJE LOGIKI ===
def log_activity(message):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    try:
        with LOGS_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except: pass

def load_logs():
    if not LOGS_FILE.exists(): return []
    return LOGS_FILE.read_text(encoding="utf-8").strip().split("\n")[-100:]

# === IMPORT WORKER (W TLE) ===
def import_worker(zip_url):
    log_activity(f"SYS: Start importu z {zip_url}")
    try:
        r = requests.get(zip_url, stream=True, timeout=120)
        r.raise_for_status()
        with tempfile.TemporaryDirectory() as tmp:
            zip_p = Path(tmp) / "data.zip"
            with open(zip_p, "wb") as f:
                for chunk in r.iter_content(8192): f.write(chunk)
            with zipfile.ZipFile(zip_p, "r") as z: z.extractall(tmp)
            
            total = 0
            batch = []
            for p in Path(tmp).rglob("*"):
                if p.is_file() and p.suffix.lower() in [".txt", ".csv", ".log"]:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            clean = line.strip()
                            if clean: batch.append({"data": clean, "source": p.name})
                            if len(batch) >= 1000:
                                requests.post(f"{SUPABASE_URL}/rest/v1/leaks", headers=SUPABASE_HEADERS, json=batch)
                                total += len(batch)
                                batch = []
            if batch: 
                requests.post(f"{SUPABASE_URL}/rest/v1/leaks", headers=SUPABASE_HEADERS, json=batch)
                total += len(batch)
            log_activity(f"SUCCESS: Zaimportowano {total} rekordów.")
    except Exception as e:
        log_activity(f"ERROR: Import nieudany: {e}")

# === PANEL ADMINA (TEMPLATE) ===
ADMIN_UI = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <title>Cold Search PRO | Admin Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Outfit:wght@300;600;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #050508; --card: #0f111a; --primary: #00f2ff; 
            --secondary: #7000ff; --text: #e0e0e0; --danger: #ff2a6d;
        }
        * { margin:0; padding:0; box-sizing:border-box; }
        body { 
            background: var(--bg); color: var(--text); 
            font-family: 'Outfit', sans-serif; overflow-x: hidden;
            background-image: radial-gradient(circle at 50% -20%, #1a1a3a 0%, transparent 50%);
        }
        .sidebar { width: 280px; position: fixed; height: 100vh; background: var(--card); border-right: 1px solid #1a1a2e; padding: 30px; }
        .main { margin-left: 280px; padding: 40px; }
        
        .logo { font-size: 24px; font-weight: 800; background: linear-gradient(90deg, var(--primary), var(--secondary)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 50px; }
        
        .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 40px; }
        .stat-card { background: var(--card); padding: 25px; border-radius: 20px; border: 1px solid #1a1a2e; position: relative; overflow: hidden; }
        .stat-card::after { content:''; position:absolute; top:0; left:0; width:4px; height:100%; background: var(--primary); }
        .stat-label { font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 1px; }
        .stat-value { font-size: 28px; font-weight: 800; margin-top: 10px; font-family: 'JetBrains Mono'; }

        .content-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 30px; }
        .card { background: var(--card); padding: 30px; border-radius: 24px; border: 1px solid #1a1a2e; margin-bottom: 30px; }
        
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th { text-align: left; padding: 15px; color: #555; font-size: 12px; text-transform: uppercase; border-bottom: 1px solid #1a1a2e; }
        td { padding: 15px; border-bottom: 1px solid #1a1a2e; font-family: 'JetBrains Mono'; font-size: 13px; }
        
        input, button { 
            width: 100%; padding: 14px; border-radius: 12px; border: 1px solid #1a1a2e; 
            background: #000; color: #fff; margin-bottom: 15px; font-family: inherit;
        }
        .btn-action { background: linear-gradient(135deg, var(--primary), var(--secondary)); color: #000; font-weight: 800; cursor: pointer; border: none; }
        .btn-action:hover { transform: scale(1.02); }
        
        .badge { padding: 4px 10px; border-radius: 6px; font-size: 10px; font-weight: 800; }
        .badge-active { background: rgba(0, 242, 255, 0.1); color: var(--primary); }
        .badge-expired { background: rgba(255, 42, 109, 0.1); color: var(--danger); }
        
        .logs-box { background: #000; height: 300px; overflow-y: scroll; padding: 20px; font-family: 'JetBrains Mono'; font-size: 11px; border-radius: 12px; color: #888; }
        .log-entry { margin-bottom: 5px; border-left: 2px solid #222; padding-left: 10px; }
    </style>
</head>
<body>
    {% if not authenticated %}
        <div style="display:flex; justify-content:center; align-items:center; height:100vh;">
            <div class="card" style="width:400px; text-align:center;">
                <div class="logo">COLD SEARCH ACCESS</div>
                <form method="POST" action="/admin/login">
                    <input type="password" name="password" placeholder="System Password">
                    <button type="submit" class="btn-action">INITIALIZE SESSION</button>
                </form>
            </div>
        </div>
    {% else %}
        <div class="sidebar">
            <div class="logo">COLD SEARCH PRO</div>
            <div style="color:#444; font-size:12px; margin-bottom:10px;">MAIN MENU</div>
            <div style="margin-bottom:20px;">
                <button onclick="location.href='/admin'" class="btn-action" style="background:#1a1a2e; color:#fff; text-align:left;">Dashboard</button>
                <button onclick="location.href='/admin/logout'" style="background:transparent; color:var(--danger); border:1px solid var(--danger); margin-top:20px;">Terminate</button>
            </div>
        </div>

        <div class="main">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Total Leaks</div>
                    <div class="stat-value">{{ "{:,}".format(stats.db_count) }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Active Keys</div>
                    <div class="stat-value">{{ stats.active_keys }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Searches (24h)</div>
                    <div class="stat-value" style="color:var(--primary);">{{ stats.searches_today }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">API Health</div>
                    <div class="stat-value" style="color:#00ff88;">Stable</div>
                </div>
            </div>

            <div class="content-grid">
                <div class="left-col">
                    <div class="card">
                        <h3>Search Activity (Live)</h3>
                        <canvas id="activityChart" height="100"></canvas>
                    </div>

                    <div class="card">
                        <h3>License Management</h3>
                        <table>
                            <thead>
                                <tr><th>Key</th><th>Bound IP</th><th>Status</th><th>Actions</th></tr>
                            </thead>
                            <tbody>
                                {% for l in licenses %}
                                <tr>
                                    <td style="color:var(--primary)">{{ l.key }}</td>
                                    <td>{{ l.ip or 'NOT_BOUND' }}</td>
                                    <td>
                                        <span class="badge {{ 'badge-active' if l.is_active else 'badge-expired' }}">
                                            {{ 'ACTIVE' if l.is_active else 'EXPIRED' }}
                                        </span>
                                    </td>
                                    <td>
                                        <form method="POST" action="/admin/delete/{{ l.key }}" style="display:inline;">
                                            <button type="submit" style="width:auto; padding:5px 10px; background:var(--danger); border:none; border-radius:4px; font-size:10px; cursor:pointer;">KILL</button>
                                        </form>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="right-col">
                    <div class="card">
                        <h3>Generator</h3>
                        <form method="POST" action="/admin/generate">
                            <input type="number" name="days" value="30" placeholder="Validity Days">
                            <button type="submit" class="btn-action">GENERATE KEY</button>
                        </form>
                        {% if new_key %}<div class="log-entry" style="color:var(--primary); border-color:var(--primary);">KEY: {{ new_key }}</div>{% endif %}
                    </div>

                    <div class="card">
                        <h3>Database Sync</h3>
                        <form method="POST" action="/admin/import_zip">
                            <input type="url" name="zip_url" placeholder="ZIP Cloud URL">
                            <button type="submit" class="btn-action" style="background:var(--secondary); color:#fff;">START SYNC</button>
                        </form>
                    </div>

                    <div class="card">
                        <h3>System Logs</h3>
                        <div class="logs-box">
                            {% for log in logs | reverse %}
                                <div class="log-entry">{{ log }}</div>
                            {% endfor %}
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const ctx = document.getElementById('activityChart').getContext('2d');
            new Chart(ctx, {
                type: 'line',
                data: {
                    labels: {{ stats.chart_labels | safe }},
                    datasets: [{
                        label: 'Searches',
                        data: {{ stats.chart_data | safe }},
                        borderColor: '#00f2ff',
                        backgroundColor: 'rgba(0, 242, 255, 0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: {
                    plugins: { legend: { display: false } },
                    scales: { 
                        y: { display: false },
                        x: { grid: { color: '#1a1a2e' } }
                    }
                }
            });
        </script>
    {% endif %}
</body>
</html>
"""

# === ROUTING ===
@app.route("/admin")
def admin_dashboard():
    if not session.get("logged_in"): return render_template_string(ADMIN_UI, authenticated=False)
    
    # Pobieranie statystyk
    stats = {"db_count": 0, "active_keys": 0, "searches_today": 0, "chart_labels": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"], "chart_data": [12, 19, 3, 5, 2, 3]}
    try:
        r = requests.head(f"{SUPABASE_URL}/rest/v1/leaks", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"})
        stats["db_count"] = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
        
        r_searches = requests.head(f"{SUPABASE_URL}/rest/v1/search_logs", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"})
        stats["searches_today"] = int(r_searches.headers.get("content-range", "0-0/0").split("/")[-1])
    except: pass

    licenses = []
    try:
        r_lic = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"order": "created_at.desc"})
        now = datetime.now(timezone.utc)
        for l in r_lic.json():
            exp = datetime.fromisoformat(l["expiry"].replace('Z', '+00:00'))
            is_active = l["active"] and now < exp
            if is_active: stats["active_keys"] += 1
            licenses.append({"key": l["key"], "ip": l.get("ip"), "is_active": is_active})
    except: pass

    return render_template_string(ADMIN_UI, authenticated=True, stats=stats, licenses=licenses, logs=load_logs(), new_key=session.pop("new_key", None))

@app.route("/admin/login", methods=["POST"])
def do_login():
    if request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True
        log_activity("ADMIN: Session started")
    return redirect("/admin")

@app.route("/admin/logout")
def do_logout():
    session.clear()
    return redirect("/admin")

@app.route("/admin/generate", methods=["POST"])
def do_generate():
    if not session.get("logged_in"): return redirect("/admin")
    days = int(request.form.get("days", 30))
    key = "COLD-" + uuid.uuid4().hex.upper()[:12]
    exp = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    requests.post(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, json={"key": key, "expiry": exp, "active": True})
    session["new_key"] = key
    log_activity(f"KEY_GEN: Created {key} for {days}d")
    return redirect("/admin")

@app.route("/admin/import_zip", methods=["POST"])
def do_import():
    if not session.get("logged_in"): return redirect("/admin")
    url = request.form.get("zip_url")
    threading.Thread(target=import_worker, args=(url,), daemon=True).start()
    return redirect("/admin")

@app.route("/admin/delete/<key>", methods=["POST"])
def do_delete(key):
    if not session.get("logged_in"): return redirect("/admin")
    requests.delete(f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}", headers=SUPABASE_HEADERS)
    log_activity(f"KEY_DEL: Terminated {key}")
    return redirect("/admin")

# === API HANDLERS ===
@app.route("/auth", methods=["POST"])
def api_auth():
    data = request.get_json(force=True)
    key, ip = data.get("key"), data.get("client_ip")
    # Walidacja logiczna (uproszczona)
    r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"key": f"eq.{key}"})
    lics = r.json()
    if not lics: return jsonify({"success": False, "message": "Key not found"})
    lic = lics[0]
    if not lic["active"]: return jsonify({"success": False, "message": "Inactive"})
    if not lic.get("ip"): 
        requests.patch(f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}", headers=SUPABASE_HEADERS, json={"ip": ip})
        return jsonify({"success": True, "message": "IP Locked"})
    if lic["ip"] != ip: return jsonify({"success": False, "message": "IP Mismatch"})
    return jsonify({"success": True, "message": "Authorized"})

@app.route("/search", methods=["POST"])
def api_search():
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if len(query) < 3: return jsonify({"success": False, "results": []})
    
    # Logowanie wyszukiwania
    requests.post(f"{SUPABASE_URL}/rest/v1/search_logs", headers=SUPABASE_HEADERS, json={"key": data.get("key"), "query": query, "ip": data.get("client_ip")})
    
    # Wyszukiwanie
    p = {"data": f"ilike.%{query}%", "select": "source,data", "limit": 100}
    r = requests.get(f"{SUPABASE_URL}/rest/v1/leaks", headers=SUPABASE_HEADERS, params=p)
    return jsonify({"success": True, "results": r.json()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
