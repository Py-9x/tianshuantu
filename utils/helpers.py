import numpy as np
import time
import hashlib
import math

def geocode(place):
    known_places = {
        "四姑娘山": {"lat": 31.11, "lon": 102.90},
        "珠穆朗玛峰": {"lat": 27.99, "lon": 86.93},
        "冈仁波齐": {"lat": 31.07, "lon": 81.31}
    }
    for k, v in known_places.items():
        if k in place:
            return v
    return {"lat": 30.0 + np.random.uniform(-2, 2), "lon": 100.0 + np.random.uniform(-5, 5)}

def get_current_location():
    return {"lat": 31.11 + np.random.uniform(-0.01, 0.01), "lon": 102.90 + np.random.uniform(-0.01, 0.01)}

def generate_vitals(prev_hr, prev_spo2, prev_temp, target_hr=None, target_spo2=None, target_temp=None):
    if target_hr is not None:
        hr = prev_hr * 0.9 + target_hr * 0.1 + np.random.normal(0, 0.5)
        spo2 = prev_spo2 * 0.9 + target_spo2 * 0.1 + np.random.normal(0, 0.2)
        temp = prev_temp * 0.9 + target_temp * 0.1 + np.random.normal(0, 0.1)
    else:
        hr = prev_hr + np.random.normal(0, 1.5)
        spo2 = prev_spo2 + np.random.normal(0, 0.5)
        temp = prev_temp + np.random.normal(0, 0.1)

    hr = float(np.clip(hr, 40, 180))
    spo2 = float(np.clip(spo2, 60, 100))
    temp = float(np.clip(temp, 32, 41))

    return {"hr": round(hr, 1), "spo2": round(spo2, 1), "temp": round(temp, 1)}

def summarize_corridor(points, focus_idx=None):
    if not points:
        return None
    if focus_idx is not None and 0 <= focus_idx < len(points):
        focus_node = points[focus_idx]
    else:
        focus_node = min(points, key=lambda pt: pt["distance_m"])
    drop_estimate = abs(focus_node["risk_delta"])
    basis = " / ".join(focus_node["causes"])
    return {
        "overall_level": focus_node["risk_level"],
        "drop_estimate": drop_estimate,
        "basis": basis,
        "action": focus_node["next_move"],
        "action_node_id": focus_node["id"]
    }

def build_risk_corridor(heading_deg, corridor_length_km, narrative, base_location, current_risk_score):
    lat = base_location.get("lat", 27.99)
    lon = base_location.get("lon", 86.93)
    heading_rad = math.radians(heading_deg)
    lat_rad = math.radians(lat)
    seed = int(hashlib.sha256(f"{lat}-{lon}-{heading_deg}-{narrative}".encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)

    path_coords = []
    points = []
    steps = 4
    for idx in range(steps + 1):
        dist_km = corridor_length_km * idx / steps
        delta_lat = (dist_km / 111) * math.cos(heading_rad)
        delta_lon = (dist_km / (111 * max(math.cos(lat_rad), 0.2))) * math.sin(heading_rad)
        lat_pt = lat + delta_lat
        lon_pt = lon + delta_lon
        path_coords.append([lon_pt, lat_pt])

        if idx == 0:
            continue

        severity = float(np.clip(
            current_risk_score + rng.normal(0.04 * idx, 0.08),
            0.05,
            0.98
        ))
        risk_level = "高" if severity > 0.65 else ("中" if severity > 0.35 else "低")
        causes = rng.choice(["冰裂缝", "碎石滑坡", "积雪暗沟", "风寒叠加", "低能见度"], size=2, replace=False)
        risk_delta = -abs(float(rng.uniform(0.08, 0.25)))
        detour_m = int(rng.integers(80, 220))
        points.append({
            "id": f"RK-{idx}",
            "label": f"{int(dist_km * 1000)} m · {risk_level}风险",
            "lat": lat_pt,
            "lon": lon_pt,
            "distance_m": int(dist_km * 1000),
            "severity": severity,
            "risk_level": risk_level,
            "uncertainty": round(float(rng.uniform(0.18, 0.45)), 2),
            "causes": causes.tolist(),
            "next_move": f"{int(dist_km * 1000)} m 处右切缓坡，控制速度",
            "explanation": f"{narrative or '当前路线'} → 叠加卫星热区，建议提前右切绕坡，避开热区。",
            "fallback_move": f"B 方案：向右平移 {detour_m} m 切入背风凹地，再折返主路",
            "risk_delta": risk_delta,
            "cost_benefit": f"代价：侧移 {detour_m} m / 收益：风险下降 {abs(int(risk_delta * 100))}%",
            "color": [220, 38, 38, 180] if severity > 0.65 else ([249, 115, 22, 180] if severity > 0.35 else [34, 197, 94, 180]),
            "radius": 140 + int(severity * 220)
        })

    return {
        "heading": heading_deg,
        "path": path_coords,
        "points": points,
        "summary": summarize_corridor(points)
    }
