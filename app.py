#!/usr/bin/env python3
# app.py — Flask web app wrapper for your PH Real-Time Earthquake Monitor
# Serves a dashboard that fetches USGS + PHIVOLCS on each request (or you can cache externally).
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
from jinja2 import Template
from bs4 import BeautifulSoup

# Configuration via environment variables (set these on Render / locally)
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
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "120"))  # seconds, used in meta refresh

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

APP_AUTHOR = os.getenv("APP_AUTHOR", "Eduardson Cortez")  # your name inserted here

# ---- Helper functions (adapted from your original script) ----
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
    headers = {"User-Agent": "PH-EQ-Monitor/1.0 (+https://example.com)"}
    for url in PHIVOLCS_JSON_CANDIDATES:
        try:
            r = requests.get(url, timeout=10, headers=headers)
            r.raise_for_status()
            try:
                data = r.json()
                if isinstance(data, dict) and "features" in data:
                    for f in data.get("features", []):
                        props = f.get("properties", {})
                        geom = f.get("geometry", {})
                        coords = geom.get("coordinates") or []
                        if len(coords) >= 2:
                            events.append({
                                "id": f.get("id", "") or props.get("id",""),
                                "mag": props.get("mag"),
                                "place": props.get("place"),
                                "time": props.get("time"),
                                "lat": coords[1],
                                "lon": coords[0],
                                "depth": coords[2] if len(coords) > 2 else None,
                                "source": "PHIVOLCS"
                            })
                    return events
                elif isinstance(data, list):
                    for item in data:
                        ev = {}
                        ev["id"] = item.get("id", "")
                        ev["mag"] = item.get("magnitude") or item.get("mag")
                        ev["place"] = item.get("location") or item.get("place")
                        ev["time"] = item.get("time") or item.get("timestamp")
                        ev["lat"] = item.get("lat")
                        ev["lon"] = item.get("lon")
                        ev["depth"] = item.get("depth")
                        ev["source"] = "PHIVOLCS"
                        if ev["lat"] and ev["lon"]:
                            events.append(ev)
                    if events:
                        return events
            except ValueError:
                pass

            soup = BeautifulSoup(r.text, "html.parser")
            text = soup.get_text(separator="\n")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            for ln in lines:
                if "M " in ln or "Magnitude" in ln or "Mag" in ln:
                    parts = ln.split()
                    mag = None
                    for i, p in enumerate(parts):
                        if p in ("M", "Mag", "Magnitude") and i+1 < len(parts):
                            try:
                                mag = float(parts[i+1].replace(',', ''))
                                break
                            except:
                                continue
                    nums = [p for p in parts if any(c.isdigit() for c in p) and '.' in p]
                    if mag and len(nums) >= 1:
                        ev = {
                            "id": f"ph_{hash(ln)}",
                            "mag": mag,
                            "place": " / ".join(parts[-4:]) if len(parts) >= 4 else ln[:40],
                            "time": None,
                            "lat": None,
                            "lon": None,
                            "depth": None,
                            "source": "PHIVOLCS"
                        }
                        events.append(ev)
            if events:
                return events

        except Exception as ex:
            current_app.logger.debug("PHIVOLCS fetch/parse error for %s: %s", url, ex)
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
    existing_signatures = set()
    for e in combined:
        sig = (round(float(e["lat"]) if e["lat"] else 0, 2),
               round(float(e["lon"]) if e["lon"] else 0, 2),
               round(float(e["mag"]) if e["mag"] else 0, 1))
        existing_signatures.add(sig)
    for e in phivolcs_events:
        sig = (round(float(e["lat"]) if e.get("lat") else 0, 2),
               round(float(e["lon"]) if e.get("lon") else 0, 2),
               round(float(e.get("mag") if e.get("mag") else 0, 1)))
        if sig not in existing_signatures:
            combined.append(e)
            existing_signatures.add(sig)
    combined.sort(key=lambda x: x.get("time") or 0, reverse=True)
    return combined

