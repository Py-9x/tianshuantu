import streamlit as st
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from models.db import init_db, add_user, get_user

# 页面配置
st.set_page_config(
    page_title="天枢安途 - 高原探险全周期生命智能监护平台",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 注入CSS
css_path = os.path.join(os.path.dirname(__file__), "static", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# 顶部标题
st.markdown("<h1 style='text-align: center; color: #3b82f6;'>🛰️ 天涯守望 - 单人户外探险全周期智能生命保障系统</h1>", unsafe_allow_html=True)
st.markdown("---")

# 初始化数据库与用户
init_db()
if not get_user("test"):
    add_user("test", "123", "13800138000")
if 'user_id' not in st.session_state:
    st.session_state.user_id = 1

# 页面路由
from views import planning, monitoring, retrospective

pages = {
    "🗺️ 行前规划": planning.render,
    "❤️ 行中监护": monitoring.render,
    "📖 行后回顾": retrospective.render
}

# 顶部导航栏
selection = st.radio("📍 请选择功能模块：", list(pages.keys()), horizontal=True)
st.markdown("---")

# 执行对应页面渲染
pages[selection]()
