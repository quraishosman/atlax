import requests
from datetime import datetime

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbwGM57AEz65_AJRCzCTCEzO4s5hI5OOE7L4EJiWvZz2jUQGbIUPsDi9fR-zXH3WJTA6/exec"

trade_data = {
    "date": datetime.now().strftime("%Y-%m-%d"),
    "symbol": "XAUUSD",
    "timeframe": "M15",
    "entry": 2415.50,
    "exit": 2420.00,
    "sl": 2410.00,
    "tp": 2420.00,
    "pips": 45,
    "result": "WIN ✅",
    "notes": "Manual Webhook Test"
}

print("Sending test trade to Google Sheets...")
response = requests.post(WEBHOOK_URL, json=trade_data)
print("Status Code:", response.status_code)
print("Response Text:", response.text)
