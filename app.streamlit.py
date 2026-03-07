import os
import sys

import streamlit as st

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from models.db import init_db, add_user, get_user_by_id, get_user_by_username


def _safe_rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _is_profile_complete(user_row):
    if not user_row:
        return False
    if "profile_complete" in user_row.keys():
        return bool(user_row["profile_complete"])
    return user_row["age"] is not None


def _theme_css():
    return """
    <style>
    :root {
      --nav-tab-bg: rgba(10, 18, 30, 0.35);
      --nav-tab-border: rgba(0, 198, 255, 0.15);
      --nav-tab-hover: rgba(0, 198, 255, 0.35);
    }
    </style>
    """


def _brand_block(username):
    brand_style = (
        "display:flex; justify-content:space-between; align-items:center; padding:12px 22px;"
        "background:rgba(10,18,30,0.82); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);"
        "border-radius:16px; border:1px solid rgba(0,198,255,0.12);"
        "box-shadow:0 4px 20px rgba(0,198,255,0.06); margin-bottom:20px;"
    )
    title_color = "#e2e8f0"
    user_color = "rgba(140,180,220,0.8)"
    title_gradient = "linear-gradient(90deg,#00C6FF,#00E5FF)"

    st.markdown(
        f"""
        <div style='{brand_style}'>
            <div style='font-size:22px; font-weight:700; color:{title_color}; letter-spacing:1px;'>
                🛰️ <span style='background:{title_gradient}; -webkit-background-clip:text; -webkit-text-fill-color:transparent;'>天涯守望</span>
            </div>
            <div style='font-size:14px; color:{user_color}; letter-spacing:1px;'>
                👤 {username}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.set_page_config(
    page_title="天涯守望 - 智能生命保障系统",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "profile_complete" not in st.session_state:
    st.session_state.profile_complete = False
if "active_tab" not in st.session_state:
    st.session_state.active_tab = -1
if "nav_page" not in st.session_state:
    st.session_state.nav_page = "行前规划"

font_awesome = '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">'
st.markdown(font_awesome, unsafe_allow_html=True)

css_path = os.path.join(os.path.dirname(__file__), "static", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

st.markdown(_theme_css(), unsafe_allow_html=True)

st.markdown(
    """
    <style>
    .stTabs [data-baseweb="tab-list"],
    div[role="radiogroup"] {
        display: flex;
        width: 100%;
        gap: 12px;
        justify-content: space-between;
        align-items: stretch;
    }
    .stTabs [data-baseweb="tab"],
    div[role="radiogroup"] > label {
        flex: 1 1 0;
        justify-content: center;
        padding: 8px 10px;
        border-radius: 14px;
        border: 1px solid var(--nav-tab-border, rgba(0,198,255,0.15));
        background: var(--nav-tab-bg, rgba(10,18,30,0.35));
        margin: 0 !important;
    }
    .stTabs [data-baseweb="tab"]:hover,
    div[role="radiogroup"] > label:hover {
        border-color: var(--nav-tab-hover, rgba(0,198,255,0.35));
    }
    </style>
    """,
    unsafe_allow_html=True,
)

init_db()
if not get_user_by_username("test"):
    add_user("test", "123", "13800138000")
if "user_id" not in st.session_state:
    st.session_state.user_id = 1

from views import planning, monitoring, retrospective, login, onboarding, user_center

if not st.session_state.logged_in:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    login.render()
    st.stop()

user = None
if st.session_state.get("user_id"):
    user = get_user_by_id(int(st.session_state["user_id"]))
if not user and st.session_state.get("username"):
    user = get_user_by_username(str(st.session_state["username"]))

if user:
    st.session_state.user_id = int(user["id"])
    st.session_state.username = str(user["username"])
    st.session_state.profile_complete = _is_profile_complete(user)

if not st.session_state.get("profile_complete", False):
    onboarding.render()
    st.stop()

_brand_block(st.session_state.get("username", "探险家"))

with st.sidebar:
    st.markdown(f"### 👤 欢迎, {st.session_state.get('username', '探险家')}")
    st.caption("个人资料状态：已完善")

    if st.button("登出"):
        st.session_state.logged_in = False
        st.session_state.profile_complete = False
        st.session_state.active_tab = -1
        st.session_state.nav_page = "行前规划"
        _safe_rerun()
    st.markdown("---")

if st.session_state.get("active_tab", -1) in (1, 2, 3):
    tab_to_page = {
        1: "行中监护",
        2: "行后回顾",
        3: "个人中心",
    }
    st.session_state.nav_page = tab_to_page.get(int(st.session_state.active_tab), st.session_state.nav_page)
    st.session_state.active_tab = -1

selection = st.radio(
    "导航",
    ["行前规划", "行中监护", "行后回顾", "个人中心"],
    horizontal=True,
    label_visibility="collapsed",
    key="nav_page",
)
st.markdown("---")

if selection == "行前规划":
    planning.render()
elif selection == "行中监护":
    if st.session_state.get("_planning_created_notice"):
        st.success(st.session_state.pop("_planning_created_notice"))
    monitoring.render()
elif selection == "行后回顾":
    retrospective.render()
elif selection == "个人中心":
    user_center.render()

st.markdown(
    """
    <div style='text-align:center; margin-top:40px; padding:20px; font-size:12px;
                color:var(--text-muted); letter-spacing:1.2px;'>
        DEPLOYMENT CORE: TIANYA OPERATOR TERMINAL V5.0 · 百度飞桨 · 文心大模型 · 百度地图
    </div>
    """,
    unsafe_allow_html=True,
)
