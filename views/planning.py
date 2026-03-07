import datetime
import os
import re
import sys

import streamlit as st

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.db import create_adventure, save_report, get_user_by_id
from services.ai_service import AIService
from services.baidu_api import (
    geocode,
    get_weather_forecast,
    reverse_geocode,
    static_map_url,
    js_map_html,
    get_elevation_open_meteo,
)
import streamlit.components.v1 as components


def _safe_rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _inject_local_css():
    st.markdown(
        """
        <style>
        /* Text Input / Date Input */
        .stTextInput > div > div > input,
        .stDateInput input {
            background: rgba(10, 18, 30, 0.65) !important;
            color: #e2e8f0 !important;
            border: 1px solid rgba(0, 198, 255, 0.22) !important;
            border-radius: 10px !important;
            min-height: 40px !important;
        }

        /* Focus */
        .stTextInput > div > div > input:focus,
        .stDateInput input:focus {
            border-color: #00C6FF !important;
            box-shadow: 0 0 0 1px rgba(0, 198, 255, 0.45), 0 0 16px rgba(0, 198, 255, 0.15) !important;
        }
        .planning-weather-box {
            border: 1px solid rgba(0, 198, 255, 0.15);
            border-radius: 14px;
            padding: 12px;
            text-align: center;
            background: rgba(10, 18, 30, 0.7);
            backdrop-filter: blur(8px);
            height: 100%;
        }
        .planning-weather-date {
            font-weight: 700;
            color: #e2e8f0;
            margin-bottom: 6px;
        }
        .planning-weather-icon {
            font-size: 22px;
            margin-bottom: 4px;
        }
        .planning-risk-item {
            margin-bottom: 6px;
            color: rgba(140, 180, 220, 0.9);
            font-size: 14px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _normalize_date_str(date_str):
    if not date_str:
        return ""
    s = str(date_str).strip()

    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return datetime.date(y, mo, d).strftime("%Y-%m-%d")
        except Exception:
            pass

    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    if m:
        try:
            y = datetime.date.today().year
            mo, d = int(m.group(1)), int(m.group(2))
            return datetime.date(y, mo, d).strftime("%Y-%m-%d")
        except Exception:
            pass

    try:
        return datetime.date.fromisoformat(s[:10]).strftime("%Y-%m-%d")
    except Exception:
        return s[:10]


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


def _filter_forecast_range(forecasts, start_date, end_date):
    selected = []
    for item in forecasts:
        d = _normalize_date_str(item.get("date"))
        if d and start_date <= d <= end_date:
            selected.append(
                {
                    "date": d,
                    "text_day": item.get("text_day", "鏈煡"),
                    "low": _to_float(item.get("low")),
                    "high": _to_float(item.get("high")),
                    "wd_day": item.get("wd_day", ""),
                    "wc_day": item.get("wc_day", ""),
                    "precip": _to_float(item.get("precip")),
                    "humidity": _to_int(item.get("humidity")),
                }
            )
    selected.sort(key=lambda x: x.get("date", ""))
    return selected


def _simulate_forecasts(start_date, end_date):
    """Dynamic fallback weather generation based on date range and season."""
    days = (end_date - start_date).days + 1
    if days <= 0:
        return []

    month = start_date.month
    if month in (12, 1, 2):
        base_low, base_high = -8, 2
    elif month in (3, 4, 5):
        base_low, base_high = 2, 14
    elif month in (6, 7, 8):
        base_low, base_high = 10, 22
    else:
        base_low, base_high = 4, 16

    weather_cycle = ["晴", "多云", "阴", "小雨", "多云", "晴", "阵雨"]
    wind_cycle = [("东北风", "2级"), ("西北风", "3级"), ("东风", "2级"), ("南风", "3级")]

    result = []
    for i in range(days):
        d = start_date + datetime.timedelta(days=i)
        wx = weather_cycle[i % len(weather_cycle)]
        wd, wc = wind_cycle[i % len(wind_cycle)]
        low = base_low + (i % 3) - 1
        high = base_high + (i % 4) - 1
        precip = 0.0 if ("晴" in wx or "多云" in wx) else (1.2 + (i % 3) * 1.4)
        humidity = 40 + (i * 7) % 35
        if "雨" in wx:
            humidity = min(95, humidity + 22)

        result.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "text_day": wx,
                "low": float(low),
                "high": float(high),
                "wd_day": wd,
                "wc_day": wc,
                "precip": float(precip),
                "humidity": int(humidity),
            }
        )
    return result


def _weather_icon(text_day):
    txt = str(text_day or "")
    if "雨" in txt or "雪" in txt:
        return "🌧️"
    if "晴" in txt:
        return "☀️"
    if "阴" in txt:
        return "☁️"
    return "🌤️"


def calculate_gear_risk(weather, bmi, altitude_m):
    """
    计算装备风险与建议。

    公式说明（透明可解释）：
    1) 体感温度（风寒效应，简化公式）：
       AT = Ta - 0.0033 * Va * (33 - Ta)
       - Ta: 空气温度 (°C)
       - Va: 风速 (m/s) —— 从风级（如"3级"）估算为 Va = wind_level * 1.5 (m/s)

    2) 海拔修正：每上升1000m，温度降低约6.5°C（标准大气递减率）
       AT_adj = AT - (altitude_m / 1000.0) * 6.5

    3) BMI 代谢系数：以 BMI=22 为基准系数 1.0，每偏离 1 点调整 0.02
       metab_coef = 1.0 + (bmi - 22.0) * 0.02
       （>1 表示代谢更高、抗寒略强；<1 则抗寒较弱）

    4) 将代谢系数映射为等效温度修正：
       metab_temp_offset = (metab_coef - 1.0) * 3.0  # 每 0.1 的系数差约对应 0.3°C

    5) 最终等效体感温度：
       effective = AT_adj + metab_temp_offset

    输出：dict 包含 risk_level (安全/警告/危险)、badge_color、gaps、suggestion、details
    """
    details = {}
    if not weather:
        return None

    Ta = weather.get("low") if weather.get("low") is not None else weather.get("high")
    try:
        Ta = float(Ta)
    except Exception:
        return None

    # 解析风速（尝试从 wc_day 类似 "3级" 提取数字）
    wc = 0
    wc_str = str(weather.get("wc_day") or "")
    m = re.search(r"(\d+)", wc_str)
    if m:
        try:
            wc = int(m.group(1))
        except Exception:
            wc = 0
    # 估算风速 Va (m/s)：近似映射，wind_level * 1.5
    Va = float(wc) * 1.5

    # 风寒计算（AT）
    AT = Ta - 0.0033 * Va * (33.0 - Ta)

    # 海拔修正
    try:
        alt = float(altitude_m) if altitude_m is not None else 0.0
    except Exception:
        alt = 0.0
    AT_adj = AT - (alt / 1000.0) * 6.5

    # BMI 代谢系数与温度修正
    try:
        bmi_v = float(bmi) if bmi is not None else 22.0
    except Exception:
        bmi_v = 22.0
    metab_coef = 1.0 + (bmi_v - 22.0) * 0.02
    metab_temp_offset = (metab_coef - 1.0) * 3.0

    effective = AT_adj + metab_temp_offset

    # 风险分级阈值（可调整）
    # effective > 0 : 安全; -10 < effective <= 0 : 警告; effective <= -10 : 危险
    if effective <= -10.0:
        risk_level = "危险"
        badge_color = "#EF4444"
    elif effective <= 0.0:
        risk_level = "警告"
        badge_color = "#F59E0B"
    else:
        risk_level = "安全"
        badge_color = "#10B981"

    # 关键缺口与建议（量化）
    gaps = []
    suggestion = ""
    if effective <= -15:
        gaps.append("极端低温：需要高等级保暖与睡袋(-20°C)")
        suggestion = "建议携带羽绒服+保暖手套+防风面罩，睡袋等级≥-20°C，并准备应急热源。"
    elif effective <= -10:
        gaps.append("低温：中等至高等级保暖不足")
        suggestion = "建议携带羽绒服、保暖手套、应急毯；夜间睡袋建议-10°C 级别。"
    elif effective <= -5:
        gaps.append("寒冷：建议增加中间保暖层")
        suggestion = "建议携带抓绒中层与保暖上衣；视夜间温度考虑羽绒外套。"
    elif effective <= 0:
        gaps.append("偏凉：轻保暖建议")
        suggestion = "建议携带中层保暖与防风外套。"
    else:
        gaps.append("无明显保暖缺口")
        suggestion = "当前无需额外高等级保暖装备，关注降水与夜间温差即可。"

    details["Ta"] = Ta
    details["Va_m_s"] = round(Va, 2)
    details["AT_raw"] = round(AT, 2)
    details["AT_altitude_adjusted"] = round(AT_adj, 2)
    details["metab_coef"] = round(metab_coef, 3)
    details["metab_temp_offset"] = round(metab_temp_offset, 2)
    details["effective_temp"] = round(effective, 2)

    return {
        "risk_level": risk_level,
        "badge_color": badge_color,
        "gaps": gaps,
        "suggestion": suggestion,
        "details": details,
    }


def _risk_badge_class(level):
    if level == "高":
        return "badge-red"
    if level == "中":
        return "badge-yellow"
    return "badge-green"


def _estimate_elevation(destination):
    """Rough elevation guess by destination name; returns meters or None."""
    name = str(destination or "")
    known = {
        "四姑娘山": 4500,
        "珠穆朗玛峰": 5200,
        "冈仁波齐": 4700,
        "梅里雪山": 4300,
        "贡嘎山": 3900,
        "黄山": 1200,
        "武功山": 1700,
        "泰山": 900,
    }
    for k, v in known.items():
        if k in name:
            return v
    return None


def _season_from_date(date_str):
    try:
        m = int(str(date_str)[5:7])
    except Exception:
        return "未知季节"
    if m in (12, 1, 2):
        return "冬季"
    if m in (3, 4, 5):
        return "春季"
    if m in (6, 7, 8):
        return "夏季"
    return "秋季"


def _generate_recommendation(query):
    ai_service = AIService()
    try:
        with st.spinner("正在分析最佳出行日期..."):
            rec = ai_service.recommend_trip_window(
                str(query.get("destination") or ""),
                str(query.get("free_start_str") or ""),
                str(query.get("free_end_str") or ""),
                query.get("selected_days") or [],
            )
            if isinstance(rec, dict):
                return rec
    except Exception:
        pass
    return {
        "best_date": str(query.get("free_start_str") or ""),
        "reason": "模型服务暂不可用，建议优先选择降水少、风力弱的日期。",
        "alt_dates": [],
        "high_risk_dates": "",
        "is_mock": True,
    }


def _generate_detail_report(query, trip_date):
    selected_days = query.get("selected_days") or []
    target_day = None
    for day in selected_days:
        if str(day.get("date") or "")[:10] == str(trip_date)[:10]:
            target_day = day
            break
    if not target_day:
        return None

    ai_service = AIService()
    activity_type = "户外探险"
    elevation = query.get("elevation")
    season = _season_from_date(trip_date)
    try:
        with st.spinner("正在生成详细风险报告..."):
            detail = ai_service.generate_trip_detail(
                str(query.get("destination") or ""),
                str(trip_date)[:10],
                target_day,
                window_forecasts=selected_days,
                elevation=elevation,
                activity_type=activity_type,
                season=season,
            )
            if isinstance(detail, dict):
                return detail
    except Exception:
        pass
    return {
        "risk_level": "中",
        "risk_factors": ["天气变化", "地形复杂"],
        "equipment_tips": ["冲锋衣", "保暖层", "头灯", "急救包", "登山鞋"],
        "general_advice": "遇到强风或降水上升时优先保守决策，及时止损撤退。",
        "is_mock": True,
    }


def render():
    _inject_local_css()

    if "map_zoom" not in st.session_state:
        st.session_state.map_zoom = 12
    if "planning_query_result" not in st.session_state:
        st.session_state.planning_query_result = None
    if "selected_date" not in st.session_state:
        st.session_state.selected_date = None
    if "planning_detailed_report" not in st.session_state:
        st.session_state.planning_detailed_report = None
    if "planning_detail_for_date" not in st.session_state:
        st.session_state.planning_detail_for_date = None
    if "show_detailed_report" not in st.session_state:
        st.session_state.show_detailed_report = False

    st.markdown("## 行前规划")

    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    default_start = datetime.date.today() + datetime.timedelta(days=1)
    default_end = default_start + datetime.timedelta(days=3)

    with st.form("planning_form"):
        destination = st.text_input(
            "📍 目的地",
            value=st.session_state.get("last_place", "四姑娘山"),
            placeholder="例如：四姑娘山、珠穆朗玛峰",
        )

        c1, c2 = st.columns(2)
        with c1:
            start_date = st.date_input("📅 空闲开始", value=default_start)
        with c2:
            end_date = st.date_input("📅 空闲结束", value=default_end)

        submit = st.form_submit_button("🔍 查询推荐")
    st.markdown("</div>", unsafe_allow_html=True)

    if submit:
        st.session_state.show_detailed_report = False
        st.session_state.planning_detailed_report = None
        st.session_state.planning_detail_for_date = None

        if not destination.strip():
            st.error("请输入目的地")
            st.session_state.planning_query_result = None
            return
        if end_date < start_date:
            st.error("结束日期不能早于开始日期")
            st.session_state.planning_query_result = None
            return

        today = datetime.date.today()
        if start_date < today or end_date > today + datetime.timedelta(days=6):
            st.warning("天气预报仅支持未来7天，请调整日期范围")
            st.session_state.planning_query_result = None
            return

        st.session_state.last_place = destination.strip()
        try:
            with st.spinner("正在解析目的地坐标..."):
                lng, lat = geocode(destination.strip())
        except Exception:
            lng, lat = None, None

        if lng is None or lat is None:
            lng, lat = 102.9056, 31.1123

        weather_is_mock = False
        try:
            forecasts = get_weather_forecast(lng, lat)
            if not forecasts:
                raise ValueError("empty weather")
        except Exception:
            weather_is_mock = True
            forecasts = _simulate_forecasts(start_date, end_date)

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        selected_days = _filter_forecast_range(forecasts, start_str, end_str)
        if not selected_days:
            weather_is_mock = True
            selected_days = _simulate_forecasts(start_date, end_date)

        try:
            address = reverse_geocode(lng, lat)
        except Exception:
            address = ""

        elevation = get_elevation_open_meteo(lat, lng)
        if elevation is None:
            elevation = _estimate_elevation(destination.strip())

        query = {
            "destination": destination.strip(),
            "elevation": elevation,
            "free_start_str": start_str,
            "free_end_str": end_str,
            "lng": lng,
            "lat": lat,
            "address": address,
            "selected_days": selected_days,
            "weather_is_mock": weather_is_mock,
        }
        query["recommendation"] = _generate_recommendation(query)

        st.session_state.planning_query_result = query
        st.session_state.selected_date = str(query["recommendation"].get("best_date") or start_str)[:10]
        st.session_state.map_zoom = 12

    query = st.session_state.get("planning_query_result")
    if not query:
        return

    selected_days = query.get("selected_days") or []
    recommendation = query.get("recommendation") or {}

    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown("### 🤖 推荐结果")

    best_date = str(recommendation.get("best_date") or query.get("free_start_str") or "")
    reason = str(recommendation.get("reason") or "")
    high_risk = str(recommendation.get("high_risk_dates") or "")

    left, right = st.columns([2, 1])
    with left:
        st.success(f"最佳日期：{best_date}")
        if reason:
            st.info(f"推荐理由：{reason}")
        if high_risk:
            st.warning(f"高风险时段：{high_risk}")

    with right:
        free_start = datetime.date.fromisoformat(str(query.get("free_start_str"))[:10])
        free_end = datetime.date.fromisoformat(str(query.get("free_end_str"))[:10])
        cur_selected = st.session_state.get("selected_date") or best_date
        try:
            selected_init = datetime.date.fromisoformat(str(cur_selected)[:10])
        except Exception:
            selected_init = free_start

        picked = st.date_input(
            "选择出行日期",
            value=selected_init,
            min_value=free_start,
            max_value=free_end,
            key="planning_selected_date_picker",
        )
        new_selected = picked.strftime("%Y-%m-%d")

        if st.session_state.get("selected_date") != new_selected:
            st.session_state.selected_date = new_selected
            query["recommendation"] = _generate_recommendation(query)
            st.session_state.planning_query_result = query
            if st.session_state.get("show_detailed_report"):
                st.session_state.planning_detailed_report = _generate_detail_report(query, new_selected)
                st.session_state.planning_detail_for_date = new_selected
            _safe_rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown("### 🌦️ 空闲时间段天气")
    if selected_days:
        cols = st.columns(len(selected_days))
        for i, day in enumerate(selected_days):
            with cols[i]:
                d = _normalize_date_str(day.get("date"))
                try:
                    dd = datetime.date.fromisoformat(d)
                    date_label = f"{dd.month}月{dd.day}日"
                except Exception:
                    date_label = d

                low = day.get("low")
                high = day.get("high")
                temp_text = "温度：未知"
                if low is not None and high is not None:
                    temp_text = f"温度：{low:g}~{high:g}°C"

                precip = day.get("precip")
                if precip is None:
                    precip_text = "降水：未知"
                elif precip <= 0:
                    precip_text = "降水：无降水"
                else:
                    precip_text = f"降水：{precip:g} mm"

                hum = day.get("humidity")
                hum_text = "湿度：未知" if hum is None else f"湿度：{hum}%"

                st.markdown(
                    f"""
                    <div class='planning-weather-box'>
                        <div class='planning-weather-date'>{date_label}</div>
                        <div class='planning-weather-icon'>{_weather_icon(day.get('text_day'))}</div>
                        <div class='text-sub'>{day.get('text_day', '')}</div>
                        <div class='text-sub'>{temp_text}</div>
                        <div class='text-sub'>风力：{day.get('wd_day', '')} {day.get('wc_day', '')}</div>
                        <div class='text-sub'>{precip_text}</div>
                        <div class='text-sub'>{hum_text}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    st.markdown("</div>", unsafe_allow_html=True)

    detail_btn = "📋 收起详细报告" if st.session_state.get("show_detailed_report") else "📋 查看详细报告"
    if st.button(detail_btn, key="toggle_detailed_report"):
        st.session_state.show_detailed_report = not st.session_state.get("show_detailed_report")
        if st.session_state.show_detailed_report:
            selected_date = st.session_state.get("selected_date") or best_date
            st.session_state.planning_detailed_report = _generate_detail_report(query, selected_date)
            st.session_state.planning_detail_for_date = selected_date
        _safe_rerun()

    detail = st.session_state.get("planning_detailed_report")
    selected_date = st.session_state.get("selected_date") or best_date
    if st.session_state.get("show_detailed_report"):
        if st.session_state.get("planning_detail_for_date") != selected_date:
            st.session_state.planning_detailed_report = _generate_detail_report(query, selected_date)
            st.session_state.planning_detail_for_date = selected_date
            detail = st.session_state.get("planning_detailed_report")

        if detail:
            st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
            st.markdown("### 🧾 详细报告")
            risk_level = str(detail.get("risk_level") or "中")
            badge_class = _risk_badge_class(risk_level)
            c1, c2 = st.columns([1, 2])
            with c1:
                st.markdown(f"<span class='{badge_class}'>风险等级：{risk_level}</span>", unsafe_allow_html=True)
            with c2:
                risks = detail.get("risk_factors") or []
                if isinstance(risks, str):
                    risks = [risks]
                if risks:
                    for risk in risks:
                        st.markdown(f"<div class='planning-risk-item'>⚠️ {risk}</div>", unsafe_allow_html=True)
                else:
                    st.caption("暂无关键风险因素")

            advice = detail.get("general_advice")
            if advice:
                st.info(f"综合建议：{advice}")

            # 装备建议：改为动态装备风险匹配，由天气与BMI计算提示（移除静态复选框）
            # 计算目标日期的天气摘要
            target_day = None
            for d in selected_days:
                if str(d.get("date") or "")[:10] == str(selected_date)[:10]:
                    target_day = d
                    break

            # 获取BMI：优先使用页面会话中的初始上报值，否则尝试从用户画像读取
            bmi = None
            try:
                h = st.session_state.get("onboard_height")
                w = st.session_state.get("onboard_weight")
                if h and w:
                    bmi = float(w) / ((float(h) / 100.0) ** 2)
                else:
                    user = get_user_by_id(st.session_state.get("user_id")) if st.session_state.get("user_id") else None
                    if user and user.get("bmi") is not None:
                        bmi = float(user.get("bmi"))
            except Exception:
                bmi = None

            gear = calculate_gear_risk(target_day, bmi, query.get("elevation"))
            if gear:
                # 风险徽章
                rl = gear.get("risk_level")
                color = gear.get("badge_color", "#F59E0B")
                st.markdown(
                    f"<div style='margin-top:8px;display:flex;align-items:center;gap:8px;'>"
                    f"<span style='display:inline-block;padding:6px 10px;border-radius:12px;background:{color};color:#fff;font-weight:700;'>{rl}</span>"
                    f"<div style='color:rgba(226,232,240,0.9)'>建议：{gear.get('suggestion')}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                # 展示计算过程
                with st.expander("查看装备风险计算过程与量化细节"):
                    det = gear.get("details", {})
                    st.write(f"- 空气温度 (Ta): {det.get('Ta')} °C")
                    st.write(f"- 估算风速 (Va): {det.get('Va_m_s')} m/s")
                    st.write(f"- 风寒体感 (AT): {det.get('AT_raw')} °C")
                    st.write(f"- 海拔修正后 (AT_altitude_adjusted): {det.get('AT_altitude_adjusted')} °C")
                    st.write(f"- BMI 代谢系数 (metab_coef): {det.get('metab_coef')}，对应温度修正 {det.get('metab_temp_offset')} °C")
                    st.write(f"- 最终等效体感温度: {det.get('effective_temp')} °C")
                    st.markdown("**关键缺口：** " + ("; ".join(gear.get('gaps', [])) or "无"))
            st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("🛰️ 卫星地图", expanded=False):
        z1, z2, z3, _ = st.columns([1, 1, 2, 4])
        with z1:
            if st.button("🔍 放大", key="map_zoom_in"):
                st.session_state.map_zoom = min(18, int(st.session_state.map_zoom) + 1)
                _safe_rerun()
        with z2:
            if st.button("🔎 缩小", key="map_zoom_out"):
                st.session_state.map_zoom = max(3, int(st.session_state.map_zoom) - 1)
                _safe_rerun()
        with z3:
            map_type_labels = {"普通地图": 1, "卫星地图": 2, "地形图": 3}
            selected_map_type = st.selectbox(
                "地图类型",
                list(map_type_labels.keys()),
                index=1,
                key="planning_map_type_select",
                label_visibility="collapsed",
            )
            planning_map_type = map_type_labels[selected_map_type]

        try:
            map_html = js_map_html(
                float(query["lng"]),
                float(query["lat"]),
                zoom=int(st.session_state.map_zoom),
                height=400,
                map_type=planning_map_type,
                marker=True,
            )
            components.html(map_html, height=410)
        except Exception:
            pass

    if st.button("✅ 确认创建档案", key="confirm_create_adventure"):
        try:
            user_id = st.session_state.get("user_id", 1)
            destination = str(query.get("destination") or "").strip()
            trip_date = str(st.session_state.get("selected_date") or best_date)[:10]
            start_time = f"{trip_date} 00:00:00"

            adv_id = create_adventure(user_id, destination, start_time=start_time)
            if not adv_id:
                st.error("创建档案失败：无效的返回ID")
                return
            
            save_report(
                adv_id,
                "pre",
                {
                    "recommendation": query.get("recommendation"),
                    "detail": st.session_state.get("planning_detailed_report"),
                    "selected_date": trip_date,
                },
            )
            st.session_state.current_adventure_id = adv_id
            st.session_state.active_tab = 1
            st.session_state.nav_page = "行中监护"
            st.session_state._planning_created_notice = "档案已创建，已切换到行中监护。"
            _safe_rerun()
        except Exception as e:
            error_msg = str(e)
            print(f"[ERROR] 创建档案失败：{error_msg}")
            if "destination" in error_msg.lower():
                st.error("创建档案失败：目的地信息不完整，请重新输入")
            elif "user_id" in error_msg.lower():
                st.error("创建档案失败：用户信息异常，请重新登录")
            else:
                st.error(f"创建档案失败，请稍后重试") 
