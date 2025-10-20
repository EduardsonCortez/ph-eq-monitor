
#!/usr/bin/env python3
# app.py — Flask web app wrapper for your PH Real-Time Earthquake Monitor
import os
import time
from datetime import datetime
from flask import Flask, render_template, send_from_directory, current_app
import requests
import folium
import pandas as pd
import matplotlib.pyplot as plt
import io
import base64
from bs4 import BeautifulSoup

# Configuration via environment variables
USGS_FEED_URL = os.getenv("USGS_FEED_URL",
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson")
PHIVOLCS_JSON_CANDIDATES = [
    "https://earthquake.phivolcs.dost.gov.ph/EQLatestFeed.php",
    "https://earthquake.phivolcs.dost.gov.ph/EQLatest.php"
]

PH_LAT_MIN, PH_LAT_MAX = 4.5, 21.5
PH_LON_MIN, PH_LON_MAX = 116.0, 127.5

MAP_STATIC_PATH = os.path.join("static", "ph_map.html")
LOG_FILE = os.getenv("LOG_FILE", "earthquake_log.txt")
ALERT_MAGNITUDE = float(os.getenv("ALERT_MAGNITUDE", "5.0"))
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "120"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
APP_AUTHOR = os.getenv("APP_AUTHOR", "Eduardson Cortez")

# ---- Helper functions ----
def fetch_usgs():
    try:
        r = requests.get(USGS_FEED_URL, timeout=12)
        r.raise_for_status()
        return r.json()
    except Exception as ex:
        current_app.logger.warning("USGS fetch error: %s", ex)
        return None

def fetch_phivolcs():
    events = []
    headers = {"User-Agent": "PH-EQ-Monitor/1.0"}
    for url in PHIVOLCS_JSON_CANDIDATES:
        try:
            r = requests.get(url, timeout=10, headers=headers)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "features" in data:
                for f in data["features"]:
                    props = f.get("properties", {})
                    geom = f.get("geometry", {})
                    coords = geom.get("coordinates") or []
                    if len(coords) >= 2:
                        events.append({
                            "id": f.get("id", ""),
                            "mag": props.get("mag"),
                            "place": props.get("place"),
                            "time": props.get("time"),
                            "lat": coords[1],
                            "lon": coords[0],
                            "depth": coords[2] if len(coords) > 2 else None,
                            "source": "PHIVOLCS"
                        })
                return events
        except Exception:
            continue
    return events

def is_in_ph(lat, lon):
    try:
        return PH_LAT_MIN <= float(lat) <= PH_LAT_MAX and PH_LON_MIN <= float(lon) <= PH_LON_MAX
    except:
        return False

def extract_usgs_events(usgs_json):
    events = []
    if not usgs_json:
        return events
    for f in usgs_json.get("features", []):
        props = f.get("properties", {})
        geom = f.get("geometry", {})
        coords = geom.get("coordinates") or []
        if len(coords) >= 2:
            lon, lat = coords[0], coords[1]
            if is_in_ph(lat, lon):
                events.append({
                    "id": f.get("id", ""),
                    "mag": props.get("mag"),
                    "place": props.get("place"),
                    "time": props.get("time"),
                    "lat": lat,
                    "lon": lon,
                    "depth": coords[2] if len(coords) > 2 else None,
                    "source": "USGS"
                })
    return events

def merge_events(usgs_events, phivolcs_events):
    combined = usgs_events[:]
    existing = {(round(float(e["lat"]),2), round(float(e["lon"]),2), round(float(e["mag"]),1)) for e in combined if e.get("lat") and e.get("lon")}
    for e in phivolcs_events:
        key = (round(float(e["lat"]),2), round(float(e["lon"]),2), round(float(e["mag"]),1))
        if key not in existing:
            combined.append(e)
            existing.add(key)
    combined.sort(key=lambda x: x.get("time") or 0, reverse=True)
    return combined

def build_map(events):
    m = folium.Map(location=[12.8797, 121.7740], zoom_start=5)
    for e in events:
        mag = float(e.get("mag") or 0)
        color = "red" if mag >= 6 else "orange" if mag >= 4 else "blue"
        popup = f"<b>Magnitude:</b> {mag}<br><b>Place:</b> {e.get('place','N/A')}"
        if e.get("lat") and e.get("lon"):
            folium.CircleMarker(
                location=[float(e["lat"]), float(e["lon"])],
                radius=max(4, 5 + mag),
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.7,
                popup=popup
            ).add_to(m)
    os.makedirs("static", exist_ok=True)
    m.save(MAP_STATIC_PATH)
    return MAP_STATIC_PATH

def build_trend_img(events, max_points=30):
    if not events:
        return ""
    df = pd.DataFrame(events)
    df['dt'] = pd.to_datetime(df['time'], unit='ms', utc=True, errors='coerce')
    df = df.dropna(subset=['dt'])
    if len(df) > max_points:
        df = df.tail(max_points)
    plt.figure(figsize=(8,3.2))
    plt.plot(df['dt'], df['mag'].astype(float), marker='o')
    plt.title('Recent Philippine Earthquakes — Magnitude over Time')
    plt.xlabel('Time (UTC)')
    plt.ylabel('Magnitude')
    plt.grid(True, linestyle='--', linewidth=0.4)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=130)
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')

def build_table_html(events, max_rows=50):
    if not events:
        return ""
    df = pd.DataFrame(events)
    df['Time (UTC)'] = pd.to_datetime(df['time'], unit='ms', utc=True, errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
    df['Magnitude'] = df['mag']
    df['Place'] = df['place']
    df['Depth (km)'] = df['depth']
    df['Lat'] = df['lat']
    df['Lon'] = df['lon']
    out = df[['Magnitude','Place','Depth (km)','Time (UTC)','Lat','Lon']].head(max_rows)
    return out.to_html(classes="quake-table", index=False, border=0)

def load_logged_ids():
    if not os.path.exists(LOG_FILE):
        return set()
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def log_event(eid):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(eid + "\n")

def check_and_alert(events):
    """Return True if a new strong quake detected"""
    logged = load_logged_ids()
    new_alert = False
    for e in events:
        eid = e.get("id") or f"{e.get('source')}_{e.get('lat')}_{e.get('lon')}_{e.get('time')}"
        mag = float(e.get("mag") or 0)
        if eid not in logged and mag >= ALERT_MAGNITUDE:
            new_alert = True
            log_event(eid)
            logged.add(eid)
    return new_alert

# ---- Flask app ----
app = Flask(__name__, static_folder="static", template_folder="templates")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)

@app.route("/")
def index():
    usgs = fetch_usgs()
    us_events = extract_usgs_events(usgs)
    ph_events = fetch_phivolcs()
    all_events = merge_events(us_events, ph_events)
    map_path = build_map(all_events)
    chart_b64 = build_trend_img(all_events)
    table_html = build_table_html(all_events)
    alert_trigger = check_and_alert(all_events)  # True if new quake detected

    updated = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    plotted = min(len(all_events), 30)
    return render_template(
        "dashboard.html",
        map_file="/" + map_path,
        chart_b64=chart_b64,
        table_html=table_html,
        updated=updated,
        refresh_seconds=REFRESH_INTERVAL,
        refresh_minutes=REFRESH_INTERVAL//60,
        plotted=plotted,
        author=APP_AUTHOR,
        alert_trigger=alert_trigger  # 👈 passes to dashboard
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)

