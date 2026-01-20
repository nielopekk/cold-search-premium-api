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
app.secret_key = os.getenv("FLASK_SECRET_KEY", "cold_search_ultra_2026_fixed")

# === LOGIKA SYSTEMOWA ===
def log_activity(message):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    try:
        with LOGS_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def load_activity_logs():
    if not LOGS_FILE.exists():
        return []
    try:
        return LOGS_FILE.read_text(encoding="utf-8").strip().split("\n")
    except:
        return []

# === IMPORT Z ZIP (SKIPOWANIE DUPLIKATÓW) ===
def import_leaks_worker(zip_url):
    log_activity(f"📥 Start importu: {zip_url}")
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
            
            total_added = 0
            batch = []
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
                except:
                    continue
            
            if batch:
                requests.post(f"{SUPABASE_URL}/rest/v1/leaks", headers=import_headers, json=batch)
                total_added += len(batch)
            
            log_activity(f"✅ Zakończono import. Przetworzono: {total_added} linii.")
    except Exception as e:
        log_activity(f"❌ Błąd importu: {str(e)}")

# === ZARZĄDZANIE LICENCJAMI ===
class LicenseManager:
    def generate(self, days):
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
            if not data:
                return {"success": False, "message": "Klucz nie istnieje"}
            
            lic = data[0]
            expiry = datetime.fromisoformat(lic["expiry"].replace('Z', '+00:00'))
            
            if not lic["active"] or datetime.now(timezone.utc) > expiry:
                return {"success": False, "message": "Klucz wygasł"}
            
            if not lic.get("ip"):
                requests.patch(f"{SUPABASE_URL}/rest/v1/licenses?key=eq.{key}", headers=SUPABASE_HEADERS, json={"ip": ip})
                return {"success": True, "message": "HWID powiązane"}
            
            if lic["ip"] != ip:
                return {"success": False, "message": "Inne hWID przypisane"}
                
            return {"success": True, "message": "OK"}
        except Exception as e:
            log_activity(f"⚠️ Błąd walidacji klucza: {e}")
            return {"success": False, "message": "Błąd bazy danych"}

lic_mgr = LicenseManager()

