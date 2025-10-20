# app.py (Modified fetch_phivolcs for new PHIVOLCS URL structure)

# Import 're' for regular expressions (needed for robust search)
import re 
# Tiyakin na ang 're' ay kasama sa imports sa itaas ng app.py:
# from bs4 import BeautifulSoup, re # (Kung hindi kasama, isama mo ito)
# Kung hindi gumana, dagdag mo lang: import re

def fetch_phivolcs():
    """Fetches and scrapes PHIVOLCS data using a more robust selector."""
    events = []
    headers = {"User-Agent": "PH-EQ-Monitor/1.0"}
    try:
        # Pinalitan: verify=False para i-ignore ang SSL error
        r = requests.get(PHIVOLCS_HTML_URL, timeout=15, headers=headers, verify=False) 
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        # HAKBANG 1: Hanapin ang table gamit ang header text sa ibabaw nito (Hal: "OCTOBER 2025")
        # I-assume natin na ang table ay kasunod ng element na may header text
        # Hanapin ang <strong> tag na may text ng buwan
        
        # Gumamit ng regular expression para hanapin ang text na naglalaman ng buong column header (e.g., 'Date - Time')
        # Ito ay mas siguradong paraan kaysa sa class name.
        
        # Subukan hanapin ang <th> tag na may text na "Date - Time (Philippine Time)"
        header_cell = soup.find('th', string=re.compile(r'Date - Time \(Philippine Time\)'))
        
        if header_cell:
            # HAKBANG 2: Akyatin ang DOM para mahanap ang <table> na magulang
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
                    dt_obj_pst = datetime.strptime(dt_str_to_parse, '%d %B %Y %I:%M %p').replace(tzinfo=PST)
                    
                    # I-convert sa UTC time at Epoch
                    dt_obj_utc = dt_obj_pst.astimezone(timezone.utc)
                    epoch_time_ms = int(dt_obj_utc.timestamp() * 1000)
                    
                    # I-extract ang ibang data: (Cols 1, 2, 3, 4, 5, 6 sa table)
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

# I-re-confirm mo lang na ang line na ito ay kasama sa iyong imports:
# from datetime import datetime, timedelta, timezone 
# import re
