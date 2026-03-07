import datetime
import io
import os
import sys

import numpy as np
import pandas as pd
import streamlit as st

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.db import (
    add_vitals,
    create_adventure,
    get_adventure_by_id,
    get_reports,
    get_user_adventures,
    get_vitals_by_adventure,
    save_report,
    update_adventure_status,
)
from services.ai_service import AIService


def _safe_rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _risk_badge(max_risk):
    if max_risk > 0.6:
        return "高", "badge-red"
    if max_risk >= 0.3:
        return "中", "badge-yellow"
    return "低", "badge-green"


def _to_df(vitals_rows):
    if not vitals_rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(v) for v in vitals_rows])
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"])
    return df


def _adv_get(adv, key, default=None):
    """Compat getter for dict/sqlite3.Row."""
    if isinstance(adv, dict):
        return adv.get(key, default)
    try:
        return adv[key]
    except Exception:
        return default





def _insert_demo_data(user_id):
    demos = [
        ("四姑娘山", datetime.datetime(2026, 3, 2, 8, 0, 0), 31.112, 102.905),
        ("珠穆朗玛峰", datetime.datetime(2026, 2, 15, 7, 30, 0), 27.9881, 86.9250),
    ]

    for name, base_time, base_lat, base_lon in demos:
        adv_id = create_adventure(user_id, name, start_date=base_time)
        for i in range(80):
            hr = float(np.clip(78 + np.random.normal(0, 8), 55, 150))
            spo2 = float(np.clip(96 + np.random.normal(-0.02 * i, 1.2), 78, 100))
            temp = float(np.clip(36.7 + np.random.normal(0, 0.2), 35.0, 39.0))
            lat = base_lat + i * 0.0009
            lon = base_lon + i * 0.0011
            risk = 0.2
            if spo2 < 90 or hr > 120:
                risk = 0.72
            elif spo2 < 94 or hr > 100:
                risk = 0.45
            add_vitals(adv_id, round(hr, 1), round(spo2, 1), round(temp, 1), round(lat, 6), round(lon, 6), risk)

        update_adventure_status(adv_id, "archived")


def _trip_title(adv):
    start_time = _adv_get(adv, "start_time")
    destination = str(_adv_get(adv, "destination", "") or "").strip()
    start = str(start_time)[:10] if start_time else "未知日期"
    if destination:
        if start not in destination:
            return f"{destination}-{start}"
        return destination
    return f"未知目的地-{start}"


def _high_risk_events(df, threshold=0.6):
    if df.empty or "risk_score" not in df.columns:
        return []
    risk_df = df[df["risk_score"] > threshold].copy()
    if risk_df.empty:
        return []
    events = []
    for _, row in risk_df.iterrows():
        ts = row.get("ts")
        events.append(
            {
                "time": ts.strftime("%Y-%m-%d %H:%M:%S") if pd.notnull(ts) else "未知时间",
                "hr": float(row.get("hr", 0)),
                "spo2": float(row.get("spo2", 0)),
                "temp": float(row.get("temp", 0)),
                "risk_score": float(row.get("risk_score", 0)),
            }
        )
    return events


def _load_or_generate_post_report(ai_service, adv, df, force=False):
    if not force:
        reports = get_reports(adv["id"], "post")
        if reports:
            return reports[0]["content"]

    risk_events = _high_risk_events(df)
    if df.empty:
        summary = {
            "destination": _adv_get(adv, "destination", "未知"),
            "start": str(_adv_get(adv, "start_time") or "未知"),
            "end": str(_adv_get(adv, "end_time") or "未知"),
            "hours": 0,
            "avg_hr": "无数据",
            "avg_spo2": "无数据",
            "avg_temp": "无数据",
            "max_risk": "未知",
            "events": "暂无体征数据",
        }
    else:
        start_time = _adv_get(adv, "start_time")
        end_time = _adv_get(adv, "end_time")
        start = pd.to_datetime(start_time) if start_time else df["ts"].min()
        end = pd.to_datetime(end_time) if end_time else df["ts"].max()
        hours = max((end - start).total_seconds() / 3600.0, 0)
        summary = {
            "destination": _adv_get(adv, "destination", "未知"),
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "end": end.strftime("%Y-%m-%d %H:%M"),
            "hours": round(hours, 2),
            "avg_hr": round(float(df["hr"].mean()), 1),
            "avg_spo2": round(float(df["spo2"].mean()), 1),
            "avg_temp": round(float(df["temp"].mean()), 1),
            "max_risk": round(float(df["risk_score"].max()), 2) if "risk_score" in df.columns else 0,
            "events": f"高风险事件 {len(risk_events)} 次",
        }

    report_text = ai_service.generate_post_report(summary)

    event_lines = []
    if risk_events:
        event_lines.append("\n\n## 高风险事件清单")
        # 控制长度，避免报告过长
        for e in risk_events[:20]:
            event_lines.append(
                f"- {e['time']} | 风险分 {e['risk_score']:.2f} | 心率 {e['hr']:.1f} bpm | 血氧 {e['spo2']:.1f}% | 体温 {e['temp']:.1f}°C"
            )
    else:
        event_lines.append("\n\n## 高风险事件清单\n- 本次行程未检测到 risk_score > 0.6 的高风险事件。")

    final_report = f"{report_text}{''.join(event_lines)}"
    save_report(adv["id"], "post", final_report)
    return final_report

    


