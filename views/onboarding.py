import os
import sys

import streamlit as st

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.db import get_user_by_id, update_user_profile


def _safe_rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _bmi_status(bmi):
    if bmi < 18.5:
        return "偏瘦", "badge-yellow"
    if bmi < 24:
        return "正常", "badge-green"
    if bmi < 28:
        return "超重", "badge-yellow"
    return "肥胖", "badge-red"


def _ai_hint(bmi, fitness="轻度运动", altitude_exp="无"):
    """增强版模板建议，基于 BMI + 体质数据"""
    hints = []

    # BMI 基础建议
    if bmi < 18.5:
        hints.append("基础储备偏低，建议提升耐力训练，行前强化碳水和蛋白补给。")
    elif bmi < 24:
        hints.append("体重区间适合户外活动，建议保持有氧+核心训练并持续补水。")
    elif bmi < 28:
        hints.append("负重和爬升路段建议控制节奏，重点关注膝踝保护与电解质补给。")
    else:
        hints.append("建议先做体能优化训练，路线优先低风险，减少高强度暴露时间。")

    # （已移除过敏史建议 — 保持建议基于 BMI、运动等级与高原经验）

    # 运动等级建议
    if fitness == "久坐":
        hints.append("运动基础较弱，建议行前至少提前2周开始适应性训练。")
    elif fitness == "高强度运动":
        hints.append("运动基础较好，但仍需注意高原环境适应，不可过度自信。")

    # 高原经验建议
    if altitude_exp == "无" or altitude_exp == "初次":
        hints.append("建议首次高原行程控制在3500m以下，循序渐进适应海拔。")

    return " ".join(hints[:3])


def _ai_health_advice(bmi, bmi_status, age, altitude_history, fitness, altitude_exp):
    """调用文心模型生成个性化健康建议"""
    try:
        from services.ai_service import AIService
        ai = AIService()
        if not ai.client:
            return None

        system_prompt = (
            "你是一名持有WMAI（国际野外医学协会）认证的户外健康评估专家。"
            "根据用户的身体数据，给出简洁、专业、可执行的户外活动健康建议。"
            "建议需要针对用户的具体体质特征，而非泛泛而谈。"
            "输出要求：纯文本，3-4句话，每句话都包含具体的量化建议或行动指引，总计不超过120字。"
        )
        user_prompt = (
            f"用户画像：\n"
            f"- 年龄：{age}岁\n"
            f"- BMI：{bmi:.1f}（{bmi_status}）\n"
            f"- 既往高原反应史：{altitude_history if altitude_history else '无'}\n"
            f"- 运动等级：{fitness}\n"
            f"- 高原经验：{altitude_exp}\n\n"
            "请根据以上信息，生成个性化户外探险健康建议。"
        )

        result = ai.call_wenxin(system_prompt, user_prompt, temperature=0.3)
        if result and "AI服务暂不可用" not in result and "AI调用失败" not in result:
            return result.strip()
    except Exception:
        pass
    return None


def _bmi_score(bmi):
    return max(0, min(100, int(100 - abs(float(bmi) - 22.0) * 8)))


def calculate_hai(bmi, fitness, altitude_exp, age, altitude_history):
    """
    计算高原适应力指数 HAI（0-100）。可解释公式：

    子项得分计算：
    - BMI评分：偏离22越远分数越低，使用：max(0, 100 - |BMI-22|*8)
    - 运动等级：久坐=60, 轻度=80, 中度=100, 高强度=110（视为优势，可超过100）
    - 高原经验：无=60, 初次=80, 有经验=100
    - 年龄：25岁为满分100，偏离每岁扣2分，使用：max(0, 100 - |age-25|*2)
    - 高原反应史：无=100, 轻度=85, 中度=60, 重度=30

    加权融合（权重）：BMI 30% + 运动 25% + 经验 20% + 年龄 15% + 病史 10%

    返回值：0-100 浮点数（保留一位小数）。
    """
    try:
        bmi = float(bmi)
    except Exception:
        bmi = 22.0

    # BMI score
    bmi_score = max(0.0, 100.0 - abs(bmi - 22.0) * 8.0)

    # fitness mapping
    fitness_map = {
        "久坐": 60.0,
        "轻度运动": 80.0,
        "中度运动": 100.0,
        "高强度运动": 110.0,
    }
    fitness_score = float(fitness_map.get(str(fitness), 80.0))

    # altitude experience mapping
    exp_map = {"无": 60.0, "初次": 80.0, "有经验": 100.0}
    exp_score = float(exp_map.get(str(altitude_exp), 60.0))

    # age score: 25岁为满分，每岁偏离扣2分
    try:
        age_v = float(age)
        age_score = max(0.0, 100.0 - abs(age_v - 25.0) * 2.0)
    except Exception:
        age_score = 100.0

    # altitude_history mapping (Lake Louise labels)
    ah = str(altitude_history or "").strip()
    if "无（" in ah or ah.startswith("无") or ah == "无":
        history_score = 100.0
    elif ah.startswith("轻度"):
        history_score = 85.0
    elif ah.startswith("中度"):
        history_score = 60.0
    elif ah.startswith("重度"):
        history_score = 30.0
    else:
        history_score = 100.0

    # Weights
    w_bmi, w_fit, w_exp, w_age, w_hist = 0.30, 0.25, 0.20, 0.15, 0.10

    # Combined raw score (note fitness_score can exceed 100; cap final to 100)
    raw = (bmi_score * w_bmi + fitness_score * w_fit + exp_score * w_exp + age_score * w_age + history_score * w_hist)
    hai = max(0.0, min(100.0, raw))
    return round(hai, 1)


