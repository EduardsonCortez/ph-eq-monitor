#!/usr/bin/env python3
# app.py — Flask web app wrapper for your PH Real-Time Earthquake Monitor
import os
import time
# I-check ang imports: dapat kasama ang timedelta at timezone
from datetime import datetime, timedelta, timezone 
from flask import Flask, render_template, send_from_directory, current_app
import requests
import folium
import pandas as pd
import matplotlib.pyplot as plt
import io
import base64
from bs4 import BeautifulSoup 
import re # I-IMPORT ITO PARA SA MAS MATIBAY NA PAGHAHANAP

# I-import ito para sa InsecureRequestWarning, na lalabas dahil sa verify=False
import urllib3 
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning) 

# Configuration via environment variables
USGS_FEED_URL = os.getenv("USGS_FEED_URL",
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson")

# NEW: PHIVOLCS URL na i-scrape (Ito ang tamang URL na nahanap natin)
PHIVOLCS_HTML_URL = "https://earthquake.phivolcs.dost.gov.ph/"


# Bounding box para sa Pilipinas
PH_LAT_MIN, PH_LAT_MAX = 4.5, 21.5
PH_LON_MIN, PH_LON_MAX = 116.0, 127.5

MAP_STATIC_PATH = os.path.join("static", "ph_map.html")
LOG_FILE = os.getenv("LOG_FILE", "earthquake_log.txt")
ALERT_MAGNITUDE = float(os.getenv("ALERT_MAGNITUDE", "5.0"))
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "120"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
APP_AUTHOR = os.getenv("APP_AUTHOR", "Eduardson Cortez")

# Timezone object para sa Philippine Standard Time (UTC+8)
PST = timezone(timedelta(hours=8))

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
    """Fetches and scrapes PHIVOLCS data using a more robust selector."""
    events = []
    headers = {"User-Agent": "PH-EQ-Monitor/1.0"}
    try:
        # Pinalitan: verify=False para i-ignore ang SSL error
        r = requests.get(PHIVOLCS_HTML_URL, timeout=15, headers=headers, verify=False) 
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        # HAKBANG 1: Mas matibay na paghahanap ng Table gamit ang header text
        # Hanapin ang <th> tag na may text na "Date - Time (Philippine Time)"
        header_cell = soup.find('th', string=re.compile(r'Date - Time \(Philippine Time\)'))
        
        if header_cell:
            # Akyatin ang DOM para mahanap ang <table> na magulang
            table = header_cell.find_parent('table')
        else:
            table = None
        
        if not table:
            current_app.logger.warning("PHIVOLCS table not found on page. Check HTML structure.")
            return events
            
        rows = table.find_all('tr')
        # Skip header row (index 0)
        for row in rows[1:]:
            cols = row.find_all('td')
            # 6 data columns ang inaasahan natin: Date/Time, Lat, Lon, Depth, Mag, Location
            if len(cols) >= 6: 
                try:
                    # Kumuha ng buong text mula sa <a> tag sa loob ng unang column
                    date_time_full = cols[0].find('a').text.strip()
                    
                    # Split ang 'Date - Time' (e.g. '20 October 2025 - 05:27 PM')
                    parts = date_time_full.split(' - ')
                    if len(parts) != 2: continue # Skip kung mali ang format

                    date_str = parts[0] # Hal: 20 October 2025
                    # I-extract ang time (05:27) at AM/PM
                    time_ampm_parts = parts[1].split(' ')
                    time_str = time_ampm_parts[0] # Hal: 05:27
                    am_pm = time_ampm_parts[1] # Hal: PM

                    # I-reformat ang date/time string para i-parse ng strptime
                    dt_str_to_parse = f"{date_str} {time_str} {am_pm}"
                    
                    # I-parse ang date/time bilang PST (Hal: 20 October 2025 05:27 PM)
                    # Note: '%d %B %Y' for (20 October 2025), '%I:%M %p' for (05:27 PM)
                    dt_obj_pst = datetime.strptime(dt_str_to_parse, '%d %B %Y %I:%M %p').replace(tzinfo=PST)
                    
                    # I-convert sa UTC time at Epoch
                    dt_obj_utc = dt_obj_pst.astimezone(timezone.utc)
                    epoch_time_ms = int(dt_obj_utc.timestamp() * 1000)
                    
                    # I-extract ang ibang data (Base sa 6-column structure)
                    latitude = cols[1].text.strip()
                    longitude = cols[2].text.strip()
                    depth = cols[3].text.strip()
                    magnitude = cols[4].text.strip()
                    region = cols[5].text.strip()
                    
                    # Gawing float ang numeric values
                    mag_float = float(magnitude) if magnitude and magnitude.replace('.', '', 1).isdigit() else None
                    depth_float = float(depth) if depth and depth.replace('.', '', 1).isdigit() else None
                    lat_float = float(latitude) if latitude else None
                    lon_float = float(longitude) if longitude else None

                    if lat_float and lon_float:
                        events.append({
                            "id": f"PHIVOLCS_SCRAPE_{dt_str_to_parse}_{latitude}_{longitude}",
                            "mag": mag_float,
                            "place": region,
                            "time": epoch_time_ms,
                            "lat": lat_float,
                            "lon": lon_float,
                            "depth": depth_float,
                            "source": "PHIVOLCS (Scraped)"
                        })
                except Exception as parse_ex:
                    # current_app.logger.debug(f"PHIVOLCS data row parsing error: {parse_ex} in row: {cols}")
                    continue

        # Tiyakin na ang events ay nasa PH bounding box
        events = [e for e in events if is_in_ph(e.get('lat', 0), e.get('lon', 0))]
        
    except Exception as ex:
        current_app.logger.warning("PHIVOLCS scraping error: %s", ex)
        
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
    # Gumawa ng set para iwasan ang duplicates batay sa location at magnitude
    # Gumamit ng 3 decimal places para maging mas accurate sa pag-deduplicate
    existing = {(round(float(e["lat"]),3), round(float(e["lon"]),3), round(float(e["mag"]),1)) for e in combined if e.get("lat") and e.get("lon")}
    
    for e in phivolcs_events:
        try:
            key = (round(float(e["lat"]),3), round(float(e["lon"]),3), round(float(e["mag"]),1))
            if key not in existing:
                combined.append(e)
                existing.add(key)
        except:
            # Skip invalid event data
            continue 
            
    # I-sort ayon sa pinakabagong oras
    combined.sort(key=lambda x: x.get("time") or 0, reverse=True)
    return combined

def get_marker_style(mag):
    """Kumuha ng radius at color batay sa magnitude"""
    mag = float(mag or 0)
    if mag >= 6.0:
        return {"color": "red", "radius": max(6, 6 + mag*0.8)}
    elif mag >= 5.0:
        return {"color": "darkred", "radius": max(5, 5 + mag*0.7)}
    elif mag >= 4.0:
        return {"color": "orange", "radius": max(4, 4 + mag*0.6)}
    elif mag >= 3.0:
        return {"color": "blue", "radius": max(3, 3 + mag*0.5)}
    else:
        return {"color": "lightblue", "radius": 4}

def build_map(events):
    m = folium.Map(location=[12.8797, 121.7740], zoom_start=5)
    for e in events:
        try:
            mag = float(e.get("mag") or 0)
            style = get_marker_style(mag)
            
            # I-format ang time
            dt_utc = datetime.fromtimestamp(e.get('time') / 1000, tz=timezone.utc)
            time_display = dt_utc.strftime('%Y-%m-%d %H:%M:%S UTC')
            
            popup = f"""
            <b>Magnitude:</b> {mag}<br>
            <b>Place:</b> {e.get('place','N/A')}<br>
            <b>Time:</b> {time_display}<br>
            <b>Source:</b> {e.get('source')}
            """
            
            if e.get("lat") and e.get("lon"):
                folium.CircleMarker(
                    location=[float(e["lat"]), float(e["lon"])],
                    radius=style['radius'],
                    color=style['color'],
                    fill=True,
                    fill_color=style['color'],
                    fill_opacity=0.7,
                    popup=popup
                ).add_to(m)
        except:
            continue
            
    os.makedirs("static", exist_ok=True)
    m.save(MAP_STATIC_PATH)
    return MAP_STATIC_PATH

def build_trend_img(events, max_points=30):
    if not events:
        return ""
    
    # Siguraduhin na 'mag' ay float at 'time' ay integer
    df = pd.DataFrame(events)
    df['mag'] = pd.to_numeric(df['mag'], errors='coerce')
    df['time'] = pd.to_numeric(df['time'], errors='coerce')
    
    # I-filter ang mga row na may valid data
    df = df.dropna(subset=['mag', 'time'])
    
    df['dt'] = pd.to_datetime(df['time'], unit='ms', utc=True)
    
    # I-sort mula sa pinakamatanda hanggang pinakabago para sa plot
    df = df.sort_values(by='dt')
    
    if len(df) > max_points:
        df = df.tail(max_points)
        
    plt.figure(figsize=(8,3.2))
    plt.plot(df['dt'], df['mag'], marker='o', linewidth=1.5, markersize=4)
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
    
    # Tiyakin na 'mag' ay float at 'time' ay integer
    df['mag'] = pd.to_numeric(df['mag'], errors='coerce').round(1)
    df['time'] = pd.to_numeric(df['time'], errors='coerce')
    df['depth'] = pd.to_numeric(df['depth'], errors='coerce').round(1)
    df['lat'] = pd.to_numeric(df['lat'], errors='coerce').round(3)
    df['lon'] = pd.to_numeric(df['lon'], errors='coerce').round(3)

    # I-convert ang time sa readable UTC string
    df['Time (UTC)'] = pd.to_datetime(df['time'], unit='ms', utc=True, errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
    
    df['Magnitude'] = df['mag']
    df['Place'] = df['place']
    df['Depth (km)'] = df['depth']
    df['Lat'] = df['lat']
    df['Lon'] = df['lon']
    
    # I-arrange ang columns
    out = df[['Magnitude','Place','Depth (km)','Time (UTC)','Lat','Lon']].head(max_rows)
    
    # I-convert sa HTML table
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
        # Gumawa ng unique ID kung wala
        eid = e.get("id") or f"{e.get('source')}_{e.get('lat')}_{e.get('lon')}_{e.get('time')}"
        mag = float(e.get("mag") or 0)
        
        # Check kung bagong event at lumampas sa alert magnitude
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
    # 1. Fetch data
    usgs = fetch_usgs()
    us_events = extract_usgs_events(usgs)
    ph_events = fetch_phivolcs() 
    
    # 2. Merge and clean data
    all_events = merge_events(us_events, ph_events)
    
    # 3. Build UI components
    map_path = build_map(all_events)
    chart_b64 = build_trend_img(all_events)
    table_html = build_table_html(all_events)
    
    # 4. Check for alerts (Alert logic)
    alert_trigger = check_and_alert(all_events)  
    
    updated = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    plotted = min(len(all_events), 30)
    
    # 5. Render template
    template_name = "index.html" if alert_trigger else "dashboard.html"
    
    return render_template(
        template_name,
        map_file="/" + map_path,
        chart_b64=chart_b64,
        table_html=table_html,
        updated=updated,
        refresh_seconds=REFRESH_INTERVAL,
        refresh_minutes=REFRESH_INTERVAL//60,
        plotted=plotted,
        author=APP_AUTHOR,
        # Ito ang kailangan ng index.html para i-trigger ang sound
        recent_quake_detected=alert_trigger 
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