def build_map(events):
    # Create map and save to static/ph_map.html
    m = folium.Map(location=[12.8797, 121.7740], zoom_start=5, tiles="OpenStreetMap")
    for e in events:
        try:
            mag = float(e.get("mag") or 0)
        except:
            mag = 0
        color = "red" if mag >= 6 else "orange" if mag >= 4 else "blue"
        popup = "<b>Magnitude:</b> {}<br><b>Place:</b> {}<br><b>Depth:</b> {} km<br><b>Time:</b> {}".format(
            e.get("mag","N/A"),
            e.get("place","N/A"),
            e.get("depth","N/A"),
            datetime.utcfromtimestamp(e["time"]/1000).strftime("%Y-%m-%d %H:%M:%S UTC") if e.get("time") else "N/A"
        )
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
    if "time" in df.columns and df["time"].notnull().any():
        df = df[df["time"].notnull()]
        df['dt'] = pd.to_datetime(df['time'], unit='ms', utc=True)
        df = df.sort_values('dt')
    else:
        df['dt'] = pd.to_datetime(datetime.utcnow())
    if len(df) > max_points:
        df = df.tail(max_points)
    plt.figure(figsize=(8,3.2))
    plt.plot(df['dt'], df['mag'].astype(float), marker='o', linewidth=1)
    plt.title('Recent Philippine Earthquakes — Magnitude over Time')
    plt.xlabel('Time (UTC)')
    plt.ylabel('Magnitude')
    plt.grid(True, linestyle='--', linewidth=0.4, alpha=0.7)
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
    if 'time' in df.columns:
        df['Time (UTC)'] = pd.to_datetime(df['time'], unit='ms', utc=True).dt.strftime('%Y-%m-%d %H:%M:%S')
    else:
        df['Time (UTC)'] = ""
    df['Magnitude'] = df['mag']
    df['Place'] = df['place']
    df['Depth (km)'] = df['depth']
    df['Lat'] = df['lat']
    df['Lon'] = df['lon']
    out = df[['Magnitude','Place','Depth (km)','Time (UTC)','Lat','Lon']].head(max_rows)
    return out.to_html(classes="quake-table", index=False, border=0, justify='left')

def load_logged_ids():
    if not os.path.exists(LOG_FILE):
        return set()
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def log_event(eid):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(eid + "\n")

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=8)
        return resp.status_code == 200
    except Exception as ex:
        current_app.logger.warning("Telegram send error: %s", ex)
        return False

def check_and_alert(events):
    logged = load_logged_ids()
    for e in events:
        eid = e.get("id") or f"{e.get('source')}_{e.get('lat')}_{e.get('lon')}_{e.get('time')}"
        try:
            mag = float(e.get("mag") or 0)
        except:
            mag = 0
        if eid not in logged and mag >= ALERT_MAGNITUDE:
            tstr = datetime.utcfromtimestamp(e['time']/1000).strftime('%Y-%m-%d %H:%M:%S UTC') if e.get('time') else "N/A"
            msg = f"🚨 EARTHQUAKE ALERT 🚨\nMag: {mag}\nLocation: {e.get('place')}\nDepth: {e.get('depth')} km\nTime: {tstr}\n(Source: {e.get('source')})"
            current_app.logger.info("Alert: %s", msg)
            sent = send_telegram(msg)
            if sent:
                current_app.logger.info("Telegram alert sent.")
            log_event(eid)
            logged.add(eid)

# ---- Flask app ----
from flask import Flask
app = Flask(__name__, static_folder="static", template_folder="templates")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)

@app.route("/")
def index():
    # fetch & build data (simple synchronous fetch — fine for moderate use)
    usgs = fetch_usgs()
    us_events = extract_usgs_events(usgs)
    ph_events = fetch_phivolcs()
    all_events = merge_events(us_events, ph_events)
    # build map and save to static
    map_path = build_map(all_events)
    chart_b64 = build_trend_img(all_events)
    table_html = build_table_html(all_events)
    # check and send alerts (non-blocking consideration: small)
    try:
        check_and_alert(all_events)
    except Exception as ex:
        app.logger.warning("Alert check error: %s", ex)
    updated = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    plotted = min(len(all_events), 30)
    return render_template("dashboard.html",
                           map_file="/" + map_path,
                           chart_b64=chart_b64,
                           table_html=table_html,
                           updated=updated,
                           refresh_seconds=REFRESH_INTERVAL,
                           refresh_minutes=REFRESH_INTERVAL//60,
                           plotted=plotted,
                           author=APP_AUTHOR)

if __name__ == "__main__":
    # for local dev
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