def render():
    user_id = st.session_state.get("user_id")
    if not user_id:
        st.error("用户会话异常，请重新登录。")
        return

    user = get_user_by_id(user_id) if user_id else None

    st.markdown(
        """
        <style>
        .onboard-title {
            font-size: 30px;
            font-weight: 700;
            color: #e2e8f0;
            letter-spacing: 0.5px;
        }
        .onboard-sub {
            font-size: 14px;
            color: rgba(140, 180, 220, 0.78);
            margin-bottom: 18px;
        }
        .onboard-kpi {
            font-size: 48px;
            font-weight: 800;
            color: #00C6FF;
            line-height: 1;
            margin: 4px 0 8px 0;
        }
        .onboard-kpi-sub {
            font-size: 16px;
            color: rgba(226, 232, 240, 0.9);
            margin-bottom: 10px;
        }
        .onboard-foot {
            margin-top: 8px;
            color: rgba(140, 180, 220, 0.8);
            font-size: 13px;
        }
        /* ===== Custom styling for allergy multiselect to match dark UI ===== */
        .custom-card .stMultiSelect, .custom-card [data-testid="stMultiselect"] {
            color: #e2e8f0 !important;
            font-family: 'Monaco', 'Menlo', 'Consolas', monospace !important;
        }
        .custom-card .stMultiSelect > div > div,
        .custom-card [data-testid="stMultiselect"] > div > div {
            background-color: rgba(10, 15, 25, 0.88) !important;
            border: 1px solid rgba(0, 198, 255, 0.14) !important;
            border-radius: 12px !important;
            padding: 8px 10px !important;
        }
        .custom-card .stMultiSelect button,
        .custom-card [data-testid="stMultiselect"] button {
            background: linear-gradient(90deg, rgba(0,180,255,0.12), rgba(0,198,255,0.06)) !important;
            border: 1px solid rgba(0,198,255,0.22) !important;
            color: #e2e8f0 !important;
            border-radius: 10px !important;
            padding: 4px 8px !important;
            margin: 3px 3px 3px 0 !important;
        }
        .custom-card .stMultiSelect div[role="listbox"],
        .custom-card [data-testid="stMultiselect"] div[role="listbox"] {
            background-color: rgba(6, 10, 14, 0.98) !important;
            border: 1px solid rgba(0, 198, 255, 0.10) !important;
            color: #e2e8f0 !important;
        }
        /* Fallback generic selectors to catch different Streamlit class names */
        .custom-card .css-1d391kg, .custom-card .css-1n76uvr {
            background-color: rgba(10, 15, 25, 0.88) !important;
            color: #e2e8f0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    age_default = int(user["age"]) if user and user["age"] is not None else 25
    height_default = int(float(user["height"])) if user and user["height"] is not None else 170
    weight_default = int(float(user["weight"])) if user and user["weight"] is not None else 65

    if "onboard_age" not in st.session_state:
        st.session_state.onboard_age = age_default
    if "onboard_height" not in st.session_state:
        st.session_state.onboard_height = height_default
    if "onboard_weight" not in st.session_state:
        st.session_state.onboard_weight = weight_default

    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown("<div class='onboard-title'>完善个人资料</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='onboard-sub'>用于初始化守望阈值与个性化保障策略（约30秒完成）</div>",
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)

    col_l, col_r = st.columns([1, 1], gap="large")

    with col_l:
        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown("### 🧾 基础信息")
        age = st.number_input("年龄", min_value=10, max_value=100, step=1, key="onboard_age")
        height = st.number_input("身高 (cm)", min_value=100, max_value=250, step=1, key="onboard_height")
        weight = st.number_input("体重 (kg)", min_value=30, max_value=200, step=1, key="onboard_weight")

        st.markdown("### 🏥 健康档案")
        # 既往高原反应史（Lake Louise 标准选项）
        altitude_history = st.selectbox(
            "既往高原反应史",
            [
                "无（从未上过高原或无症状）",
                "轻度（头痛+头晕，不影响活动）",
                "中度（头痛+恶心+乏力，活动受限）",
                "重度（肺水肿/脑水肿史，或需紧急下撤）",
            ],
            index=0,
            key="onboard_altitude_history",
        )

        fitness = st.selectbox(
            "日常运动等级",
            ["久坐", "轻度运动", "中度运动", "高强度运动"],
            index=1,
            key="onboard_fitness",
        )

        altitude_exp = st.selectbox(
            "高原经验",
            ["无", "初次", "有经验"],
            index=0,
            key="onboard_altitude_exp",
        )

        st.markdown("<div class='onboard-foot'>这些参数仅用于个性化预警阈值，不会公开展示。</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    bmi = float(weight) / ((float(height) / 100.0) ** 2) if height > 0 else 0.0
    status, badge_class = _bmi_status(bmi)
    score = _bmi_score(bmi)

    # 右侧改为展示 HAI（高原适应力指数）卡片，取代单独的 BMI 大字显示
    with col_r:
        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown("### 📊 高原适应力指数（HAI）")

        # 计算 HAI 并写入 session
        hai_score = calculate_hai(bmi, fitness, altitude_exp, age, altitude_history)
        st.session_state['hai_score'] = hai_score

        # 颜色分级
        if hai_score >= 80:
            color = "#10B981"
            level = "优秀"
        elif hai_score >= 60:
            color = "#F59E0B"
            level = "良好"
        else:
            color = "#EF4444"
            level = "需谨慎"

        # HAI 大字 + 自定义进度条
        st.markdown(
            f"<div style=\"display:flex;align-items:center;justify-content:space-between;\">"
            f"<div style=\"font-size:48px;font-weight:800;color:{color};\">{hai_score:.1f}</div>"
            f"<div style=\"text-align:right;min-width:140px;color:rgba(226,232,240,0.9);\">{level}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # 简易进度条（HTML）
        pct = max(0, min(100, float(hai_score)))
        st.markdown(
            f"<div style='background:rgba(255,255,255,0.06);border-radius:10px;height:14px;overflow:hidden;'>"
            f"<div style='width:{pct}%;height:100%;background:{color};transition:width 0.6s;'></div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # 展开显示明细
        with st.expander("查看 HAI 分项明细"):
            bmi_s = _bmi_score(bmi)
            fitness_map = {"久坐": 60.0, "轻度运动": 80.0, "中度运动": 100.0, "高强度运动": 110.0}
            fitness_s = fitness_map.get(fitness, 80.0)
            exp_map = {"无": 60.0, "初次": 80.0, "有经验": 100.0}
            exp_s = exp_map.get(altitude_exp, 60.0)
            try:
                age_s = max(0.0, 100.0 - abs(float(age) - 25.0) * 2.0)
            except Exception:
                age_s = 100.0
            ah = str(altitude_history or "")
            if "无（" in ah or ah.startswith("无"):
                hist_s = 100.0
            elif ah.startswith("轻度"):
                hist_s = 85.0
            elif ah.startswith("中度"):
                hist_s = 60.0
            elif ah.startswith("重度"):
                hist_s = 30.0
            else:
                hist_s = 100.0

            st.write(f"- BMI 得分：{bmi_s}/100")
            st.write(f"- 运动等级得分：{fitness_s}/110")
            st.write(f"- 高原经验得分：{exp_s}/100")
            st.write(f"- 年龄得分：{age_s}/100")
            st.write(f"- 既往高原反应史得分：{hist_s}/100")
            st.write(f"- 计算说明：HAI = BMI*30% + 运动*25% + 经验*20% + 年龄*15% + 病史*10%，结果截取0-100")

        # AI 个性化建议
        ai_advice = _ai_health_advice(bmi, status, age, altitude_history, fitness, altitude_exp)
        if ai_advice:
            st.info(f"🤖 AI 个性化建议：{ai_advice}")
        else:
            hint = _ai_hint(bmi, fitness, altitude_exp)
            st.info(f"建议：{hint}")

        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    if st.button("开启守望之旅", key="onboard_submit"):
        try:
            update_user_profile(
                user_id, age, height, weight, bmi, status,
                chronic_conditions='',
                fitness_level=fitness,
                altitude_experience=altitude_exp,
                altitude_history=altitude_history,
                hai_score=hai_score,
            )
            st.session_state.profile_complete = True
            st.session_state.nav_page = "行前规划"
            st.session_state.active_tab = -1
            st.success("资料已保存，正在进入主导航…")
            _safe_rerun()
        except Exception:
            st.error("资料保存失败，请稍后重试。")
    st.markdown("</div>", unsafe_allow_html=True)
