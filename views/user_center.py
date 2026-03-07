import os
import sys

import streamlit as st

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.db import get_adventures, get_user_by_id, update_user_profile


def _estimate_destination_altitude(destination):
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
    for key, value in known.items():
        if key in name:
            return value
    return 1800 if ("山" in name or "峰" in name) else 800


def _status_dot(online=True):
    if online:
        return "<span class='status-dot dot-green' style='margin-right:6px;'></span>"
    return "<span class='status-dot dot-red' style='margin-right:6px;'></span>"


def _safe_rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _bmi_status(bmi):
    if bmi < 18.5:
        return "偏瘦"
    if bmi < 24:
        return "正常"
    if bmi < 28:
        return "超重"
    return "肥胖"


def _bmi_badge_class(status):
    if status == "正常":
        return "badge-green"
    if status in ("偏瘦", "超重"):
        return "badge-yellow"
    return "badge-red"


def render():
    user_id = st.session_state.get("user_id")
    user = get_user_by_id(user_id) if user_id else None
    if not user:
        st.warning("未找到用户信息，请重新登录。")
        return

    age = int(user["age"]) if user["age"] is not None else 25
    height = float(user["height"]) if user["height"] is not None else 170.0
    weight = float(user["weight"]) if user["weight"] is not None else 65.0
    bmi = float(user["bmi"]) if user["bmi"] is not None else 22.0
    bmi_status = str(user["bmi_status"] or "正常")
    bmi_badge = _bmi_badge_class(bmi_status)
    heart_limit = max(100, 220 - age)
    spo2_line = "92%" if bmi > 26 else "90%"

    adventures = get_adventures(int(user_id)) or []
    trips = len(adventures)
    guard_hours = max(8, trips * 6 + 11)
    highest_altitude = max([_estimate_destination_altitude(a["destination"]) for a in adventures], default=3200)

    st.markdown(
        """
        <style>
        .uc-title {
            font-size: 30px;
            font-weight: 700;
            color: #e2e8f0;
            letter-spacing: 0.5px;
        }
        .uc-sub {
            font-size: 14px;
            color: rgba(140, 180, 220, 0.78);
            margin-bottom: 8px;
        }
        .uc-kpi {
            font-size: 34px;
            font-weight: 800;
            color: #00C6FF;
            line-height: 1.2;
            margin: 4px 0;
        }
        .uc-line {
            color: rgba(226, 232, 240, 0.92);
            margin: 6px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown("<div class='uc-title'>个人中心</div>", unsafe_allow_html=True)
    st.markdown("<div class='uc-sub'>守望档案、算法基准与设备状态总览</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    c1, c2 = st.columns(2, gap="large")

    with c1:
        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown("### 👤 个人档案")
        st.markdown(f"<div class='uc-line'>用户名：{user['username']}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='uc-line'>年龄：{age} 岁</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='uc-line'>身高：{height:.0f} cm</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='uc-line'>体重：{weight:.1f} kg</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='uc-line'>BMI：<b>{bmi:.1f}</b> <span class='{bmi_badge}' style='margin-left:6px;'>{bmi_status}</span></div>",
            unsafe_allow_html=True,
        )
        chronic = str(user.get("chronic_conditions", "") if hasattr(user, "get") else (user["chronic_conditions"] if "chronic_conditions" in user.keys() else "")) or "无"
        fitness = str(user.get("fitness_level", "") if hasattr(user, "get") else (user["fitness_level"] if "fitness_level" in user.keys() else "")) or "轻度运动"
        altitude_exp = str(user.get("altitude_experience", "") if hasattr(user, "get") else (user["altitude_experience"] if "altitude_experience" in user.keys() else "")) or "无"
        st.markdown(f"<div class='uc-line'>慢性病史：{chronic if chronic else '无'}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='uc-line'>运动等级：{fitness}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='uc-line'>高原经验：{altitude_exp}</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown("### ✏️ 修改资料")
        with st.form("user_profile_edit_form"):
            edit_age = st.number_input("年龄", min_value=10, max_value=100, value=int(age), step=1)
            edit_height = st.number_input("身高 (cm)", min_value=100.0, max_value=250.0, value=float(height), step=1.0)
            edit_weight = st.number_input("体重 (kg)", min_value=30.0, max_value=200.0, value=float(weight), step=0.5)
            save_ok = st.form_submit_button("保存资料")
        if save_ok:
            try:
                new_bmi = float(edit_weight) / ((float(edit_height) / 100.0) ** 2)
                new_status = _bmi_status(new_bmi)
                update_user_profile(user_id, edit_age, edit_height, edit_weight, new_bmi, new_status)
                st.success("资料已更新")
                _safe_rerun()
            except Exception:
                st.error("资料更新失败，请稍后重试")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown("### 📡 虚拟外设状态")
        st.markdown(f"{_status_dot(True)} 已连接 Garmin Fenix 7（模拟）", unsafe_allow_html=True)
        st.markdown(f"{_status_dot(True)} 已连接 Mate 60 Pro（模拟）", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown("### ⚙️ 算法基准")
        st.markdown(f"<div class='uc-kpi'>{heart_limit} bpm</div>", unsafe_allow_html=True)
        st.caption("心率上限（220 - 年龄）")
        st.markdown(f"<div class='uc-line'>血氧预警线：<b>{spo2_line}</b></div>", unsafe_allow_html=True)
        st.caption(f"您的 BMI 为 {bmi:.1f}（{bmi_status}），预警阈值会随体质画像动态校准。")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown("### 🏅 探险勋章")
        st.markdown(f"<div class='uc-line'>累计守护时长：<b>{guard_hours} 小时</b></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='uc-line'>最高海拔记录：<b>{highest_altitude} m</b></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='uc-line'>累计探险档案：<b>{trips} 次</b></div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
