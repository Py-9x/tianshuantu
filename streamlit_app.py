"""
天涯智盾 - Streamlit Cloud 入口文件

Streamlit Cloud 会自动寻找：
1. streamlit_app.py （优先）
2. app.py
3. main.py

本文件兼容云环境，自动处理路径和依赖问题。
"""
import os
import sys

import streamlit as st

# 云环境路径处理
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

# 导入主应用
try:
    from models.db import init_db, add_user, get_user_by_id, get_user_by_username
except ImportError as e:
    st.error(f"❌ 模块加载失败：{e}")
    st.info("请确保所有依赖已正确安装")
    sys.exit(1)


def _safe_rerun():
    """兼容新旧版本 Streamlit 的刷新方法"""
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _is_profile_complete(user_row):
    """检查用户资料是否完整"""
    if not user_row:
        return False
    if "profile_complete" in user_row.keys():
        return bool(user_row["profile_complete"])
    return user_row["age"] is not None


def _theme_css():
    """全局样式"""
    return """
    <style>
    :root {
      --nav-tab-bg: rgba(10, 18, 30, 0.35);
      --nav-tab-border: rgba(0, 198, 255, 0.15);
      --nav-tab-hover: rgba(0, 198, 255, 0.35);
    }
    ::-webkit-scrollbar { width: 8px; }
    ::-webkit-scrollbar-track { background: rgba(0,0,0,0.1); }
    ::-webkit-scrollbar-thumb { background: rgba(0,198,255,0.3); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(0,198,255,0.5); }
    </style>
    """


def _brand_block(username):
    """品牌展示区"""
    brand_style = (
        "display:flex; justify-content:space-between; align-items:center; padding:12px 22px;"
        "background:rgba(10,18,30,0.82); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);"
        "border-radius:16px; border:1px solid rgba(0,198,255,0.12);"
        "box-shadow:0 4px 20px rgba(0,198,255,0.06); margin-bottom:20px;"
    )
    title_color = "#e2e8f0"
    user_color = "rgba(140,180,220,0.8)"

    st.markdown(
        f"""
        <div style='{brand_style}'>
            <div>
                <h2 style='margin:0; color:{title_color}; font-size:20px;'>⛰️ 天枢安途</h2>
                <p style='margin:4px 0 0; color:rgba(200,200,200,0.6); font-size:12px;'>
                    高原探险全周期生命智能监护平台
                </p>
            </div>
            <div style='text-align:right; color:{user_color}; font-size:12px;'>
                <p style='margin:0;'>欢迎</p>
                <p style='margin:4px 0 0; font-weight:bold;'>{username}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )


def main():
    """主应用入口"""
    # 初始化数据库
    try:
        init_db()
    except Exception as e:
        st.error(f"❌ 数据库初始化失败：{e}")
        return

    # 初始化 session state
    if "user_id" not in st.session_state:
        st.session_state.user_id = None
    if "current_page" not in st.session_state:
        st.session_state.current_page = "login"

    # 应用样式
    st.markdown(_theme_css(), unsafe_allow_html=True)

    # 认证检查
    if not st.session_state.user_id:
        # 登录逻辑
        from views.login import render as render_login
        render_login()
    else:
        # 主应用逻辑
        user = get_user_by_id(st.session_state.user_id)
        if not user:
            st.session_state.user_id = None
            _safe_rerun()
            return

        username = user.get("username") if isinstance(user, dict) else user["username"]
        _brand_block(username)

        # 导航菜单
        tab1, tab2, tab3, tab4 = st.tabs(["📋 行前规划", "🏔️ 行中监护", "📊 行后回顾", "⚙️ 用户中心"])

        with tab1:
            from views.planning import render as render_planning
            render_planning()

        with tab2:
            from views.monitoring import render as render_monitoring
            render_monitoring()

        with tab3:
            from views.retrospective import render as render_retrospective
            render_retrospective()

        with tab4:
            from views.user_center import render as render_user_center
            render_user_center()


if __name__ == "__main__":
    # 设置页面配置
    st.set_page_config(
        page_title="天枢安途 - 高原探险全周期生命智能监护平台",
        page_icon="⛰️",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    main()
