# PH Earthquake Monitor — Web App
Author: Eduardson Cortez

Simple Flask web dashboard that fetches USGS + PHIVOLCS and shows an interactive Folium map, magnitude trend chart, and recent events table.

## Quick start (local)
1. python -m venv venv
2. source venv/bin/activate   # or venv\Scripts\activate on Windows
3. pip install -r requirements.txt
4. export TELEGRAM_BOT_TOKEN=""   # optional
   export TELEGRAM_CHAT_ID=""
   export APP_AUTHOR="Eduardson Cortez"
5. python app.py
6. Open http://127.0.0.1:5000

## Deploy to Render
1. Push repo to GitHub.
2. Create a new Web Service on Render -> Connect to the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Add Environment Variables on Render:
   - TELEGRAM_BOT_TOKEN (optional)
   - TELEGRAM_CHAT_ID (optional)
   - ALERT_MAGNITUDE (optional) e.g. 5.0
   - REFRESH_INTERVAL (optional) e.g. 120
   - APP_AUTHOR (optional) e.g. "Eduardson Cortez"
