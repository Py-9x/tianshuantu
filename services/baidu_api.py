import requests
import datetime
import re
import time
from config import BAIDU_AK


def geocode(address):
    url = f"https://api.map.baidu.com/geocoding/v3/?address={address}&output=json&ak={BAIDU_AK}"
    try:
        resp = requests.get(url, timeout=8).json()
        if resp.get("status") == 0:
            loc = resp["result"]["location"]
            return loc["lng"], loc["lat"]
    except Exception:
        pass
    return None, None


def get_weather(lng, lat):
    # Baidu weather expects location as "lng,lat".
    url = f"https://api.map.baidu.com/weather/v1/?location={lng},{lat}&output=json&ak={BAIDU_AK}"
    try:
        resp = requests.get(url, timeout=8).json()
        if resp.get("status") == 0:
            return resp["result"].get("now")
    except Exception:
        pass
    return None


def get_current_weather(lat, lon):
    """
    Returns a normalized weather dict:
    {"temperature": xx, "text": "...", "wind": "..."}
    """
    try:
        now = get_weather(lon, lat)
        if not isinstance(now, dict):
            return None
        wind = f"{now.get('wind_dir', '')}{now.get('wind_class', '')}".strip()
        return {
            "temperature": now.get("temp"),
            "text": now.get("text", ""),
            "wind": wind or now.get("wind_dir", ""),
        }
    except Exception:
        return None


