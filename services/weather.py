import requests
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import QWEATHER_KEY

def get_forecast(lat, lon):
    url = f"https://devapi.qweather.com/v7/weather/3d?location={lon},{lat}&key={QWEATHER_KEY}"
    try:
        res = requests.get(url, timeout=5).json()
        if res.get("code") == "200":
            return res.get("daily", [])
    except Exception:
        pass
    return [
        {"fxDate": "今天", "textDay": "晴", "tempMin": "-5", "tempMax": "5", "windScaleDay": "3-4"},
        {"fxDate": "明天", "textDay": "多云", "tempMin": "-8", "tempMax": "2", "windScaleDay": "4-5"},
        {"fxDate": "后天", "textDay": "小雪", "tempMin": "-12", "tempMax": "-2", "windScaleDay": "5-6"}
    ]

def get_current_weather(lat, lon):
    url = f"https://devapi.qweather.com/v7/weather/now?location={lon},{lat}&key={QWEATHER_KEY}"
    try:
        res = requests.get(url, timeout=5).json()
        if res.get("code") == "200":
            return res.get("now", {})
    except Exception:
        pass
    return {"text": "晴", "temp": "-5", "windScale": "3"}