# === UI DASHBOARD (PEŁNY HTML Z NOWYMI SEKCJAMI) ===
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cold Search | Premium Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: #00f2ff;
            --secondary: #bc13fe;
            --bg: #0a0a12;
            --card-bg: rgba(15, 15, 25, 0.7);
            --border: rgba(255, 255, 255, 0.08);
            --text: #eaeaff;
            --text-muted: #8888aa;
            --success: #00ffaa;
            --danger: #ff3366;
            --warning: #ffcc00;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            background-image:
                radial-gradient(circle at 10% 20%, rgba(0, 242, 255, 0.05) 0%, transparent 20%),
                radial-gradient(circle at 90% 80%, rgba(188, 19, 254, 0.05) 0%, transparent 20%);
            padding: 20px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        /* === LOGIN === */
        .login-card {
            max-width: 420px;
            margin: 120px auto;
            background: var(--card-bg);
            padding: 40px;
            border-radius: 24px;
            border: 1px solid var(--border);
            backdrop-filter: blur(12px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
        }
        .login-card h2 {
            text-align: center;
            margin-bottom: 30px;
            font-weight: 800;
            font-size: 1.8rem;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .login-card input {
            width: 100%;
            padding: 16px;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--border);
            border-radius: 14px;
            color: white;
            font-family: 'JetBrains Mono';
            margin-bottom: 20px;
            font-size: 1rem;
        }
        .login-card button {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: #000;
            font-weight: 700;
            border: none;
            border-radius: 14px;
            cursor: pointer;
            font-size: 1.05rem;
            transition: transform 0.2s, opacity 0.2s;
        }
        .login-card button:hover {
            transform: translateY(-2px);
            opacity: 0.95;
        }

        /* === HEADER === */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 24px 32px;
            background: var(--card-bg);
            border-radius: 24px;
            border: 1px solid var(--border);
            margin-bottom: 32px;
            backdrop-filter: blur(12px);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.3);
        }
        .logo {
            font-size: 1.8rem;
            font-weight: 800;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .btn-logout {
            padding: 10px 24px;
            background: transparent;
            border: 1px solid var(--danger);
            color: var(--danger);
            border-radius: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-logout:hover {
            background: rgba(255, 51, 102, 0.1);
        }

        /* === STATS === */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 24px;
            margin-bottom: 32px;
        }
        .stat-card {
            background: var(--card-bg);
            padding: 28px;
            border-radius: 20px;
            border: 1px solid var(--border);
            display: flex;
            flex-direction: column;
        }
        .stat-label {
            font-size: 0.9rem;
            color: var(--text-muted);
            margin-bottom: 8px;
        }
        .stat-value {
            font-size: 2.2rem;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
            color: white;
        }

        /* === MAIN LAYOUT === */
        .main-layout {
            display: grid;
            grid-template-columns: 1fr;
            gap: 28px;
        }
        @media (min-width: 992px) {
            .main-layout {
                grid-template-columns: 380px 1fr;
            }
        }

        .card {
            background: var(--card-bg);
            padding: 32px;
            border-radius: 24px;
            border: 1px solid var(--border);
            backdrop-filter: blur(10px);
        }
        .card h3 {
            font-size: 1.3rem;
            margin-bottom: 24px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .card h3 svg {
            width: 20px;
            height: 20px;
            fill: var(--primary);
        }

        input[type="number"],
        input[type="url"] {
            width: 100%;
            padding: 14px;
            background: rgba(0, 0, 0, 0.35);
            border: 1px solid var(--border);
            border-radius: 12px;
            color: white;
            font-family: 'JetBrains Mono';
            margin-bottom: 18px;
            font-size: 1rem;
        }

        .btn-primary {
            width: 100%;
            padding: 15px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: #000;
            font-weight: 700;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-size: 1rem;
            transition: transform 0.2s, opacity 0.2s;
        }
        .btn-primary:hover {
            transform: translateY(-2px);
            opacity: 0.95;
        }

        .btn-secondary {
            background: linear-gradient(135deg, var(--secondary), #8a00d4);
        }

        .generated-key {
            margin-top: 20px;
            padding: 16px;
            background: rgba(0, 0, 0, 0.4);
            border: 1px dashed var(--primary);
            border-radius: 12px;
            text-align: center;
            font-family: 'JetBrains Mono';
            font-size: 1.1rem;
            color: var(--primary);
            word-break: break-all;
        }

        /* === TABLE === */
        .table-container {
            overflow-x: auto;
            border-radius: 20px;
            border: 1px solid var(--border);
            background: var(--card-bg);
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-family: 'JetBrains Mono';
            font-size: 0.95rem;
        }
        th {
            padding: 16px;
            text-align: left;
            color: var(--text-muted);
            font-weight: 600;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid var(--border);
        }
        td {
            padding: 16px;
            border-bottom: 1px solid var(--border);
        }
        tr:last-child td {
            border-bottom: none;
        }
        .status-active {
            color: var(--success);
            background: rgba(0, 255, 170, 0.1);
            padding: 4px 10px;
            border-radius: 8px;
            font-weight: 600;
        }
        .status-expired {
            color: var(--danger);
            background: rgba(255, 51, 102, 0.1);
            padding: 4px 10px;
            border-radius: 8px;
            font-weight: 600;
        }

        /* === RECENT SEARCHES === */
        .recent-searches {
            grid-column: 1 / -1;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 24px;
        }
        .recent-searches h3 {
            margin-bottom: 20px;
        }
        .search-item {
            display: grid;
            grid-template-columns: 1fr 2fr 1fr 100px;
            gap: 12px;
            padding: 12px 0;
            border-bottom: 1px solid var(--border);
            font-family: 'JetBrains Mono';
            font-size: 0.9rem;
        }
        .search-item:last-child {
            border-bottom: none;
        }
        .search-key { color: var(--primary); }
        .search-query { color: white; }
        .search-ip { color: #aaa; }
        .search-time { color: var(--text-muted); }

        /* === LOGS === */
        .logs-card {
            grid-column: 1 / -1;
            background: rgba(0, 0, 0, 0.4);
            border-radius: 20px;
            padding: 24px;
            height: 280px;
            overflow-y: auto;
            border: 1px solid var(--border);
            font-family: 'JetBrains Mono';
            font-size: 12px;
            color: #aaa;
        }
        .log-line {
            margin-bottom: 6px;
            line-height: 1.4;
        }
        .log-timestamp {
            color: var(--primary);
            margin-right: 8px;
        }
        .log-action {
            color: var(--text);
        }
        .log-error {
            color: var(--danger);
        }
        .log-success {
            color: var(--success);
        }

        /* SCROLLBAR */
        ::-webkit-scrollbar {
            width: 8px;
        }
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.2);
        }
    </style>
</head>
<body>
    <div class="container">
        {% if not authenticated %}
            <div class="login-card">
                <h2>🔐 Secure Login</h2>
                <form method="POST" action="/admin/login">
                    <input type="password" name="password" placeholder="Wprowadź hasło administratora" required autofocus autocomplete="off">
                    <button type="submit">ZALOGUJ SIĘ</button>
                </form>
            </div>
        {% else %}
            <header>
                <div class="logo">❄️ Cold Search Premium</div>
                <button class="btn-logout" onclick="location.href='/admin/logout'">WYLOGUJ</button>
            </header>

            <!-- BAZA I LICENCJE -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">REKORDY W BAZIE</div>
                    <div class="stat-value">{{ "{:,}".format(db_count).replace(",", " ") }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">AKTYWNE LICENCJE</div>
                    <div class="stat-value">{{ active_keys }}</div>
                </div>
            </div>

            <!-- STATYSTYKI WYSZUKIWAŃ -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">WYSZUKAŃ OGÓŁEM</div>
                    <div class="stat-value">{{ "{:,}".format(total_searches).replace(",", " ") }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">UNIKALNE IP</div>
                    <div class="stat-value">{{ "{:,}".format(unique_ips).replace(",", " ") }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">WYSZUKAŃ DZIŚ</div>
                    <div class="stat-value">{{ "{:,}".format(searches_today).replace(",", " ") }}</div>
                </div>
            </div>

            <div class="main-layout">
                <div class="card">
                    <h3>
                        <svg viewBox="0 0 24 24"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/></svg>
                        Nowa Licencja
                    </h3>
                    <form method="POST" action="/admin/generate">
                        <input type="number" name="days" value="30" min="1" placeholder="Liczba dni ważności">
                        <button type="submit" class="btn-primary">GENERUJ KLUCZ</button>
                    </form>
                    {% if new_key and "Error" not in new_key %}
                        <div class="generated-key">{{ new_key }}</div>
                    {% elif new_key %}
                        <div style="margin-top:15px; color:var(--danger); font-size:0.9rem;">⚠️ {{ new_key }}</div>
                    {% endif %}

                    <h3 style="margin-top: 36px;">
                        <svg viewBox="0 0 24 24"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
                        Import Bazy ZIP
                    </h3>
                    <form method="POST" action="/admin/import_zip">
                        <input type="url" name="zip_url" placeholder="https://example.com/data.zip" required>
                        <button type="submit" class="btn-primary btn-secondary">ROZPOCZNIJ IMPORT</button>
                    </form>
                </div>

                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Klucz</th>
                                <th>Status</th>
                                <th>IP</th>
                                <th>Pozostało</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for lic in licenses %}
                            <tr>
                                <td style="color:var(--primary); font-weight:600;">{{ lic.key }}</td>
                                <td>
                                    {% if lic.is_active %}
                                        <span class="status-active">Aktywny</span>
                                    {% else %}
                                        <span class="status-expired">Wygasły</span>
                                    {% endif %}
                                </td>
                                <td>{{ lic.ip or '—' }}</td>
                                <td>{{ lic.time_left }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <!-- OSTANIE WYSZUKIWANIA -->
                <div class="recent-searches">
                    <h3>🔍 Ostatnie wyszukiwania</h3>
                    {% if recent_searches %}
                        {% for s in recent_searches %}
                        <div class="search-item">
                            <div class="search-key">{{ s.key }}</div>
                            <div class="search-query">{{ s.query }}</div>
                            <div class="search-ip">{{ s.ip }}</div>
                            <div class="search-time">{{ s.time }}</div>
                        </div>
                        {% endfor %}
                    {% else %}
                        <div style="color:var(--text-muted); font-style:italic;">Brak ostatnich wyszukiwań</div>
                    {% endif %}
                </div>

                <!-- AKTYWNOŚĆ SYSTEMOWA -->
                <div class="logs-card">
                    {% for line in logs[-50:] | reverse %}
                        {% set parts = line.split('] ', 1) %}
                        {% if parts|length == 2 %}
                            <div class="log-line">
                                <span class="log-timestamp">{{ parts[0][1:] }}</span>
                                <span class="log-action">
                                    {% if "✅" in parts[1] or "Zalogowano" in parts[1] or "Start importu" in parts[1] %}
                                        <span class="log-success">{{ parts[1] }}</span>
                                    {% elif "❌" in parts[1] or "Błąd" in parts[1] or "⚠️" in parts[1] %}
                                        <span class="log-error">{{ parts[1] }}</span>
                                    {% else %}
                                        {{ parts[1] }}
                                    {% endif %}
                                </span>
                            </div>
                        {% else %}
                            <div class="log-line">{{ line }}</div>
                        {% endif %}
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
    if not session.get("logged_in"):
        return render_template_string(ADMIN_HTML, authenticated=False)

    # --- Liczba rekordów w leaks ---
    db_count = 0
    try:
        r = requests.head(f"{SUPABASE_URL}/rest/v1/leaks", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"})
        db_count = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
    except:
        pass

    # --- Liczba aktywnych licencji ---
    licenses = []
    active_keys = 0
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/licenses", headers=SUPABASE_HEADERS, params={"order": "created_at.desc"})
        now = datetime.now(timezone.utc)
        for l in r.json():
            exp = datetime.fromisoformat(l["expiry"].replace('Z', '+00:00'))
            active = l["active"] and now < exp
            if active:
                active_keys += 1
            licenses.append({
                "key": l["key"],
                "ip": l.get("ip"),
                "is_active": active,
                "time_left": "Aktualny"
            })
    except:
        pass

    # --- STATYSTYKI WYSZUKIWAŃ ---
    total_searches = 0
    unique_ips = 0
    searches_today = 0
    try:
        # Całkowita liczba
        r = requests.head(f"{SUPABASE_URL}/rest/v1/search_logs", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"})
        total_searches = int(r.headers.get("content-range", "0-0/0").split("/")[-1])

        # Unikalne IP
        r = requests.get(f"{SUPABASE_URL}/rest/v1/search_logs", headers=SUPABASE_HEADERS, params={
            "select": "ip",
            "distinct": "true"
        })
        unique_ips = len(r.json())

        # Wyszukiwania dzisiaj
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r = requests.head(f"{SUPABASE_URL}/rest/v1/search_logs", headers={**SUPABASE_HEADERS, "Prefer": "count=exact"}, params={
            "timestamp": f"gte.{today}T00:00:00Z"
        })
        searches_today = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
    except Exception as e:
        log_activity(f"⚠️ Błąd ładowania statystyk: {e}")

    # --- OSTANIE WYSZUKIWANIA ---
    recent_searches = []
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/search_logs",
            headers=SUPABASE_HEADERS,
            params={
                "order": "timestamp.desc",
                "limit": 10,
                "select": "key,query,ip,timestamp"
            }
        )
        for item in r.json():
            ts = datetime.fromisoformat(item["timestamp"].replace('Z', '+00:00'))
            recent_searches.append({
                "key": item["key"],
                "query": item["query"],
                "ip": item["ip"],
                "time": ts.strftime("%H:%M:%S")
            })
    except Exception as e:
        log_activity(f"⚠️ Błąd ładowania ostatnich wyszukiwań: {e}")

    return render_template_string(
        ADMIN_HTML,
        authenticated=True,
        db_count=db_count,
        licenses=licenses,
        active_keys=active_keys,
        logs=load_activity_logs(),
        new_key=session.pop("new_key", None),
        total_searches=total_searches,
        unique_ips=unique_ips,
        searches_today=searches_today,
        recent_searches=recent_searches
    )

@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("password") == ADMIN_PASSWORD:
        session["logged_in"] = True
        log_activity("Zalogowano do panelu")
    return redirect("/admin")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    log_activity("Wylogowano z panelu")
    return redirect("/admin")

@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    if not session.get("logged_in"):
        return redirect("/admin")
    days = int(request.form.get("days", 30))
    key = lic_mgr.generate(days)
    session["new_key"] = key
    log_activity(f"Nowy klucz wygenerowany: {key} (ważność: {days} dni)")
    return redirect("/admin")

@app.route("/admin/import_zip", methods=["POST"])
def admin_import_zip():
    if not session.get("logged_in"):
        return redirect("/admin")
    url = request.form.get("zip_url")
    threading.Thread(target=import_leaks_worker, args=(url,), daemon=True).start()
    log_activity(f"Rozpoczęto import w tle: {url}")
    return redirect("/admin")

@app.route("/auth", methods=["POST"])
def api_auth():
    d = request.json or {}
    key = d.get("key")
    ip = d.get("client_ip")
    result = lic_mgr.validate(key, ip)
    return jsonify(result)

@app.route("/search", methods=["POST"])
def api_search():
    d = request.json or {}
    key = d.get("key")
    query = d.get("query", "")
    ip = d.get("client_ip")

    auth = lic_mgr.validate(key, ip)
    if not auth["success"]:
        return jsonify(auth), 403

    # Zapisz log wyszukiwania
    try:
        log_payload = {
            "key": key,
            "query": query[:200],
            "ip": ip,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        requests.post(
            f"{SUPABASE_URL}/rest/v1/search_logs",
            headers=SUPABASE_HEADERS,
            json=log_payload
        )
    except Exception as e:
        log_activity(f"⚠️ Nie udało się zapisać logu wyszukiwania: {e}")

    # Wyszukaj dane
    params = {"data": f"ilike.%{query}%", "select": "source,data", "limit": 150}
    r = requests.get(f"{SUPABASE_URL}/rest/v1/leaks", headers=SUPABASE_HEADERS, params=params)
    return jsonify({"success": True, "results": r.json()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