def get_weather_forecast(lng, lat, with_raw=False):
    # Baidu Weather API expects "lng,lat" in location.
    url = f"https://api.map.baidu.com/weather/v1/?location={lng},{lat}&data_type=all&ak={BAIDU_AK}"
    resp = None

    def _to_float(v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except Exception:
            return None

    def _to_int(v):
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except Exception:
            return None

    def _normalize_date(v):
        s = str(v or "").strip()
        if not s:
            return ""
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
        if m:
            try:
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
            except Exception:
                pass
        m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
        if m:
            try:
                y = datetime.date.today().year
                return datetime.date(y, int(m.group(1)), int(m.group(2))).strftime("%Y-%m-%d")
            except Exception:
                pass
        return s[:10]

    for attempt in range(1, 4):
        try:
            resp = requests.get(url, timeout=5).json()
            if resp.get("status") == 0:
                forecasts = resp.get("result", {}).get("forecasts", [])
                normalized = []
                for day in forecasts:
                    # Adapt to multiple potential field names.
                    text_day = day.get("text_day") or day.get("text") or day.get("weather_day") or "未知"
                    low = _to_float(day.get("low"))
                    if low is None:
                        low = _to_float(day.get("temp_min"))
                    high = _to_float(day.get("high"))
                    if high is None:
                        high = _to_float(day.get("temp_max"))
                    wd_day = day.get("wd_day") or day.get("wind_dir_day") or day.get("wind_dir") or ""
                    wc_day = day.get("wc_day") or day.get("wind_scale_day") or day.get("wind_class") or ""
                    precip = day.get("precip")
                    if precip is None:
                        precip = day.get("precipitation")
                    humidity = day.get("humidity")
                    if humidity is None:
                        humidity = day.get("hum")

                    normalized.append(
                        {
                            "date": _normalize_date(day.get("date")),
                            "text_day": text_day,
                            "low": low,
                            "high": high,
                            "wd_day": wd_day,
                            "wc_day": wc_day,
                            "precip": _to_float(precip),
                            "humidity": _to_int(humidity),
                        }
                    )
                return (normalized, resp) if with_raw else normalized
        except Exception:
            pass
        if attempt < 3:
            time.sleep(0.8)

    return (None, resp) if with_raw else None


def static_map_url(lng, lat, zoom=12, width=800, height=400, scale=2, map_type=2):
    # Free tier commonly rejects large size when scale=2. Clamp safely.
    scale = 2 if int(scale) == 2 else 1
    max_side = 512 if scale == 2 else 1024
    width = max(10, min(int(width), max_side))
    height = max(10, min(int(height), max_side))
    return (
        "https://api.map.baidu.com/staticimage/v2"
        f"?ak={BAIDU_AK}&center={lng},{lat}&width={width}&height={height}"
        f"&zoom={zoom}&scale={scale}&markers={lng},{lat}&markerStyles=l,A,0xFF0000"
        f"&copyright=1"
    )


def js_map_html(lng, lat, zoom=14, height=400, map_type=1, marker=True,
                circle_radius=0, circle_color="#10B981", circle_fill="rgba(16,185,129,0.25)"):
    """
    生成百度地图JavaScript API交互式地图HTML。
    map_type: 1=普通, 2=卫星, 3=地形(卫星+路网叠加)
    支持真实的卫星图/地形图切换（静态图API不支持）。
    可选：在中心点绘制安全圈（circle_radius > 0 时启用，单位：米）
    """
    # 地图类型映射为百度JS API常量
    type_map = {
        1: "BMAP_NORMAL_MAP",
        2: "BMAP_SATELLITE_MAP",
        3: "BMAP_HYBRID_MAP",   # 卫星+路网
    }
    js_map_type = type_map.get(int(map_type), "BMAP_NORMAL_MAP")
    
    # 安全圈JS代码
    circle_js = ""
    if circle_radius and circle_radius > 0:
        circle_js = f"""
        var circle = new BMap.Circle(center, {int(circle_radius)}, {{
            strokeColor: "{circle_color}",
            strokeWeight: 3,
            strokeOpacity: 0.8,
            fillColor: "{circle_fill}",
            fillOpacity: 0.35
        }});
        map.addOverlay(circle);
        """
    
    # 标记点JS
    marker_js = ""
    if marker:
        marker_js = """
        var marker = new BMap.Marker(center);
        map.addOverlay(marker);
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            html,body,#map{{ margin:0;padding:0;width:100%;height:{height}px; }}
            #map{{ border-radius:12px; }}
        </style>
        <script src="https://api.map.baidu.com/api?v=3.0&ak={BAIDU_AK}"></script>
    </head>
    <body>
        <div id="map"></div>
        <script>
            var map = new BMap.Map("map");
            var center = new BMap.Point({lng}, {lat});
            map.centerAndZoom(center, {zoom});
            map.enableScrollWheelZoom(true);
            map.setMapType({js_map_type});
            {marker_js}
            {circle_js}
        </script>
    </body>
    </html>
    """
    return html


def static_map_with_path(lng_list, lat_list, width=800, height=300):
    if not lng_list or not lat_list or len(lng_list) != len(lat_list):
        return None

    path_points = [f"{lng},{lat}" for lng, lat in zip(lng_list, lat_list)]
    center_idx = len(path_points) // 2
    center = path_points[center_idx]
    path_text = "|".join(path_points)
    start = path_points[0]
    end = path_points[-1]

    return (
        "https://api.map.baidu.com/staticimage/v2"
        f"?ak={BAIDU_AK}&center={center}&zoom=12&width={width}&height={height}&scale=2"
        f"&paths={path_text}&pathStyles=0xFF0000,4,0.85"
        f"&markers={start}|{end}&markerStyles=l,A,0xFF0000|l,B,0x2563EB"
    )


def get_static_map_image(lng, lat, zoom=14):
    url = static_map_url(lng, lat, zoom=zoom)
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", ""):
            return resp.content
    except Exception:
        pass
    return None


def reverse_geocode(lng, lat):
    url = f"https://api.map.baidu.com/reverse_geocoding/v3/?ak={BAIDU_AK}&output=json&coordtype=wgs84ll&location={lat},{lng}"
    try:
        resp = requests.get(url, timeout=8).json()
        if resp.get("status") == 0:
            return resp.get("result", {}).get("formatted_address")
    except Exception:
        pass
    return ""


def get_elevation_open_meteo(lat, lng):
    """
    Query real elevation (meters) from Open-Meteo Elevation API.
    Docs: https://open-meteo.com/en/docs/elevation-api
    Returns int meters or None on failure.
    """
    url = "https://api.open-meteo.com/v1/elevation"
    try:
        resp = requests.get(
            url,
            params={"latitude": lat, "longitude": lng},
            timeout=5,
        ).json()
    except Exception:
        return None

    # API commonly returns {"elevation":[xxx]} or {"elevation": xxx}
    elevation = resp.get("elevation")
    if isinstance(elevation, list) and elevation:
        elevation = elevation[0]
    try:
        if elevation is None:
            return None
        return int(float(elevation))
    except Exception:
        return None


def get_path_map_image(path_or_lng_list, lat_list=None):
    """
    Backward-compatible signature:
    1) get_path_map_image(path) where path=[{"lon":..., "lat":...}, ...]
    2) get_path_map_image(lng_list, lat_list)
    """
    if lat_list is not None:
        lng_values = path_or_lng_list or []
        lat_values = lat_list or []
        if not lng_values or not lat_values:
            return None
        url = static_map_with_path(lng_values, lat_values, width=900, height=450)
    else:
        path = path_or_lng_list
        if not path:
            return None
        lng_values = [p["lon"] for p in path]
        lat_values = [p["lat"] for p in path]
        url = static_map_with_path(lng_values, lat_values, width=900, height=450)

    if not url:
        return None
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", ""):
            return resp.content
    except Exception:
        pass
    return None