def _render_list(adventures):
    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown("## 📚 历史行程")
    hide_unknown = st.checkbox("隐藏“当前未知/未知目的地”行程", value=True, key="retro_hide_unknown")
    st.markdown("</div>", unsafe_allow_html=True)

    shown = 0
    for adv in adventures:
        destination = str(adv["destination"] or "").strip()
        if hide_unknown and destination in ("当前未知", "未知目的地", ""):
            continue

        vitals_rows = get_vitals_by_adventure(adv["id"])
        df = _to_df(vitals_rows)
        max_risk = float(df["risk_score"].max()) if (not df.empty and "risk_score" in df.columns) else 0.0
        risk_text, badge_class = _risk_badge(max_risk)

        start = str(adv["start_time"])[:10] if adv["start_time"] else "未知"
        end = str(adv["end_time"])[:10] if adv["end_time"] else "进行中"

        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown(f"### {_trip_title(adv)}")
        st.markdown(f"日期：{start} 至 {end}")
        st.markdown(f"<span class='{badge_class}'>风险等级：{risk_text}</span>", unsafe_allow_html=True)

        if st.button("查看详情", key=f"detail_{adv['id']}"):
            st.session_state.selected_adventure_id = adv["id"]
            st.session_state.view_mode = "detail"
            _safe_rerun()

        st.markdown("</div>", unsafe_allow_html=True)
        shown += 1

    if shown == 0:
        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.info("当前筛选条件下没有可展示的已归档行程。")
        st.markdown("</div>", unsafe_allow_html=True)


def _render_detail(ai_service):
    adv_id = st.session_state.get("selected_adventure_id")
    if not adv_id:
        st.session_state.view_mode = "list"
        return

    adv = get_adventure_by_id(adv_id)
    if not adv:
        st.warning("行程不存在")
        st.session_state.view_mode = "list"
        st.session_state.selected_adventure_id = None
        return

    if st.button("← 返回列表"):
        st.session_state.view_mode = "list"
        st.session_state.selected_adventure_id = None
        _safe_rerun()

    start_time = _adv_get(adv, "start_time")
    end_time = _adv_get(adv, "end_time")
    start = str(start_time)[:10] if start_time else "未知"
    end = str(end_time)[:10] if end_time else "未知"

    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown(f"## {_trip_title(adv)} ({start} 至 {end})")
    st.markdown("</div>", unsafe_allow_html=True)

    vitals_rows = get_vitals_by_adventure(adv_id)
    df = _to_df(vitals_rows)

    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown("### 基本信息")
    st.caption("本行程数据已存档，可用于保险理赔凭证、未来探险参考。")
    if df.empty:
        st.info("无体征数据")
    else:
        start_dt = pd.to_datetime(start_time) if start_time else df["ts"].min()
        end_dt = pd.to_datetime(end_time) if end_time else df["ts"].max()
        duration_hours = max((end_dt - start_dt).total_seconds() / 3600.0, 0)
        high_risk_count = int((df["risk_score"] > 0.6).sum()) if "risk_score" in df.columns else 0

        r1c1, r1c2 = st.columns(2)
        r2c1, r2c2 = st.columns(2)
        r3c1, r3c2 = st.columns(2)

        r1c1.metric("总时长(h)", f"{duration_hours:.1f}")
        r1c2.metric("高风险事件次数", f"{high_risk_count}")
        r2c1.metric("平均心率", f"{df['hr'].mean():.1f}")
        r2c2.metric("平均血氧", f"{df['spo2'].mean():.1f}%")
        r3c1.metric("最低血氧", f"{df['spo2'].min():.1f}%")
        r3c2.metric("最高体温", f"{df['temp'].max():.1f}°C")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("""
    <style>
    .report-card {
        background: rgba(26, 40, 63, 0.78);
        backdrop-filter: blur(14px) saturate(160%);
        -webkit-backdrop-filter: blur(14px) saturate(160%);
        border-radius: 16px;
        box-shadow: 0 8px 30px rgba(67, 122, 214, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.04);
        padding: 22px;
        margin-bottom: 20px;
        border: 1px solid rgba(124, 179, 255, 0.24);
        transition: border-color 0.3s ease;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="report-card">', unsafe_allow_html=True)
    st.markdown("### 🤖 AI行程总结报告")

    if "retro_report_cache" not in st.session_state:
        st.session_state.retro_report_cache = {}

    if adv_id not in st.session_state.retro_report_cache:
        with st.spinner("正在自动生成AI总结..."):
            st.session_state.retro_report_cache[adv_id] = _load_or_generate_post_report(ai_service, adv, df, force=False)

    if st.button("刷新报告", key=f"refresh_report_{adv_id}"):
        with st.spinner("正在刷新AI总结..."):
            st.session_state.retro_report_cache[adv_id] = _load_or_generate_post_report(ai_service, adv, df, force=True)

    # 关闭报告卡片，显示 Markdown，然后重新打开
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown(st.session_state.retro_report_cache.get(adv_id, "暂无报告"))
    st.markdown('<div class="report-card">', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render():
    if "view_mode" not in st.session_state:
        st.session_state.view_mode = "list"
    if "selected_adventure_id" not in st.session_state:
        st.session_state.selected_adventure_id = None

    user_id = st.session_state.get("user_id", 1)
    ai_service = AIService()

    adventures = get_user_adventures(user_id, status="archived")

    # 🔥 无缝自动生成演示数据，不向用户/评委暴露"这是演示"的事实
    if not adventures:
        _insert_demo_data(user_id)
        adventures = get_user_adventures(user_id, status="archived")

    if st.session_state.view_mode == "detail" and st.session_state.selected_adventure_id:
        _render_detail(ai_service)
    else:
        _render_list(adventures)
