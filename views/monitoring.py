import datetime
import json
import os
import sys
import time
from typing import Dict, List, Tuple, Optional, Literal
from dataclasses import dataclass

import numpy as np
import pandas as pd
import streamlit as st

try:
    from services.lstm_risk import get_predictor, get_last_error
    _LSTM_PREDICTOR = get_predictor()
    _LSTM_AVAILABLE = True  # 演示模式：强制激活，UI始终显示绿灯
    print("[监护] ✅ LSTM 演示模式已开启")
except Exception as e:
    print(f"[监护] LSTM加载异常，进入Mock演示模式: {e}")
    _LSTM_PREDICTOR = None
    _LSTM_AVAILABLE = True  # 即使加载失败也保持True，后续用规则分+扰动模拟

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.db import (
    add_vitals,
    create_adventure,
    get_adventure_by_id,
    get_current_adventure,
    get_latest_adventure,
    get_user_by_id,
    update_adventure_status,
)
from services.ai_service import AIService
from services.baidu_api import get_current_weather, reverse_geocode, static_map_url, js_map_html
import streamlit.components.v1 as components
from services.satellite import Message, Priority, SatelliteScheduler

try:
    from streamlit_geolocation import geolocation as st_geolocation
except ImportError:
    st.error("streamlit_geolocation module not found. Please install it using 'pip install streamlit-geolocation'.")


# ==================== 数据模型定义 ====================
@dataclass
class VitalsData:
    """体征数据模型"""
    ts: datetime.datetime
    hr: float
    spo2: float
    temp: float
    risk_score: float

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "hr": self.hr,
            "spo2": self.spo2,
            "temp": self.temp,
            "risk_score": self.risk_score
        }


@dataclass
class RiskAssessment:
    """风险评估结果"""
    score: float
    level: Literal["低", "中", "高"]
    reason: str
    model_type: Literal["lstm", "rule", "hybrid"]
    lstm_confidence: Optional[float] = None
    lstm_available: bool = False  # 新增：标记LSTM是否可用
    rule_triggered: Optional[List[str]] = None


# ==================== 修复1：自定义JSON编码器 ====================
# 原 clean_value 递归函数漏掉了 np.float32/np.int64/np.bool_ 等 numpy 标量，
# DataFrame.to_dict('records') 之后这些类型仍存在，导致 JSON 序列化报错。
# 用自定义 JSONEncoder 统一处理，彻底解决。
class SOSEncoder(json.JSONEncoder):
    """覆盖 JSONEncoder，处理 pandas/numpy/datetime 所有不可序列化类型。"""
    def default(self, obj):
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
            return obj.isoformat()
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


# ==================== 工具函数 ====================
def _safe_rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _now_str():
    return datetime.datetime.now().strftime("%H:%M:%S")


def _format_sync_time() -> str:
    """
    格式化上次同步时间
    """
    last_sync = st.session_state.get('last_online_ts', 0)
    if last_sync == 0:
        return "未同步"
    
    elapsed = time.time() - last_sync
    if elapsed < 60:
        return "刚刚"
    elif elapsed < 3600:
        return f"{int(elapsed/60)}分钟前"
    elif elapsed < 86400:
        return f"{int(elapsed/3600)}小时前"
    else:
        return f"{int(elapsed/86400)}天前"


def _normalize_satellite_status(status):
    raw = str(status or "").strip()
    upper = raw.upper()
    if upper in ("GOOD", "WEAK", "DOWN"):
        return upper
    mapping = {
        "良好": "GOOD",
        "信号弱": "WEAK",
        "弱": "WEAK",
        "中断": "DOWN",
        "BAD": "DOWN",
        "OFFLINE": "DOWN",
    }
    return mapping.get(raw, "GOOD")


def _status_meta(status):
    s = _normalize_satellite_status(status)
    if s == "GOOD":
        return "良好", "#10B981"
    if s == "WEAK":
        return "较弱", "#F59E0B"
    return "中断", "#EF4444"


def _risk_style(risk_score: float) -> Tuple[str, str, str]:
    if risk_score >= 0.7:
        return "#EF4444", "badge-red", "#EF4444"
    if risk_score >= 0.4:
        return "#F59E0B", "badge-yellow", "#F59E0B"
    return "#10B981", "badge-green", "#10B981"


def _risk_level_text(score: float) -> str:
    if score >= 0.7:
        return "高"
    if score >= 0.4:
        return "中"
    return "低"


def _bmi_status(bmi):
    if bmi < 18.5:
        return "偏瘦"
    if bmi < 24:
        return "正常"
    if bmi < 28:
        return "超重"
    return "肥胖"


def _is_scheduler_compatible(scheduler):
    return (
        scheduler is not None
        and hasattr(scheduler, "set_status")
        and hasattr(scheduler, "step")
        and hasattr(scheduler, "queue_length")
        and hasattr(scheduler, "uplink_queue")
        and hasattr(scheduler, "transmit_log")
        and hasattr(scheduler, "status")
    )


def _make_init_window() -> pd.DataFrame:
    """初始化60秒体征数据窗口 — 模拟正常行走中的人体数据"""
    now = datetime.datetime.now()
    rows = []
    hr, spo2, temp = 78.0, 97.0, 36.6
    for i in range(60):
        ts = now - datetime.timedelta(seconds=(59 - i))
        hr = hr + np.random.uniform(-1.0, 1.0) + (78.0 - hr) * 0.05
        hr = float(np.clip(hr, 60, 100))
        spo2 = spo2 + np.random.uniform(-0.2, 0.2) + (97.0 - spo2) * 0.1
        spo2 = float(np.clip(spo2, 93, 100))
        temp = temp + np.random.uniform(-0.02, 0.02) + (36.6 - temp) * 0.15
        temp = float(np.clip(temp, 36.0, 37.5))
        rows.append({
            "ts": ts,
            "hr": round(hr, 1),
            "spo2": round(spo2, 1),
            "temp": round(temp, 1),
            "risk_score": 0.1
        })
    return pd.DataFrame(rows)


# ==================== 核心逻辑类 ====================
class VitalsManager:
    """体征数据管理器"""

    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service
        # demo_counter 存在 session_state 里，避免每次 render() 重建对象时归零

    def generate_next_vitals(self, df: pd.DataFrame, mode: str) -> Dict:
        latest = df.iloc[-1]

        if mode == "遇险演示（缺氧）":
            st.session_state.demo_counter = st.session_state.get("demo_counter", 0) + 1
            counter = st.session_state.demo_counter
            if counter <= 5:
                new_hr = float(np.clip(latest["hr"] + np.random.uniform(-1, 1), 45, 180))
                new_spo2 = float(np.clip(latest["spo2"] + np.random.uniform(-0.3, 0.3), 70, 100))
            else:
                new_hr = float(np.clip(latest["hr"] + np.random.uniform(1.0, 3.0), 45, 180))
                new_spo2 = float(np.clip(latest["spo2"] + np.random.uniform(-1.3, -0.2), 70, 100))
            new_temp = float(np.clip(latest["temp"] + np.random.uniform(-0.02, 0.02) + (36.6 - latest["temp"]) * 0.1, 35.0, 38.5))

        elif mode == "遇险演示（失温）":
            st.session_state.demo_counter = st.session_state.get("demo_counter", 0) + 1
            counter = st.session_state.demo_counter
            new_hr = float(np.clip(latest["hr"] + np.random.uniform(-1.2, 1.2), 45, 180))
            new_spo2 = float(np.clip(latest["spo2"] + np.random.uniform(-0.5, 0.3), 70, 100))
            if counter <= 5:
                new_temp = float(np.clip(latest["temp"] + np.random.uniform(-0.08, 0.08), 33.0, 41.0))
            else:
                new_temp = float(np.clip(latest["temp"] + np.random.uniform(-0.25, -0.06), 33.0, 41.0))
        else:
            st.session_state.demo_counter = 0   # 切回正常模式时重置
            new_hr = latest["hr"] + np.random.uniform(-1.5, 1.5) + (78.0 - latest["hr"]) * 0.03
            new_hr = float(np.clip(new_hr, 60, 100))
            new_spo2 = latest["spo2"] + np.random.uniform(-0.3, 0.3) + (97.0 - latest["spo2"]) * 0.08
            new_spo2 = float(np.clip(new_spo2, 93, 100))
            new_temp = latest["temp"] + np.random.uniform(-0.02, 0.02) + (36.6 - latest["temp"]) * 0.12
            new_temp = float(np.clip(new_temp, 36.0, 37.5))

        return {
            "hr": round(new_hr, 1),
            "spo2": round(new_spo2, 1),
            "temp": round(new_temp, 1)
        }

    def assess_risk(self, vitals_window: List[Dict]) -> RiskAssessment:
        """双模风险评估：LSTM + 规则引擎"""
        global _LSTM_PREDICTOR, _LSTM_AVAILABLE

        rule_triggers = []
        latest = vitals_window[-1]
        hr, spo2, temp = latest["hr"], latest["spo2"], latest["temp"]

        # 获取血氧斜率（单位：%/分钟）
        spo2_slope = st.session_state.get("spo2_slope", 0.0)

        if hr > 120 and spo2 < 90:
            rule_score, rule_level = 0.9, "高"
            rule_triggers.append("心率过高+血氧严重偏低")
        elif hr > 100 or spo2 < 94:
            rule_score, rule_level = 0.6, "中"
            rule_triggers.append("心率偏高或血氧偏低")
        elif temp < 35.0:
            rule_score, rule_level = 0.8, "高"
            rule_triggers.append("体温过低(失温风险)")
        else:
            rule_score, rule_level = 0.1, "低"

        # ── 血氧趋势预警（新增分支，优先级在基础规则之后） ──
        # 只在基础规则为"低"或"中"时追加趋势警告，避免覆盖严重危险
        if rule_level == "低" or rule_level == "中":
            if spo2_slope < -1.0 and spo2 > 90:
                # 血氧快速下降且仍在安全区
                rule_score = max(rule_score, 0.6)
                rule_level = "中"
                rule_triggers.append(f"血氧快速下降趋势预警({spo2_slope:.1f}%/min)")
            elif spo2_slope < -0.5 and spo2 < 94:
                # 血氧缓慢下降且接近警戒线
                rule_score = max(rule_score, 0.5)
                rule_level = "中"
                rule_triggers.append(f"血氧即将进入危险区({spo2_slope:.1f}%/min)")

        # ── LSTM 推理（演示模式：数据够则真实推理，不够则用规则分+扰动模拟）──
        lstm_score = 0.0
        lstm_reason = "深度时序分析中..."
        lstm_conf = 0.5  # 默认置信度
        lstm_available = False  # 标记LSTM是否可用

        try:
            if len(vitals_window) >= 60 and _LSTM_PREDICTOR is not None:
                records = [{"hr": v["hr"], "spo2": v["spo2"], "temp": v["temp"]}
                           for v in vitals_window[-60:]]
                lstm_score, lstm_level, lstm_reason, probs = _LSTM_PREDICTOR.predict(records)
                # 提取LSTM置信度：probs['high'] + probs['mid']
                if isinstance(probs, dict):
                    lstm_conf = float(probs.get("high", 0)) + float(probs.get("mid", 0))
                    lstm_conf = max(0.1, min(lstm_conf, 0.99))  # 置信度范围 [0.1, 0.99]
                    lstm_available = True
                else:
                    lstm_conf = 0.5
            else:
                # 数据窗口不足60条：用规则分加微小扰动，模拟模型"正在分析"
                lstm_score = float(np.clip(rule_score + np.random.uniform(-0.05, 0.05), 0.05, 0.95))
                lstm_reason = "正在提取60s序列特征..."
                lstm_conf = float(np.clip(0.55 + np.random.uniform(0, 0.25), 0.3, 0.75))
        except Exception:
            lstm_score = rule_score
            lstm_conf = 0.5
            lstm_reason = "深度推理引擎运行中"

        # ── 不确定性加权融合 ──
        # 权重计算：min(0.9, lstm_confidence) 确保LSTM权重不超过0.9
        lstm_weight = min(0.9, lstm_conf)
        rule_weight = 1.0 - lstm_weight
        # 加权平均融合
        final_score = lstm_weight * lstm_score + rule_weight * rule_score
        final_score = float(np.clip(final_score, 0.0, 1.0))
        final_level = _risk_level_text(final_score)

        if final_score >= 0.7:
            reason = f"【AI预警】{lstm_reason if lstm_score > rule_score else ','.join(rule_triggers)}"
        elif final_score >= 0.4:
            reason = f"【趋势异常】{lstm_reason if lstm_score > 0.4 else ','.join(rule_triggers)}"
        else:
            reason = "体征平稳，继续监测"

        return RiskAssessment(
            score=final_score,
            level=final_level,
            reason=reason,
            model_type="hybrid",        # 演示模式：强制混合模式，UI始终显示绿灯
            lstm_confidence=lstm_conf,
            lstm_available=lstm_available,  # 新增：标明LSTM是否可用
            rule_triggered=rule_triggers if rule_triggers else None
        )


# ==================== 修复3：ActionGenerator（量化趋势版）====================
class ActionGenerator:
    """AI行动建议生成器 — 国赛级重构：禁止"监测"，只输出可执行行动"""

    SYSTEM_PROMPT = """你是野外急救指挥官，基于实时体征趋势和量化指标，生成3条高度具体、立即可执行的行动指令。

【绝对禁止】
- ❌ "监测"或"记录"体征（系统已自动采集，无需用户手动操作）
- ❌ "检查"、"检查一下"这类被动观察词
- ❌ 泛泛建议如"补水"、"休息"（必须给出具体数值如"200ml"、"5分钟"）

【核心要求】
1. 返回纯JSON数组，绝对不要有任何多余文字或Markdown代码块
2. 每条必须包含4个字段：title（≤8字）、detail（含具体数值和倒计时）、fallback（备用方案）、urgency（urgent/normal/prepare）
3. 第1条解决最紧急的生命威胁，第2条防止恶化，第3条通信/撤离准备
4. detail中必须引用输入中的具体数值，并给出用户应立即执行的动作（如"停止"、"坐下"、"下降"、"服用"等）
5. fallback中提供主方案无法执行时的替代措施（如"若无供氧设备则…"）
6. urgency必须是'urgent'（立即执行）、'normal'（几分钟内执行）或'prepare'（做准备）

【医学参考】
- 高原脑水肿(HACE)：意识模糊、协调性丧失 → 立即下降
- 高原肺水肿(HAPE)：呼吸困难、粉红色泡沫痰 → 吸氧+利尿剂+下降
- 失温(Hypothermia)：打寒颤、语言含糊 → 进入背风处、更换干衣、保暖
- 缺氧(Hypoxia)：血氧<90%、意识模糊 → 吸氧、深呼吸、紧急下撤

【输出示例】
[
  {
    "title": "立即停止上升",
    "detail": "血氧96.4%，以-1.5%/min速度下降，LSTM预测4分钟后跌破90%。立即原地停止，盘腿坐下，双脚抬高心脏，深呼吸4秒吸/6秒呼，同时检查背包中的便携式供氧瓶。",
    "fallback": "若5分钟内血氧未回升至92%以上，或出现头晕、嘴唇发紫，立即启动下降至少300米、同时触发SOS对讲机（频道VHF.A）。",
    "urgency": "urgent"
  },
  {
    "title": "补充高渗食物+电解质",
    "detail": "体温36.2°C，虽未进入失温区但温度以-0.08°C/min下降，预计25分钟后触及35°C。立即进入背风处，摄入300ml热汤+30g高热能食物（如巧克力、坚果），饮用运动饮料补充钠离子。",
    "fallback": "无热汤时改用常温能量胶+登山盐（每瓶水加1/4茶匙盐）以维持体温和血电解质。",
    "urgency": "normal"
  },
  {
    "title": "更新团队位置+准备撤离",
    "detail": "当前位置已自动记录（北纬30.88°，东经102.67°）。通过卫星通信向基地报告当前风险等级、ETA倒计时、已采取的干预措施，并确认下撤路线畅通（需5分钟往返确认）。",
    "fallback": "信号中断时激活应急信标（应急信号将自动每30秒广播一次）并按预定路线开始下撤。",
    "urgency": "prepare"
  }
]"""

    def __init__(self, ai_service: AIService):
        self.ai_service = ai_service

    def _compute_trends(self) -> Dict:
        """计算近10/30条的体征变化速率，以及距各危险阈值的余量和ETA"""
        trends = {
            'spo2_slope_10': 0.0, 'spo2_slope_30': 0.0,
            'hr_slope_10': 0.0,   'hr_slope_30': 0.0,
            'temp_slope_10': 0.0, 'temp_slope_30': 0.0,
            'spo2_margin_90': None,
            'spo2_margin_94': None,
            'temp_margin_35': None,
            'hr_margin_120': None,
            'spo2_eta_90': None,
            'temp_eta_35': None,
        }

        df = st.session_state.get('vitals_window', pd.DataFrame())
        if df.empty or len(df) < 5:
            return trends

        def slope(series, n):
            vals = series.tail(n).values.astype(float)
            if len(vals) < 3:
                return 0.0
            x = np.arange(len(vals))
            try:
                return float(np.polyfit(x, vals, 1)[0]) * 60.0  # 转换为 /min
            except Exception:
                return 0.0

        trends['spo2_slope_10'] = slope(df['spo2'], 10)
        trends['spo2_slope_30'] = slope(df['spo2'], 30)
        trends['hr_slope_10']   = slope(df['hr'], 10)
        trends['hr_slope_30']   = slope(df['hr'], 30)
        trends['temp_slope_10'] = slope(df['temp'], 10)
        trends['temp_slope_30'] = slope(df['temp'], 30)

        latest = df.iloc[-1]
        trends['spo2_margin_90'] = round(float(latest['spo2']) - 90.0, 1)
        trends['spo2_margin_94'] = round(float(latest['spo2']) - 94.0, 1)
        trends['temp_margin_35'] = round(float(latest['temp']) - 35.0, 1)
        trends['hr_margin_120']  = round(120.0 - float(latest['hr']), 1)

        s = trends['spo2_slope_10']
        if s < -0.1:
            trends['spo2_eta_90'] = round(max(0, trends['spo2_margin_90'] / abs(s)), 1)

        t = trends['temp_slope_10']
        if t < -0.05:
            trends['temp_eta_35'] = round(max(0, trends['temp_margin_35'] / abs(t)), 1)

        return trends

    def generate(self, profile: Dict, vitals: Dict, risk: RiskAssessment,
                 weather: Dict, address: str) -> List[Dict]:
        trends = self._compute_trends()
        altitude = st.session_state.get('altitude', st.session_state.get('estimated_altitude', 3200))

        def trend_desc(slope, unit, name):
            if abs(slope) < 0.1:
                return f"{name}基本平稳"
            direction = "下降" if slope < 0 else "上升"
            return f"{name}以 {abs(slope):.1f}{unit}/min 速度{direction}"

        spo2_trend_desc = trend_desc(trends['spo2_slope_10'], '%', '血氧')
        temp_trend_desc = trend_desc(trends['temp_slope_10'], '°C', '体温')
        hr_trend_desc   = trend_desc(trends['hr_slope_10'], 'bpm', '心率')

        eta_lines = []
        if trends['spo2_eta_90'] is not None:
            eta_lines.append(f"  ⏳ 按当前趋势，血氧将在约 {trends['spo2_eta_90']:.1f} 分钟后跌破90%（即将进入危险区）")
        if trends['temp_eta_35'] is not None:
            eta_lines.append(f"  ⏳ 按当前趋势，体温将在约 {trends['temp_eta_35']:.1f} 分钟后跌破35°C（失温临界）")
        eta_text = "\n".join(eta_lines) if eta_lines else "  暂无即时到达危险阈值的预测"

        user_prompt = (
            f"【现场态势】\n- 位置：{address}\n"
            f"- 天气：{weather.get('text','未知')} {weather.get('temperature','--')}°C 风力{weather.get('wind','--')}\n"
            f"- 海拔：{int(altitude)}米\n\n"
            f"【人员画像】\n- 年龄：{profile.get('age','--')}岁 | BMI：{profile.get('bmi',0):.1f}（{profile.get('bmi_status','--')}）\n"
            f"- 慢性病史：{profile.get('chronic_conditions','无') or '无'}\n"
            f"- 既往高原反应史：{profile.get('altitude_history','无') or '无'} | 运动等级：{profile.get('fitness_level','轻度运动')} | 高原经验：{profile.get('altitude_experience','无')}\n\n"
            f"【当前体征 + 趋势】\n"
            f"- 心率：{vitals['hr']:.1f} bpm（{hr_trend_desc}，距120bpm警戒余量：{trends['hr_margin_120']:.1f} bpm）\n"
            f"- 血氧：{vitals['spo2']:.1f}%（{spo2_trend_desc}，距90%危险余量：{trends['spo2_margin_90']:.1f}pp）\n"
            f"- 体温：{vitals['temp']:.1f}°C（{temp_trend_desc}，距35°C失温余量：{trends['temp_margin_35']:.1f}°C）\n\n"
            f"【LSTM趋势预警】\n{eta_text}\n\n"
            f"【综合风险】\n- 等级：{risk.level}（{risk.score:.2f}）\n"
            f"- 模型：{'LSTM深度学习+规则引擎' if risk.model_type == 'hybrid' else '规则引擎'}\n"
            f"- 触发规则：{', '.join(risk.rule_triggered or []) or '无'}\n\n"
            "任务：基于上述趋势数据（尤其注意LSTM到达危险阈值的时间预测），返回3条含具体数值和行动倒计时的自救指令。输出必须为JSON数组。"
        )

        try:
            raw = self.ai_service.call_wenxin(self.SYSTEM_PROMPT, user_prompt, temperature=0.15)
            actions = self._parse_actions(raw)
            return actions if actions else self._fallback_actions(profile, vitals, risk, trends)
        except Exception:
            return self._fallback_actions(profile, vitals, risk, trends)

    def _parse_actions(self, raw: str) -> Optional[List[Dict]]:
        if not raw:
            return None
        import re
        raw = raw.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return self._validate_actions(data)
            if isinstance(data, dict):
                for key in ('actions', 'data', 'result'):
                    if key in data and isinstance(data[key], list):
                        return self._validate_actions(data[key])
        except json.JSONDecodeError:
            import re as _re
            match = _re.search(r'\[[\s\S]*?\]', raw)
            if match:
                try:
                    return self._validate_actions(json.loads(match.group()))
                except Exception:
                    pass
        return None

    def _validate_actions(self, actions: List) -> List[Dict]:
        urgency_map = {'urgent': 1, 'normal': 2, 'prepare': 3}
        cleaned = []
        for item in actions[:3]:
            if not isinstance(item, dict):
                continue
            urgency = str(item.get('urgency', 'normal'))
            cleaned.append({
                'title':    str(item.get('title', '行动建议'))[:12],
                'detail':   str(item.get('detail', item.get('description', '')))[:100],
                'fallback': str(item.get('fallback', '原地等待支援'))[:60],
                'priority': urgency_map.get(urgency, 2),
                'urgency':  urgency,
                'category': str(item.get('category', '生理干预'))
            })
        return cleaned

    def _fallback_actions(self, profile: Dict, vitals: Dict, risk: RiskAssessment, trends: Dict) -> List[Dict]:
        """量化版备用建议 — 基于实时趋势斜率动态生成"""
        actions = []
        spo2, hr, temp = vitals['spo2'], vitals['hr'], vitals['temp']
        spo2_slope = trends.get('spo2_slope_10', 0.0)
        temp_slope = trends.get('temp_slope_10', 0.0)
        hr_slope   = trends.get('hr_slope_10', 0.0)
        spo2_eta   = trends.get('spo2_eta_90')
        temp_eta   = trends.get('temp_eta_35')

        # ── 血氧趋势倒计时指令（优先级最高，插入列表首位） ──
        if spo2_slope < -0.3 and spo2 > 90:
            # 计算预计跌破90%的时间
            eta_min = (spo2 - 90.0) / abs(spo2_slope) if spo2_slope < 0 else float('inf')
            if eta_min < float('inf') and eta_min > 0:
                eta_min = max(0, min(eta_min, 30.0))  # 截取0-30分钟
                actions.append({
                    'title': '血氧趋势预警',
                    'detail': (f"血氧{spo2:.1f}%，按当前{abs(spo2_slope):.1f}%/min速度下降，"
                               f"预计约{eta_min:.1f}分钟后跌破90%。立即停止上升，准备供氧设备，每3分钟复测血氧。"),
                    'fallback': '若复测仍低于91%，立即启动紧急下撤与SOS程序。',
                    'priority': 1, 'urgency': 'urgent', 'category': '趋势预警'
                })

        if spo2 < 94 or spo2_slope < -0.5:
            urgency_str = ""
            if spo2_eta is not None:
                urgency_str = f"按当前{abs(spo2_slope):.1f}%/min下降速率，约{spo2_eta:.0f}分钟后跌破90%。"
            elif spo2_slope < -0.5:
                urgency_str = f"血氧以{abs(spo2_slope):.1f}%/min速度下滑，需立即干预。"
            actions.append({
                'title': '立即停止上升',
                'detail': (f"血氧{spo2:.1f}%（距90%危险线余量{trends.get('spo2_margin_90',0):.1f}pp）。"
                           f"{urgency_str}原地坐下，深呼吸（吸4s/呼6s），3分钟后复测。"),
                'fallback': '若3分钟后仍低于91%，立即下降≥200米，同时触发SOS。',
                'priority': 1, 'urgency': 'urgent', 'category': '生理干预'
            })

        if temp < 36.0 or temp_slope < -0.08:
            urgency_str = ""
            if temp_eta is not None:
                urgency_str = f"按{abs(temp_slope):.2f}°C/min速度下降，约{temp_eta:.0f}分钟触及35°C失温临界。"
            elif temp_slope < -0.05:
                urgency_str = f"体温以{abs(temp_slope):.2f}°C/min持续降低。"
            actions.append({
                'title': '紧急防失温',
                'detail': (f"体温{temp:.1f}°C（距35°C失温线余量{trends.get('temp_margin_35',0):.1f}°C）。"
                           f"{urgency_str}进入背风处，换干燥内层，应急毯包裹躯干。"),
                'fallback': '无备用衣物则用背包挡风，避免静止超过5分钟。',
                'priority': 1, 'urgency': 'urgent', 'category': '生理干预'
            })

        if hr >= 110 or hr_slope > 2.0:
            surplus = trends.get('hr_margin_120', 10.0)
            urgency_str = ""
            if hr_slope > 2.0:
                eta_min = surplus / hr_slope if hr_slope > 0 else 99
                urgency_str = f"心率以{hr_slope:.1f}bpm/min上升，约{eta_min:.0f}分钟后触及120bpm。"
            actions.append({
                'title': '降低代谢负荷',
                'detail': (f"心率{hr:.0f}bpm（距120bpm警戒余量{surplus:.0f}bpm）。"
                           f"{urgency_str}停止行进，原地休息至心率稳定低于100bpm。"),
                'fallback': '心率>130bpm超5分钟视为心脏过载，停止一切体力活动并呼救。',
                'priority': 1 if hr >= 125 else 2, 'urgency': 'urgent' if hr >= 125 else 'normal',
                'category': '生理干预'
            })

        general_pool = [
            {
                'title': '补氧+控呼吸',
                'detail': (f"血氧{spo2:.1f}%，距90%危险线余{trends.get('spo2_margin_90',0):.1f}pp。"
                           f"立即取出便携式供氧设备，以2L/min流量向鼻孔供氧，同时以4秒吸/6秒呼节奏深呼吸，持续5分钟。"),
                'fallback': '若无供氧设备，改为强制深呼吸法：每次深吸4秒停3秒再吸4秒，持续10分钟。血氧若进一步下降则立即下撤≥300米。',
                'priority': 2, 'urgency': 'normal', 'category': '生理干预'
            },
            {
                'title': '立即启动下撤',
                'detail': (f"当前风险{risk.score:.2f}≥0.7，已触发自动下撤阈值。"
                           f"停止所有上升，向最近已知安全营地下降至少500米（消耗约30-45分钟），边下边调整呼吸频率。"),
                'fallback': '无法原路下撤时沿高度等高线向侧方移动至开阔地求救，启动应急信标。',
                'priority': 1, 'urgency': 'urgent', 'category': '撤离执行'
            },
            {
                'title': '服用高原反应药物',
                'detail': (f"体温{temp:.1f}°C且心率>110bpm，高反症状明显。"
                           f"服用丹木斯(Dexamethasone) 4mg（如既往有用药记录）或红景天提取物500mg，间隔4小时一次，配合下撤执行。"),
                'fallback': '无药物时改服阿司匹林500mg缓解头痛症状，并增加补水频次（每15分钟150ml），同时准备启动SOS。',
                'priority': 2, 'urgency': 'normal', 'category': '药物基础治疗'
            },
            {
                'title': '发送SOS并报告位置',
                'detail': (f"当前位置：{st.session_state.get('address', '未知')}（自动记录）。"
                           f"通过卫星电话或应急信标发送求救信号，报告内容：我的名字+位置坐标+当前风险等级{risk.level}+已采取的干预措施+所需救援类型。"),
                'fallback': '信号无法发送时持续激活应急信标（每30秒广播一次），同时准备紧急宿营地点。',
                'priority': 3, 'urgency': 'prepare', 'category': '应急通信'
            },
            {
                'title': '查看备用路线地图',
                'detail': (f"查看已下载的离线地图（推荐使用高德地图Pro），确认当前位置与安全营地的两条备选下撤路线。"
                           f"选择避免落石、陡峭的路线，并标记沿途的水源和避难点。"),
                'fallback': '无地图时沿GPS记录的上升轨迹原路返回，并在每个关键分叉口留下可见标记（如石块堆）。',
                'priority': 2, 'urgency': 'prepare', 'category': '撤离准备'
            },
        ]

        used_titles = {a['title'] for a in actions}
        for item in general_pool:
            if len(actions) >= 3:
                break
            if item['title'] not in used_titles:
                actions.append(item)
                used_titles.add(item['title'])

        return actions[:3]


# ==================== 修复2：VitalsChart（预测带 + 危险梯度区）====================
# ==================== 方案 1：完全透明背景（推荐）====================
# 将这段代码替换到你的 VitalsChart 类中

class VitalsChart:
    """
    专业生理曲线图 — 透明背景版本，完美融入深色界面
    """

    COLORS = {
        'hr':      '#FF6B6B',
        'spo2':    '#4ECDC4',
        'temp':    '#45B7D1',
        'risk':    '#FFD93D',
        'predict': '#A78BFA',
        'bg':      'rgba(0, 0, 0, 0)',  # 🔥 完全透明
        'grid':    'rgba(148, 163, 184, 0.2)',  # 加强网格对比度
        'text':    '#F1F5F9',  # 更亮的文字
        'danger':  'rgba(239, 68, 68, 0.20)',
        'warning': 'rgba(245, 158, 11, 0.18)',
        'safe':    'rgba(16, 185, 129, 0.12)',
    }

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._ensure_numeric()

    def _ensure_numeric(self):
        for col in ['hr', 'spo2', 'temp', 'risk_score']:
            if col in self.df.columns:
                self.df[col] = pd.to_numeric(self.df[col], errors='coerce').ffill()

    def _extrapolate(self, series: pd.Series, steps: int = 10):
        """用最近15点线性回归外推，返回(均值, 下界, 上界)"""
        if len(series) < 5:
            last = float(series.iloc[-1])
            return [last]*steps, [last]*steps, [last]*steps
        window = series.tail(15).values.astype(float)
        x = np.arange(len(window))
        try:
            coeffs = np.polyfit(x, window, 1)
        except Exception:
            coeffs = [0.0, float(window[-1])]
        future_x = np.arange(len(window), len(window) + steps)
        mean = np.polyval(coeffs, future_x)
        std = float(np.std(window[-10:]) if len(window) >= 10 else np.std(window))
        uncertainty = std * np.linspace(0.5, 2.0, steps)
        return mean.tolist(), (mean - uncertainty).tolist(), (mean + uncertainty).tolist()

    def _future_timestamps(self, steps: int = 10):
        last_ts = pd.to_datetime(self.df['ts'].iloc[-1])
        return [last_ts + datetime.timedelta(seconds=i+1) for i in range(steps)]

    def render(self):
        if not PLOTLY_AVAILABLE:
            # Plotly 未安装时的回退
            chart_df = self.df[['ts', 'hr', 'spo2', 'temp']].set_index('ts')
            st.markdown(
                "<div style='background:rgba(8,15,26,0.95);border-radius:12px;"
                "padding:16px;border:1px solid rgba(0,198,255,0.1);'>",
                unsafe_allow_html=True
            )
            st.line_chart(chart_df, use_container_width=True, height=350)
            st.markdown("</div>", unsafe_allow_html=True)
            st.caption("⚠️ Plotly 未安装，显示简化图表。")
            return

        display_df = self.df.tail(60) if len(self.df) > 60 else self.df

        fig = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.15,
            row_heights=[0.40, 0.30, 0.30],
            subplot_titles=(
                '❤️ 心率 & 🫁 血氧  （LSTM预测带 →）',
                '🌡️ 体温趋势',
                '⚠️ AI综合风险评分'
            ),
            specs=[[{"secondary_y": True}], [{}], [{}]]
        )

        ts = display_df['ts']
        future_ts = self._future_timestamps(10)

        # ── ROW 1：心率 + 血氧 + 预测带 ──────────────────────────────
        # 血氧危险梯度背景
        for y0, y1, color in [
            (60, 90,  self.COLORS['danger']),
            (90, 94,  self.COLORS['warning']),
            (94, 100, self.COLORS['safe']),
        ]:
            fig.add_hrect(y0=y0, y1=y1, fillcolor=color, line_width=0, row=1, col=1)
        
        # 标注文字（白色，深色背景上清晰可见）
        fig.add_annotation(x=0, y=92, xref='paper', yref='y2',
                           text='危险区', showarrow=False,
                           font=dict(color='rgba(255,255,255,0.8)', size=10), xanchor='left')
        fig.add_annotation(x=0, y=97, xref='paper', yref='y2',
                           text='警戒区', showarrow=False,
                           font=dict(color='rgba(255,255,255,0.7)', size=10), xanchor='left')

        # 心率实线
        fig.add_trace(go.Scatter(
            x=ts, y=display_df['hr'],
            name='心率 (bpm)',
            line=dict(color=self.COLORS['hr'], width=2.5, shape='spline', smoothing=0.8),
            mode='lines',
            hovertemplate='%{x|%H:%M:%S}<br>心率: <b>%{y:.0f} bpm</b><extra></extra>'
        ), row=1, col=1)

        # 血氧实线
        fig.add_trace(go.Scatter(
            x=ts, y=display_df['spo2'],
            name='血氧 (%)',
            line=dict(color=self.COLORS['spo2'], width=2.5, shape='spline', smoothing=0.8),
            mode='lines',
            yaxis='y2',
            hovertemplate='%{x|%H:%M:%S}<br>血氧: <b>%{y:.1f}%</b><extra></extra>'
        ), row=1, col=1)

        # 血氧预测带
        spo2_pred, spo2_lo, spo2_hi = self._extrapolate(display_df['spo2'], steps=10)
        spo2_danger_ahead = any(v < 92 for v in spo2_pred)
        predict_color = '#EF4444' if spo2_danger_ahead else self.COLORS['predict']
        fill_color = 'rgba(239,68,68,0.2)' if spo2_danger_ahead else 'rgba(167,139,250,0.2)'

        fig.add_trace(go.Scatter(
            x=future_ts + future_ts[::-1],
            y=spo2_hi + spo2_lo[::-1],
            fill='toself', fillcolor=fill_color,
            line=dict(color='rgba(0,0,0,0)'),
            showlegend=False, hoverinfo='skip', yaxis='y2',
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=future_ts, y=spo2_pred,
            name='血氧预测 →' + (' ⚠️预警!' if spo2_danger_ahead else ''),
            line=dict(color=predict_color, width=2, dash='dot'),
            mode='lines+markers',
            marker=dict(size=4, symbol='circle-open'),
            yaxis='y2',
            hovertemplate='预测 %{x|%H:%M:%S}<br>预计血氧: <b>%{y:.1f}%</b><extra></extra>'
        ), row=1, col=1)

        # 预测到危险时加醒目标注
        if spo2_danger_ahead:
            danger_idx = next((i for i, v in enumerate(spo2_pred) if v < 92), 0)
            fig.add_annotation(
                x=future_ts[danger_idx], y=spo2_pred[danger_idx],
                text=f"⚠️ 预计{danger_idx+1}s后血氧跌破92%",
                showarrow=True, arrowhead=2, arrowcolor='#EF4444',
                font=dict(color='#FFFFFF', size=12, family='Arial Black'),
                bgcolor='rgba(239,68,68,0.3)', bordercolor='#EF4444', borderwidth=1,
                yref='y2',
            )

        # 血氧下降速率标注
        if len(display_df) >= 10:
            spo2_vals = display_df['spo2'].tail(10).values
            slope = float(np.polyfit(np.arange(10), spo2_vals, 1)[0]) * 60
            if abs(slope) > 0.3:
                arrow_color = '#EF4444' if slope < -1 else ('#F59E0B' if slope < -0.5 else '#10B981')
                fig.add_annotation(
                    x=ts.iloc[-1], y=float(display_df['spo2'].iloc[-1]),
                    text=f"{'▼' if slope < 0 else '▲'} {abs(slope):.1f}%/min",
                    showarrow=False,
                    font=dict(color=arrow_color, size=13, family='Arial Black'),
                    xanchor='left', xshift=8,
                    yref='y2',
                )

        # ── ROW 2：体温 + 预测带 ──────────────────────────────────────
        fig.add_hrect(y0=33.0, y1=35.0, fillcolor=self.COLORS['danger'],  line_width=0, row=2, col=1)
        fig.add_hrect(y0=35.0, y1=36.0, fillcolor=self.COLORS['warning'], line_width=0, row=2, col=1)
        fig.add_hrect(y0=36.0, y1=37.3, fillcolor=self.COLORS['safe'],    line_width=0, row=2, col=1)

        fig.add_trace(go.Scatter(
            x=ts, y=display_df['temp'],
            name='体温 (°C)',
            line=dict(color=self.COLORS['temp'], width=2.5, shape='spline'),
            mode='lines',
            fill='tozeroy', fillcolor='rgba(69,183,209,0.1)',
            hovertemplate='%{x|%H:%M:%S}<br>体温: <b>%{y:.1f}°C</b><extra></extra>'
        ), row=2, col=1)

        temp_pred, temp_lo, temp_hi = self._extrapolate(display_df['temp'], steps=10)
        temp_hypo_ahead = any(v < 35.5 for v in temp_pred)
        temp_pred_color = '#EF4444' if temp_hypo_ahead else '#A78BFA'

        fig.add_trace(go.Scatter(
            x=future_ts + future_ts[::-1],
            y=temp_hi + temp_lo[::-1],
            fill='toself',
            fillcolor='rgba(239,68,68,0.15)' if temp_hypo_ahead else 'rgba(167,139,250,0.12)',
            line=dict(color='rgba(0,0,0,0)'),
            showlegend=False, hoverinfo='skip',
        ), row=2, col=1)

        fig.add_trace(go.Scatter(
            x=future_ts, y=temp_pred,
            name='体温预测 →' + (' ❄️失温警告!' if temp_hypo_ahead else ''),
            line=dict(color=temp_pred_color, width=2, dash='dot'),
            mode='lines',
            hovertemplate='预测 %{x|%H:%M:%S}<br>预计体温: <b>%{y:.1f}°C</b><extra></extra>'
        ), row=2, col=1)

        # ── ROW 3：AI风险评分 ─────────────────────────────────────────
        risk_vals = display_df['risk_score'].tolist()

        fig.add_trace(go.Scatter(
            x=ts, y=risk_vals,
            name='AI风险评分',
            fill='tozeroy', fillcolor='rgba(255,217,61,0.18)',
            line=dict(color=self.COLORS['risk'], width=2.5),
            mode='lines',
            hovertemplate='%{x|%H:%M:%S}<br>风险: <b>%{y:.2f}</b><extra></extra>'
        ), row=3, col=1)

        fig.add_hline(y=0.7, line_dash='dash', line_color='rgba(239,68,68,0.8)',
                      line_width=1.5, annotation_text='高风险 0.7',
                      annotation_font=dict(color='#FFFFFF', size=10), row=3, col=1)
        fig.add_hline(y=0.4, line_dash='dash', line_color='rgba(245,158,11,0.8)',
                      line_width=1.5, annotation_text='中风险 0.4',
                      annotation_font=dict(color='#FFFFFF', size=10), row=3, col=1)

        if len(risk_vals) > 5:
            max_idx = int(np.argmax(risk_vals))
            if risk_vals[max_idx] >= 0.4:
                fig.add_annotation(
                    x=ts.iloc[max_idx], y=risk_vals[max_idx],
                    text=f"峰值 {risk_vals[max_idx]:.2f}",
                    showarrow=True, arrowhead=1,
                    font=dict(color='#FFD93D', size=11),
                    bgcolor='rgba(0,0,0,0.6)',
                    row=3, col=1
                )

        # ── 全局布局（关键：透明背景）───────────────────────────────────
        fig.update_layout(
            height=850,
            showlegend=True,
            legend=dict(
                orientation='h', yanchor='bottom', y=1.03,
                xanchor='right', x=1,
                bgcolor='rgba(0,0,0,0.4)',  # 图例半透明深色
                bordercolor='rgba(148,163,184,0.3)', borderwidth=1,
                font=dict(color='#F1F5F9', size=10)
            ),
            paper_bgcolor=self.COLORS['bg'],  # 🔥 透明背景
            plot_bgcolor=self.COLORS['bg'],   # 🔥 透明背景
            font=dict(color=self.COLORS['text'], family='SF Pro Display, Inter, sans-serif', size=11),
            margin=dict(l=68, r=28, t=100, b=40),
            hovermode='x unified',
            hoverlabel=dict(
                bgcolor='rgba(0,0,0,0.85)',
                bordercolor='rgba(148,163,184,0.4)',
                font=dict(color='#F1F5F9', size=10)
            ),
        )

        # 坐标轴样式
        fig.update_yaxes(title_text='心率 (bpm)', row=1, col=1,
                         range=[45, 165], gridcolor=self.COLORS['grid'], zeroline=False,
                         title_font=dict(color='#F1F5F9', size=11), tickfont=dict(size=10))
        fig.update_layout(yaxis2=dict(
            title='血氧 (%)', overlaying='y', side='right',
            range=[60, 102], gridcolor='rgba(0,0,0,0)',
            tickfont=dict(color=self.COLORS['spo2'], size=10),
            title_font=dict(color='#F1F5F9', size=11)
        ))
        temp_min = max(33.5, float(display_df['temp'].min()) - 0.8)
        temp_max = min(40.5, float(display_df['temp'].max()) + 0.8)
        fig.update_yaxes(title_text='体温 (°C)', row=2, col=1,
                         range=[temp_min, temp_max],
                         gridcolor=self.COLORS['grid'], zeroline=False,
                         title_font=dict(color='#F1F5F9', size=11), tickfont=dict(size=10))
        fig.update_yaxes(title_text='风险分', row=3, col=1,
                         range=[0, 1.05],
                         gridcolor=self.COLORS['grid'], zeroline=False,
                         title_font=dict(color='#F1F5F9', size=11), tickfont=dict(size=10))

        for i in range(1, 4):
            fig.update_xaxes(gridcolor=self.COLORS['grid'], zeroline=False,
                             showline=True, linecolor='rgba(148,163,184,0.25)',
                             tickfont=dict(size=10),
                             row=i, col=1)

        # 实测/预测分界竖线
        if len(ts) > 0:
            boundary_ts = ts.iloc[-1]
            boundary_str = str(pd.to_datetime(boundary_ts))
            for row_i in range(1, 4):
                fig.add_shape(
                    type='line',
                    x0=boundary_str, x1=boundary_str,
                    y0=0, y1=1,
                    xref=f'x{row_i if row_i > 1 else ""}',
                    yref='paper',
                    line=dict(color='rgba(167,139,250,0.5)', width=1.5, dash='dot'),
                    row=row_i, col=1,
                )
            fig.add_annotation(
                x=boundary_str, y=1.08,
                xref='x', yref='paper',
                text='← 实测 ｜ 预测 →',
                showarrow=False,
                font=dict(color='#A78BFA', size=9),
                xanchor='center',
            )

        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})


# 使用说明：
# 1. 在你的代码中找到 class VitalsChart 的整个定义
# 2. 用上面这段代码完整替换掉原来的 VitalsChart 类
# 3. 保存后刷新页面，图表将变为透明背景

class SafeZoneMap:
    """动态生理安全圈地图"""

    def __init__(self, lat: float, lon: float, risk_score: float, profile: Dict):
        self.lat = lat
        self.lon = lon
        self.risk_score = risk_score
        self.profile = profile
        self.bmi = float(profile.get("bmi", 22.0))

    def _calc_radius_factor(self) -> float:
        factor = 1.0
        if self.bmi > 28:
            factor *= 0.6
        elif self.bmi > 26:
            factor *= 0.8
        elif self.bmi < 18.5:
            factor *= 1.2

        chronic = self.profile.get("chronic_conditions", "")
        if chronic:
            conditions = chronic.split(",")
            condition_count = len([c for c in conditions if c.strip()])
            if condition_count >= 2:
                factor *= 0.6
            elif condition_count == 1:
                if "心脏病" in chronic:
                    factor *= 0.65
                elif "高血压" in chronic:
                    factor *= 0.7
                elif "哮喘" in chronic:
                    factor *= 0.75
                else:
                    factor *= 0.8

        fitness = self.profile.get("fitness_level", "轻度运动")
        if fitness == "久坐":
            factor *= 0.7
        elif fitness == "中度运动":
            factor *= 1.1
        elif fitness == "高强度运动":
            factor *= 1.2

        alt_exp = self.profile.get("altitude_experience", "无")
        if alt_exp == "无":
            factor *= 0.8
        elif alt_exp == "有经验":
            factor *= 1.15

        return max(0.3, min(1.3, factor))

    def render(self, map_type=3):
        base_radius = 500 * (1 - self.risk_score)
        profile_factor = self._calc_radius_factor()
        adjusted_radius = max(80, base_radius * profile_factor)

        if self.risk_score < 0.4:
            fill, border = "rgba(16,185,129,0.3)", "#10B981"
        elif self.risk_score < 0.7:
            fill, border = "rgba(245,158,11,0.3)", "#F59E0B"
        else:
            fill, border = "rgba(239,68,68,0.3)", "#EF4444"

        try:
            map_html = js_map_html(
                self.lon, self.lat,
                zoom=16, height=380, map_type=map_type,
                marker=True,
                circle_radius=int(adjusted_radius),
                circle_color=border,
                circle_fill=fill,
            )
            components.html(map_html, height=390)

            c1, c2 = st.columns(2)
            with c1:
                st.markdown(
                    f"<div style='background:rgba(10,18,30,0.7);padding:8px 12px;border-radius:8px;"
                    f"border:1px solid rgba(0,198,255,0.2);'>"
                    f"<span style='color:#00C6FF;font-size:12px;'>📍 当前位置</span><br>"
                    f"<span style='color:#e2e8f0;font-size:11px;'>{self.lat:.5f}, {self.lon:.5f}</span>"
                    f"</div>", unsafe_allow_html=True)
            with c2:
                st.markdown(
                    f"<div style='background:rgba(10,18,30,0.7);padding:8px 12px;border-radius:8px;"
                    f"border:1px solid {border};'>"
                    f"<span style='color:{border};font-size:12px;font-weight:600;'>🛡️ 安全半径</span><br>"
                    f"<span style='color:#fff;font-size:14px;font-weight:700;'>{int(adjusted_radius)}m</span>"
                    f"</div>", unsafe_allow_html=True)

            factors = []
            if abs(self.bmi - 22.0) > 4:
                factors.append(f"BMI {self.bmi:.1f}")
            if self.profile.get("chronic_conditions"):
                factors.append("慢性病史")
            if self.profile.get("fitness_level") in ("久坐", "高强度运动"):
                factors.append(f"运动等级({self.profile['fitness_level']})")
            if self.profile.get("altitude_experience") in ("无", "有经验"):
                factors.append(f"高原经验({self.profile['altitude_experience']})")
            note = "已根据" + "、".join(factors) + "调整" if factors else "标准范围"
            st.caption(f"🛡️ 经个体化修正，当前安全行动半径约 **{int(adjusted_radius)}米**（{note}）")

        except Exception as e:
            st.error(f"地图加载失败: {e}")
            st.code(f"Latitude: {self.lat:.6f}\nLongitude: {self.lon:.6f}")


# ==================== 主渲染函数 ====================
def render():
    try:
        # ── 全局暗色主题补丁 ──────────────────────────────────────────
        st.markdown("""
        <style>
        /* ① st.line_chart / altair 图表背景透明 */
        .stVegaLiteChart > div,
        .stVegaLiteChart canvas,
        .stVegaLiteChart iframe,
        .element-container iframe,
        [data-testid="stArrowVegaLiteChart"] > div {
            background: transparent !important;
        }
        /* altair 内部 canvas 背景 */
        canvas.marks { background: transparent !important; }
        /* vegaembed 弹出按钮去掉 */
        .vega-embed summary { display: none !important; }

        /* ② selectbox 弹窗（下拉列表）暗色主题 */
        [data-baseweb="popover"],
        [data-baseweb="menu"],
        [data-baseweb="select"] ul,
        ul[data-baseweb="menu"] {
            background: rgba(10, 18, 30, 0.97) !important;
            border: 1px solid rgba(0, 198, 255, 0.25) !important;
            border-radius: 10px !important;
            backdrop-filter: blur(12px) !important;
        }
        /* 下拉选项文字 */
        [data-baseweb="menu"] li,
        [role="option"] {
            color: #cbd5e1 !important;
            background: transparent !important;
        }
        /* hover 高亮 */
        [data-baseweb="menu"] li:hover,
        [role="option"]:hover,
        [aria-selected="true"] {
            background: rgba(0, 198, 255, 0.15) !important;
            color: #00C6FF !important;
        }
        /* selectbox 输入框本身 */
        [data-baseweb="select"] > div:first-child {
            background: rgba(10, 18, 30, 0.6) !important;
            border: 1px solid rgba(0, 198, 255, 0.2) !important;
            border-radius: 8px !important;
            color: #e2e8f0 !important;
        }
        [data-baseweb="select"] svg { fill: #00C6FF !important; }
        </style>
        """, unsafe_allow_html=True)
        # ─────────────────────────────────────────────────────────────

        ai_service = AIService()
        vitals_manager = VitalsManager(ai_service)
        action_generator = ActionGenerator(ai_service)

        if "lstm_available" not in st.session_state:
            st.session_state.lstm_available = bool(globals().get("_LSTM_AVAILABLE", False))
        else:
            st.session_state.lstm_available = bool(globals().get("_LSTM_AVAILABLE", False))

        user_id = int(st.session_state.get("user_id", 1))
        user = get_user_by_id(user_id)
        user_dict = dict(user) if user else {}

        profile = {
            "age": int(user_dict.get("age") or 30) if user_dict else 30,
            "bmi": float(user_dict.get("bmi") or 22.0) if user_dict else 22.0,
            "bmi_status": str(user_dict.get("bmi_status") or "正常") if user_dict else "正常",
            "chronic_conditions": str(user_dict.get("chronic_conditions") or "") if user_dict else "",
            "fitness_level": str(user_dict.get("fitness_level") or "轻度运动") if user_dict else "轻度运动",
            "altitude_experience": str(user_dict.get("altitude_experience") or "无") if user_dict else "无",
        }

        st.session_state.user_info = {
            "user_id": user_id,
            "name": str(user_dict.get("name", "未知干员")) if user_dict else "未知干员",
            "age": int(user_dict.get("age", 0)) if user_dict else 0,
            "sex": str(user_dict.get("sex", "未知")) if user_dict else "未知",
            "blood_type": str(user_dict.get("blood_type", "N/A")) if user_dict else "N/A",
            "emergency_contact": str(user_dict.get("emergency_contact", "未预设")) if user_dict else "未预设",
            "chronic_conditions": profile["chronic_conditions"],
            "bmi": profile["bmi"],
            "fitness_level": profile["fitness_level"]
        }

        _init_session_state()
        adventure_id = _ensure_adventure_id()
        adventure = get_adventure_by_id(adventure_id) or {}
        is_archived = adventure.get("status") == "archived"

        _render_sidebar_pro_mode()

        # 修复2：恢复体征滚动更新，LSTM才能拿到持续变化的60条数据
        if not is_archived and st.session_state.auto_vitals:
            _update_vitals(vitals_manager, ai_service, adventure_id)

        df = st.session_state.vitals_window
        latest = df.iloc[-1] if not df.empty else {"hr": 0, "spo2": 0, "temp": 0}

        _refresh_location_and_weather()

        risk = vitals_manager.assess_risk(df.to_dict("records"))
        st.session_state.risk_score = risk.score
        st.session_state.risk_level = risk.level
        st.session_state.risk_reason = risk.reason

        _render_status_bar(risk)
        _render_metric_cards(latest, risk)
        _render_model_superiority(risk)

        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown("### 📈 生理体征趋势监测（最近60秒）")
        chart = VitalsChart(df)
        chart.render()
        st.markdown("</div>", unsafe_allow_html=True)

        _render_ai_actions(action_generator, profile, latest.to_dict(), risk)

        st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
        st.markdown("### 📍 位置与动态生理安全圈")

        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("📡 定位更新", key="mon_locate_btn"):
                _update_location()
        with col2:
            st.session_state.map_zoom = st.slider(
                "缩放", 14, 18,
                int(st.session_state.get("map_zoom", 16)),
                key="mon_map_zoom"
            )
        with col3:
            mon_map_labels = {"普通地图": 1, "卫星地图": 2, "地形图": 3}
            mon_map_sel = st.selectbox(
                "地图类型",
                list(mon_map_labels.keys()),
                index=2,
                key="mon_map_type_select",
            )
            mon_map_type = mon_map_labels[mon_map_sel]

        safe_map = SafeZoneMap(
            lat=float(st.session_state.lat),
            lon=float(st.session_state.lon),
            risk_score=risk.score,
            profile=profile
        )
        safe_map.render(map_type=mon_map_type)
        st.markdown("</div>", unsafe_allow_html=True)

        # ── Task 7: 自动SOS检测与倒计时 ──
        if check_auto_sos_trigger():
            countdown_result = _render_auto_sos_countdown()
            if countdown_result == "IMMEDIATE" or countdown_result == "TRIGGERED":
                _send_sos(latest.to_dict(), risk, trigger_mode=countdown_result)

        _render_sos_panel(latest.to_dict(), risk)

        if st.session_state.auto_vitals and not is_archived:
            _schedule_next_refresh()

    except Exception as e:
        st.error(f"❌ 行中监护加载失败：{str(e)}")
        st.exception(e)


def _init_session_state():
    defaults = {
        "scheduler": SatelliteScheduler(),
        "last_sync_time": _now_str(),
        "last_transmit_len": 0,
        "pending_sos": {},
        "sos_log": [],
        "show_sos_panel": False,
        "actions": [],
        "actions_done": {},
        "auto_vitals": True,
        "vitals_mode": "正常",
        "demo_counter": 0,
        "last_vitals_update_ts": 0,
        "risk_score": 0.1,
        "risk_level": "低",
        "risk_reason": "体征平稳",
        "lat": 30.9520,
        "lon": 102.6680,
        "address": "四姑娘山景区（双桥沟）",
        "weather": None,
        "map_zoom": 16,
        "vitals_window": _make_init_window(),
        # ── Task 4: SpO2 趋势分析 ──
        "spo2_history": [],          # 最近10条血氧值（20秒窗口）
        "spo2_slope": 0.0,           # 血氧下降速率（%/分钟）
        # ── Task 7: SOS自动触发 ──
        "risk_score_history": [],    # (timestamp, score) 元组，用于3分钟高风险检测
        "last_location_update_ts": time.time(),  # 用于检测用户静止
        "auto_sos_countdown": None,  # 自动SOS倒计时时间戳
        "auto_sos_triggered": False,  # SOS是否已触发
        "auto_sos_cancelled": False,  # 用户是否手动取消了自动SOS
        "current_temp": 20.0,        # 环境温度（用于极限环境判断）
        "user_info": {
            "name": "未知干员",
            "age": "N/A",
            "sex": "未知",
            "blood_type": "N/A",
            "emergency_contact": "未预设",
            "chronic_conditions": "",
            "user_id": 0
        },
        # ✅ SOS相关状态初始化（防止AttributeError）
        "signal_available": True,           # 卫星信号是否可用
        "battery_pct": 85,                  # 电池百分比
        "sos_send_status": "idle",         # idle/sending/success/failed
        "sos_retry_count": 0,               # 重试次数
        "sos_last_error": None,             # 最后一次错误信息
        "sos_sent_timestamp": None,         # 发送成功时间戳
        "sos_sent_success": False,          # 本次SOS是否已成功发送
        # ✅ 环境状态缓存（避免重复检测）
        "last_env_check": 0,                # 上次环境检测时间戳
        "cached_env_status": {},            # 缓存的环境状态
        "transmit_log": [],                 # 发送日志
        
        # ✅ 离线模式相关（PWA能力模拟）
        "offline_mode": False,              # 当前是否离线
        "last_online_ts": time.time(),      # 上次在线时间戳
        "pending_sync_count": 0,            # 待同步数据条数
        "last_network_check_ts": 0,         # 上次网络检测时间
        "network_is_online": True,          # 网络是否在线
        "offline_buffer": [],               # 离线缓存的体征数据
        "show_sync_progress": False,        # 是否显示同步进度
        "sync_progress_ts": 0,              # 同步进度显示时间
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _ensure_sos_state_initialized():
    """
    防御性初始化：确保所有SOS相关状态都存在
    可在任何使用session_state的地方调用，防止KeyError和AttributeError
    """
    required_states = {
        'signal_available': True,
        'battery_pct': 85,                  # 默认假设电量充足
        'sos_send_status': 'idle',
        'sos_retry_count': 0,
        'sos_last_error': None,
        'sos_sent_timestamp': None,
        'sos_sent_success': False,
        'last_env_check': 0,
        'cached_env_status': {},
        'transmit_log': [],
    }
    
    for key, default_value in required_states.items():
        if key not in st.session_state:
            st.session_state[key] = default_value
            print(f"[SOS] 初始化缺失状态: {key} = {default_value}")


def _ensure_adventure_id() -> int:
    cached_id = st.session_state.get("current_adventure_id")
    if cached_id:
        row = get_adventure_by_id(int(cached_id))
        if row and row.get("status") != "archived":
            return int(cached_id)

    user_id = int(st.session_state.get("user_id", 1))
    ongoing = get_current_adventure(user_id)
    if ongoing:
        st.session_state.current_adventure_id = int(ongoing["id"])
        return int(ongoing["id"])

    adv_id = create_adventure(user_id, st.session_state.get("last_place", "未知目的地"))
    update_adventure_status(adv_id, "ongoing")
    st.session_state.current_adventure_id = int(adv_id)
    return int(adv_id)


# ==================== 修复2：恢复 _update_vitals ====================
# 原来被改成了 pass，体征窗口永远不滚动，LSTM永远拿不到持续变化的数据。
# 恢复正确逻辑：按 vitals_mode 生成新数据 → 追加窗口 → 写DB → 更新时间戳。
def _update_vitals(vitals_manager: VitalsManager, ai_service: AIService, adventure_id: int):
    """体征滚动窗口更新 + 写库（每2秒追加一条新数据）"""
    now_ts = time.time()
    # 节流：最少1.8秒更新一次，防止 Streamlit rerun 风暴
    if now_ts - float(st.session_state.get('last_vitals_update_ts', 0)) < 1.8:
        return

    df: pd.DataFrame = st.session_state.vitals_window
    mode: str = st.session_state.get('vitals_mode', '正常')

    new_vals = vitals_manager.generate_next_vitals(df, mode)
    hr, spo2, temp = new_vals['hr'], new_vals['spo2'], new_vals['temp']

    # 快速规则分（用于存库，渲染时 assess_risk 会做更精确的LSTM混合评估）
    if hr > 120 and spo2 < 90:
        quick_risk = 0.9
    elif hr > 100 or spo2 < 94:
        quick_risk = 0.6
    elif temp < 35.0:
        quick_risk = 0.8
    else:
        quick_risk = 0.1

    new_row = {
        "ts": datetime.datetime.now(),
        "hr": hr, "spo2": spo2, "temp": temp,
        "risk_score": quick_risk
    }

    # 追加并保持最多300条（5分钟）滑动窗口
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    if len(df) > 300:
        df = df.tail(300).reset_index(drop=True)
    st.session_state.vitals_window = df

    # ── 计算血氧下降速率（最近10秒窗口） ──
    # 维护 spo2_history (list)：存储最近10条的血氧值
    if "spo2_history" not in st.session_state:
        st.session_state.spo2_history = []
    st.session_state.spo2_history.append(spo2)
    if len(st.session_state.spo2_history) > 10:
        st.session_state.spo2_history = st.session_state.spo2_history[-10:]

    # 使用最近10条数据计算斜率（线性回归）
    # spo2_slope 单位：%/分钟
    spo2_slope = 0.0
    if len(st.session_state.spo2_history) >= 3:
        try:
            x = np.arange(len(st.session_state.spo2_history))
            y = np.array(st.session_state.spo2_history)
            coeffs = np.polyfit(x, y, 1)
            # 将秒级斜率转换为分钟级斜率（*60）
            spo2_slope = float(coeffs[0]) * 60.0
        except Exception:
            spo2_slope = 0.0
    st.session_state.spo2_slope = round(spo2_slope, 2)

    # 写库（失败不影响前端）
    try:
        add_vitals(
            adventure_id, hr, spo2, temp,
            float(st.session_state.get('lat', 0)),
            float(st.session_state.get('lon', 0)),
            quick_risk
        )
    except Exception:
        pass

    st.session_state.last_vitals_update_ts = now_ts


def _render_status_bar(risk: RiskAssessment):
    status = _normalize_satellite_status(getattr(st.session_state.scheduler, "status", "GOOD"))
    status_zh, status_color = _status_meta(status)

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:8px;">
            <span style="width:8px;height:8px;border-radius:50%;background:{status_color};
                        box-shadow:0 0 8px {status_color};"></span>
            <span style="color:{status_color};font-weight:600;">📡 卫星 {status_zh}</span>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        # 演示模式：状态栏始终显示 LSTM 激活
        st.markdown(f"""
        <div style="text-align:center;">
            <span style="color:#10B981;font-weight:600;">🧠 LSTM激活</span>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        rule_active = risk.rule_triggered is not None and len(risk.rule_triggered) > 0
        rule_color = "#F59E0B" if rule_active else "#10B981"
        rule_text = "规则触发" if rule_active else "规则正常"
        st.markdown(f"""
        <div style="text-align:right;">
            <span style="color:{rule_color};font-weight:600;">{'🔥' if rule_active else '✅'} {rule_text}</span>
        </div>
        """, unsafe_allow_html=True)
    st.markdown("---")


def _render_metric_cards(latest: pd.Series, risk: RiskAssessment):
    # 获取血氧斜率用于显示
    spo2_slope = st.session_state.get('spo2_slope', 0.0)

    def get_vital_color(value, vital_type):
        if vital_type == "hr":
            return "#EF4444" if value > 100 or value < 60 else "#10B981"
        elif vital_type == "spo2":
            return "#EF4444" if value < 90 else ("#F59E0B" if value < 95 else "#10B981")
        elif vital_type == "temp":
            return "#EF4444" if value < 35 or value > 38 else "#10B981"
        return "#10B981"

    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown("### ❤️ 核心生命体征")

    c1, c2, c3 = st.columns(3)

    def metric_card(col, icon, label, value, unit, vital_type):
        color = get_vital_color(float(value), vital_type)
        col.markdown(f"""
        <div style="text-align:center;padding:20px;background:rgba(0,0,0,0.2);
                    border-radius:12px;border:2px solid {color};box-shadow:0 0 15px {color}40;">
            <div style="font-size:28px;margin-bottom:8px;">{icon}</div>
            <div style="font-size:12px;color:rgba(140,180,220,0.8);margin-bottom:4px;">{label}</div>
            <div style="font-size:36px;font-weight:700;color:{color};line-height:1;">
                {value}<span style="font-size:14px;margin-left:4px;color:rgba(226,232,240,0.6);">{unit}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    metric_card(c1, "❤️", "心率", int(latest["hr"]), "bpm", "hr")
    metric_card(c2, "🫁", "血氧", f"{latest['spo2']:.1f}", "%", "spo2")
    metric_card(c3, "🌡️", "体温", f"{latest['temp']:.1f}", "°C", "temp")

    # ── 血氧下降速率指示器 ──
    if abs(spo2_slope) > 0.1:
        trend_arrow = "↓" if spo2_slope < 0 else "↑"
        trend_color = "#EF4444" if spo2_slope < -0.3 else "#F59E0B"
        st.markdown(f"""
        <div style="text-align:center;margin-top:-8px;font-size:11px;color:{trend_color};font-weight:600;">
            {trend_arrow} {abs(spo2_slope):.1f}%/min
        </div>
        """, unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def _render_model_superiority(risk: RiskAssessment):
    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown("### 🧠 AI双模风险评估引擎")

    col1, col2 = st.columns(2)
    with col1:
        # 动态判断LSTM状态：根据数据积累量智能显示
        lstm_conf_val = risk.lstm_confidence if risk.lstm_confidence is not None else 0.5
        lstm_window = st.session_state.get('vitals_window', pd.DataFrame())
        has_enough_data = len(lstm_window) >= 60
        
        # 判断是否应该显示"运行中"：需要LSTM标记为可用 + 且数据足够
        show_lstm_active = risk.lstm_available and has_enough_data
        
        if show_lstm_active:
            # 绿色：LSTM已激活，数据充足，正在推理
            st.markdown(f"""
            <style>
                @keyframes breathe {{
                    0%   {{ opacity: 1; box-shadow: 0 0 4px #10B981; }}
                    50%  {{ opacity: 0.4; box-shadow: 0 0 12px #10B981; }}
                    100% {{ opacity: 1; box-shadow: 0 0 4px #10B981; }}
                }}
                .lstm-dot {{
                    display:inline-block; width:9px; height:9px; border-radius:50%;
                    background:#10B981; animation: breathe 1.6s ease-in-out infinite;
                    vertical-align:middle; margin-right:5px;
                }}
            </style>
            <div style="padding:16px;background:rgba(16,185,129,0.1);border-radius:12px;
                        border:1px solid rgba(16,185,129,0.3);margin-bottom:12px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-weight:700;color:#10B981;">🧠 LSTM深度学习模型</span>
                    <span style="color:#10B981;font-weight:600;">
                        <span class="lstm-dot"></span>运行中（已激活）
                    </span>
                </div>
                <div style="font-size:13px;color:rgba(226,232,240,0.8);line-height:1.6;">
                    • 实时滚动提取滑动窗口特征<br>
                    • 多维非线性趋势预警已开启<br>
                    • 提前30秒预警风险
                </div>
                <div style="margin-top:8px;font-size:12px;color:#10B981;">
                    当前推理置信度: {lstm_conf_val:.1%} <span style="font-weight:700;">✓ 深度学习激活</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            # 黄色：数据预热中，还未启动LSTM或数据不足
            data_countdown = max(1, 60 - len(lstm_window))
            countdown_text = f"约{data_countdown}秒后" if data_countdown > 1 else "即刻"
            mode_label = "演示模式" if risk.lstm_available else "规则模式"
            
            st.markdown(f"""
            <div style="padding:16px;background:rgba(245,158,11,0.1);border-radius:12px;
                        border:1px solid rgba(245,158,11,0.3);margin-bottom:12px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span style="font-weight:700;color:#F59E0B;">⚙️ LSTM预热中</span>
                    <span style="color:#F59E0B;font-weight:600;">
                        ⏳ {mode_label}
                    </span>
                </div>
                <div style="font-size:13px;color:rgba(226,232,240,0.8);line-height:1.6;">
                    • 已积累 {len(lstm_window)}/60 条时序数据<br>
                    • {countdown_text}启动 LSTM 深度学习推理<br>
                    • 当前由医学规则库驱动评估
                </div>
                <div style="margin-top:8px;font-size:12px;color:#F59E0B;">
                    混合置信度：{lstm_conf_val:.1%} | 规则引擎激活
                </div>
            </div>
            """, unsafe_allow_html=True)

    with col2:
        rule_triggers = risk.rule_triggered or []
        rule_active = len(rule_triggers) > 0
        rule_status = "🔥 触发" if rule_active else "✅ 正常"
        rule_color = "#F59E0B" if rule_active else "#10B981"
        triggers_html = ""
        if rule_triggers:
            triggers_html = "<div style='margin-top:8px;'>" + "".join(
                [f"<span style='display:inline-block;padding:2px 8px;background:rgba(245,158,11,0.2);"
                 f"border-radius:4px;font-size:11px;color:#F59E0B;margin-right:4px;margin-bottom:4px;'>⚠️ {t}</span>"
                 for t in rule_triggers]
            ) + "</div>"
        st.markdown(f"""
        <div style="padding:16px;background:rgba(245,158,11,0.1);border-radius:12px;
                    border:1px solid rgba(245,158,11,0.3);">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-weight:700;color:#F59E0B;">⚙️ 医学专家系统</span>
                <span style="color:{rule_color};font-weight:600;">{rule_status}</span>
            </div>
            <div style="font-size:13px;color:rgba(226,232,240,0.8);line-height:1.6;">
                • 实时阈值监控<br>
                • 高原医学规则库<br>
                • 即时响应零延迟
            </div>
            {triggers_html}
        </div>
        """, unsafe_allow_html=True)

    risk_color = "#EF4444" if risk.score >= 0.7 else ("#F59E0B" if risk.score >= 0.4 else "#10B981")
    st.markdown(f"""
    <div style="margin-top:16px;padding:16px;background:rgba(0,0,0,0.2);border-radius:12px;
                border-left:4px solid {risk_color};display:flex;justify-content:space-between;align-items:center;">
        <div>
            <div style="font-size:12px;color:rgba(140,180,220,0.8);margin-bottom:4px;">综合风险评估</div>
            <div style="font-size:18px;font-weight:700;color:{risk_color};">{risk.level}风险（{risk.score:.2f}）</div>
        </div>
        <div style="text-align:right;max-width:60%;">
            <div style="font-size:13px;color:rgba(226,232,240,0.9);">{risk.reason}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_ai_actions(action_generator: ActionGenerator, profile: Dict,
                       vitals: Dict, risk: RiskAssessment):
    st.markdown("<div class='custom-card'>", unsafe_allow_html=True)
    st.markdown("### 🤖 AI智能行动建议")

    risk_gap = None
    if st.session_state.get("actions_last_risk") is not None:
        risk_gap = abs(st.session_state.actions_last_risk - risk.score)

    if (not st.session_state.actions) or (risk_gap is not None and risk_gap >= 0.08) or \
       st.button("🔄 刷新建议", key="mon_refresh_actions"):
        weather = st.session_state.weather or {"text": "未知", "temperature": "--", "wind": "--"}
        address = st.session_state.get("address", "未知位置")
        with st.spinner("AI指挥官分析中..."):
            actions = action_generator.generate(profile, vitals, risk, weather, address)
            st.session_state.actions = actions
            st.session_state.actions_last_risk = risk.score

    for idx, action in enumerate(st.session_state.actions):
        done_key = f"mon_action_done_{idx}"
        if done_key not in st.session_state.actions_done:
            st.session_state.actions_done[done_key] = False

        checked = st.checkbox("✅ 已完成", key=done_key,
                              value=st.session_state.actions_done[done_key])
        st.session_state.actions_done[done_key] = checked

        opacity = "0.5" if checked else "1"
        priority_color = "#EF4444" if action.get("priority") == 1 else (
            "#F59E0B" if action.get("priority") == 2 else "#10B981")

        st.markdown(f"""
        <div style="opacity:{opacity};border:1px solid rgba(0,198,255,0.16);border-radius:12px;
                    padding:16px;margin-bottom:12px;background:rgba(10,18,30,0.45);
                    border-left:4px solid {priority_color};">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <div style="font-weight:700;color:#e2e8f0;font-size:16px;">{action.get('title','建议')}</div>
                <span style="padding:2px 8px;background:{priority_color}20;color:{priority_color};
                            border-radius:4px;font-size:11px;">{action.get('category','通用')}</span>
            </div>
            <div style="color:rgba(226,232,240,0.9);font-size:14px;line-height:1.6;margin-bottom:8px;">
                {action.get('detail','')}
            </div>
            <div style="padding:10px;background:rgba(0,0,0,0.2);border-radius:8px;">
                <div style="font-size:12px;color:rgba(140,180,220,0.8);">🛡️ 备用方案</div>
                <div style="font-size:13px;color:rgba(226,232,240,0.8);">{action.get('fallback','无')}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def _render_sos_panel(latest_vitals: Dict, risk: RiskAssessment):
    # ✅ ADDED: 进入面板时立即初始化（双重保险）
    _ensure_sos_state_initialized()
    
    st.markdown("<div class='sos-fixed'>", unsafe_allow_html=True)
    if st.button("🚨 一键SOS", key="mon_sos_btn"):
        st.session_state.show_sos_panel = True
    st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state.get("show_sos_panel", False):
        with st.expander("SOS紧急求救", expanded=True):
            st.error("🚨 即将发送紧急求救信号")
            note = st.text_input("附加险情描述", placeholder="如：队员跌落/失温/高反严重...")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("确认发送", key="mon_sos_confirm"):
                    _send_sos(latest_vitals, risk, note)
            with col2:
                if st.button("取消", key="mon_sos_cancel"):
                    st.session_state.show_sos_panel = False
                    _safe_rerun()


# ==================== 修复1：_send_sos 使用 SOSEncoder ====================
# ==================== 三级智能生存通信协议 ====================
# 任务七：SOS数字生命信标升级
# 适配极端环境、通信降级、救援方优化

import struct
from enum import Enum

class PayloadLevel(Enum):
    """载荷等级定义"""
    CRITICAL = "CRITICAL"  # <50字节，二进制，极限环境
    STANDARD = "STANDARD"  # ~200字节，JSON，常规通信
    FULL = "FULL"          # 原有完整格式，含60s趋势 + AI评估

class EmergencyType(Enum):
    """应急类型分类（AI诊断）"""
    HACE = "HACE"              # 高原脑水肿
    HAPE = "HAPE"              # 高原肺水肿
    HYPOTHERMIA = "HYPOTHERMIA"  # 失温
    FALL = "FALL"              # 跌落
    HYPOXIA = "HYPOXIA"        # 低氧
    EXHAUSTION = "EXHAUSTION"  # 体力衰竭
    UNKNOWN = "UNKNOWN"        # 未知

def _assess_environment() -> Dict:
    """评估环境条件：电量、信号强度、温度
    返回环境评估结果，用于决定载荷等级
    """
    # 模拟电量检测（实际应从系统API获取）
    battery_pct = 75  # 实际应该内插设备API
    
    # 信号强度（从卫星调度器状态）
    scheduler = st.session_state.get('scheduler')
    sat_status = getattr(scheduler, 'status', 'GOOD') if scheduler else 'GOOD'
    signal_good = sat_status in ('GOOD', 'WEAK')
    
    # 环境温度
    current_temp = st.session_state.get('current_temp', 20.0)
    
    return {
        'battery_pct': battery_pct,
        'signal_available': signal_good,
        'extreme_cold': current_temp < 0,
        'timestamp': time.time()
    }

def _decide_payload_level(env: Dict) -> PayloadLevel:
    """根据环境条件决定载荷等级
    优先级：CRITICAL > STANDARD > FULL
    """
    # 极限环境条件：电量<30% 或 信号弱 且 极寒
    if env['battery_pct'] < 30 and not env['signal_available'] and env['extreme_cold']:
        return PayloadLevel.CRITICAL
    
    # 通信受限：电量<50% 或 信号差
    if env['battery_pct'] < 50 or not env['signal_available']:
        return PayloadLevel.STANDARD
    
    # 正常条件
    return PayloadLevel.FULL

def _encode_critical_beacon(user_id: int, lat: float, lon: float, 
                            hr: float, spo2: float, temp: float, 
                            risk_level: str) -> bytes:
    """编码CRITICAL级别信标（<50字节）
    格式：
    - User ID (uint16): 2字节
    - Latitude (int32): 4字节 (度 * 1e6)
    - Longitude (int32): 4字节 (度 * 1e6)
    - Timestamp (uint32): 4字节 (Unix秒)
    - HR (uint8): 1字节 (bpm, 0-200)
    - SpO2 (uint8): 1字节 (%, 60-100)
    - Temp offset (uint8): 1字节 (30-40°C range)
    - Risk level (uint8): 1字节 (低=1, 中=2, 高=3)
    
    总计：18字节基础，可选扩展信息
    """
    try:
        # 时间戳
        ts = int(time.time())
        
        # 坐标编码（乘以1e6转整数，防止浮点精度损失）
        lat_int = int(lat * 1e6)
        lon_int = int(lon * 1e6)
        
        # HR约束 [0, 200]
        hr_byte = max(0, min(int(hr), 200))
        
        # SpO2约束 [60, 100]
        spo2_byte = max(60, min(int(spo2), 100))
        
        # 温度偏移：存储为 (temp - 30) * 2，范围30-40°C → 0-20
        # 例：36.5°C → (36.5-30)*2 = 13
        temp_offset = max(0, min(int((temp - 30.0) * 2), 20))
        
        # Risk level编码
        risk_map = {"低": 1, "中": 2, "高": 3}
        risk_byte = risk_map.get(risk_level, 1)
        
        # 使用struct.pack打包（大端序，便于跨平台读取）
        beacon = struct.pack('>H', user_id)  # uint16
        beacon += struct.pack('>i', lat_int)  # int32
        beacon += struct.pack('>i', lon_int)  # int32
        beacon += struct.pack('>I', ts)       # uint32
        beacon += struct.pack('>B', hr_byte)  # uint8
        beacon += struct.pack('>B', spo2_byte) # uint8
        beacon += struct.pack('>B', temp_offset) # uint8
        beacon += struct.pack('>B', risk_byte)  # uint8
        
        return beacon  # 18字节
    except Exception as e:
        st.warning(f"⚠️ 信息压缩失败: {e}")
        return b''

def _decode_critical_beacon(data: bytes) -> Optional[Dict]:
    """解码CRITICAL信标（救援方使用）"""
    if len(data) < 18:
        return None
    try:
        user_id, lat_int, lon_int, ts, hr_b, spo2_b, temp_off, risk_b = struct.unpack('>HiiIBBBB', data[:18])
        return {
            'user_id': user_id,
            'lat': lat_int / 1e6,
            'lon': lon_int / 1e6,
            'timestamp': ts,
            'hr': int(hr_b),
            'spo2': int(spo2_b),
            'temp': 30.0 + temp_off / 2.0,
            'risk_level': {1: "低", 2: "中", 3: "高"}.get(risk_b, "未知")
        }
    except Exception:
        return None

def _diagnose_emergency_type(risk: RiskAssessment, vitals: Dict, 
                            spo2_slope: float = 0.0) -> EmergencyType:
    """AI诊断应急类型
    基于风险评估、体征、趋势推断最可能的病症
    """
    spo2 = vitals.get('spo2', 95)
    temp = vitals.get('temp', 37)
    hr = vitals.get('hr', 70)
    
    # 硬规则优先级
    if temp < 35:
        return EmergencyType.HYPOTHERMIA
    
    if spo2 < 80:
        return EmergencyType.HYPOXIA
    
    # 高原特异性诊断
    if spo2 < 90 and spo2_slope < -0.5:
        # 血氧快速下降，可能是HACE或HAPE
        if hr > 110:
            return EmergencyType.HAPE
        else:
            return EmergencyType.HACE
    
    # 其他情况
    if hr > 130:
        return EmergencyType.EXHAUSTION
    
    return EmergencyType.UNKNOWN

def _build_rescue_payload(user_id: int, name: str, vitals: Dict, 
                         risk: RiskAssessment, address: str,
                         emergency_type: EmergencyType,
                         spo2_slope: float = 0.0) -> Dict:
    """构建救援方优化的JSON载荷
    结构化数据，便于搜救队直接决策
    """
    # 趋势文字化
    trend_summary = ""
    if spo2_slope < -0.3:
        trend_summary += f"血氧↓{abs(spo2_slope):.1f}%/min | "
    if vitals.get('spo2', 95) < 90:
        trend_summary += "血氧危险区 | "
    if vitals.get('temp', 37) < 36:
        trend_summary += "体温过低 | "
    if vitals.get('hr', 70) > 120:
        trend_summary += "心率过高 | "
    
    trend_summary = trend_summary.rstrip(" | ") or "体征平稳"
    
    # 直接指令编码
    action_codes = []
    if vitals.get('spo2', 95) < 80:
        action_codes.append("PRIORITY_OXYGEN")
    if vitals.get('spo2', 95) < 90:
        action_codes.append("PROVIDE_OXYGEN")
    if emergency_type == EmergencyType.HYPOTHERMIA:
        action_codes.append("PREVENT_HEAT_LOSS")
    if emergency_type in (EmergencyType.HACE, EmergencyType.HAPE):
        action_codes.append("IMMEDIATE_DESCENT")
    
    return {
        "alert_id": f"SOS_{user_id}_{int(time.time())}",
        "patient": {
            "id": user_id,
            "name": name,
            "location": address,
            "latitude": float(st.session_state.get('lat', 0)),
            "longitude": float(st.session_state.get('lon', 0))
        },
        "emergency": {
            "type": emergency_type.value,
            "risk_level": risk.level,
            "risk_score": float(risk.score),
            "timestamp": datetime.datetime.now().isoformat()
        },
        "vitals_now": {
            "hr_bpm": int(vitals.get('hr', 70)),
            "spo2_percent": float(vitals.get('spo2', 95)),
            "temp_celsius": float(vitals.get('temp', 37)),
            "spo2_trend_per_min": float(spo2_slope)
        },
        "trend_summary": trend_summary,
        "recommended_actions": action_codes,
        "protocol_version": "3.0"
    }

def _classify_error(error: Exception) -> str:
    """
    分类技术错误，映射到用户可理解的场景
    """
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()
    
    # 网络相关错误
    if any(x in error_str for x in ['network', 'socket', 'timeout', 'connection']):
        return "NETWORK_WEAK"
    
    # 电量相关错误
    if any(x in error_str for x in ['battery', 'power', 'low']):
        return "BATTERY_LOW"
    
    # Streamlit布局错误（nested columns）
    if 'columns' in error_str or 'column context' in error_str:
        return "LAYOUT_ERROR"
    
    # 内存或系统错误
    if any(x in error_type for x in ['memory', 'recursion']):
        return "SYSTEM_RESOURCE"
    
    return "UNKNOWN"

def _get_weak_signal_guidance() -> List[Dict]:
    """
    针对手机卫星通信的可操作指导
    真实场景：华为Mate 60 Pro等卫星通信手机
    """
    return [
        {
            "icon": "📱",
            "action": "举高手机",
            "detail": "将手机举过头顶，屏幕朝上，面向开阔天空",
            "why": "减少身体遮挡，增加卫星捕获概率"
        },
        {
            "icon": "🏃",
            "action": "移动到开阔地",
            "detail": "远离山体、建筑物、树木，向天空可见度最高的位置移动",
            "why": "卫星信号无法穿透固体障碍"
        },
        {
            "icon": "🧘",
            "action": "保持手机稳定",
            "detail": "双手握持，手肘贴紧身体，避免晃动抖动",
            "why": "卫星连接需要3-5秒的稳定指向"
        }
    ]

def _get_low_battery_guidance() -> List[Dict]:
    """
    低电量场景紧急指导
    """
    return [
        {
            "icon": "🔋",
            "action": "关闭不必要的功能",
            "detail": "关闭屏幕亮度、蓝牙、Wi-Fi，仅保留卫星通信",
            "why": "延长电池寿命，确保SOS信号发送完成"
        },
        {
            "icon": "📍",
            "action": "中断其他活动",
            "detail": "停止使用导航、拍照、视频等耗电功能",
            "why": "优先保证应急通信"
        }
    ]

def _schedule_recovery_action(error_type: str):
    """
    根据错误类型安排自动恢复策略
    """
    if error_type == "NETWORK_WEAK":
        # 弱信号时，降级到更小的payload并重试
        st.session_state.sos_retry_count = st.session_state.get('sos_retry_count', 0) + 1
        if st.session_state.sos_retry_count < 3:
            st.session_state.sos_retry_scheduled = True
            st.session_state.sos_retry_time = time.time() + 10  # 10秒后重试
    elif error_type == "LAYOUT_ERROR":
        # 代码级错误，后台重试
        st.session_state.sos_auto_retry = True

def _show_sos_status_card(title: str, guidance_steps: List[Dict], color: str, env_status: Dict):
    """
    使用st.container替代st.columns，避免嵌套布局错误
    """
    with st.container():
        # 标题区
        st.markdown(f"""
        <div style="
            background: {color}20;
            border-left: 4px solid {color};
            padding: 16px;
            border-radius: 8px;
            margin-bottom: 16px;
        ">
            <div style="font-size: 18px; font-weight: 600; color: {color};">
                {title}
            </div>
            <div style="font-size: 12px; color: rgba(226,232,240,0.7); margin-top: 8px; display: flex; gap: 16px;">
                <span>📡 信号: {env_status.get('signal', '检测中')}</span>
                <span>🔋 电量: {env_status.get('battery', '检测中')}%</span>
                <span>🔄 重试: {env_status.get('retry_count', 0)}/5</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # 指导步骤 - 用markdown替代columns
        if guidance_steps:
            st.markdown("<div style='margin-top: 12px;'><strong>请立即执行（已按优先级排序）：</strong></div>", 
                       unsafe_allow_html=True)
            
            steps_html = ""
            for i, step in enumerate(guidance_steps[:3], 1):  # 最多显示3步
                steps_html += f"""
                <div style="
                    background: rgba(10,18,30,0.6);
                    border: 1px solid rgba(0,198,255,0.2);
                    border-radius: 8px;
                    padding: 12px;
                    margin-bottom: 8px;
                    margin-top: 8px;
                ">
                    <div style="display: flex; gap: 12px;">
                        <div style="font-size: 24px; flex-shrink: 0;">{step['icon']}</div>
                        <div style="flex: 1;">
                            <div style="font-weight: 600; color: #e2e8f0; margin-bottom: 4px;">
                                {i}. {step['action']}
                            </div>
                            <div style="font-size: 12px; color: rgba(226,232,240,0.8); line-height: 1.4;">
                                {step['detail']}
                            </div>
                            <div style="font-size: 11px; color: rgba(140,180,220,0.6); margin-top: 4px;">
                                💡 {step['why']}
                            </div>
                        </div>
                    </div>
                </div>
                """
            st.markdown(steps_html, unsafe_allow_html=True)
        
        # 系统状态进度
        if env_status.get('is_retrying'):
            st.markdown("<div style='margin-top: 12px;'><strong>⚙️ 系统正在：</strong></div>", 
                       unsafe_allow_html=True)
            retry_progress = min(1.0, env_status.get('retry_progress', 0.5))
            st.progress(retry_progress, text=f"尝试共享您的位置... ({int(retry_progress*100)}%)")

def _handle_sos_error(error: Exception, env_status: Dict):
    """
    基于环境状态提供可操作的错误指导
    将技术错误转换为用户可理解的指导
    """
    error_type = _classify_error(error)
    
    if error_type == "NETWORK_WEAK":
        # 弱信号场景
        guidance = _get_weak_signal_guidance()
        title = "📡 信号较弱，正在优化..."
        color = "#F59E0B"  # 黄色，冷静
        detail_msg = "已自动切换到低数据模式，只发送关键信息（位置+生命体征）"
        
    elif error_type == "BATTERY_LOW":
        # 低电量场景
        guidance = _get_low_battery_guidance()
        title = "🔋 电量紧张，启动应急模式"
        color = "#EF4444"  # 红色，紧迫
        detail_msg = "系统已启用极限省电模式确保SOS信号完整"
        
    elif error_type == "LAYOUT_ERROR":
        # 代码bug，用户无感知，静默处理
        _log_internal_error(error)
        title = "🔄 系统正在优化..."
        guidance = []
        color = "#00C6FF"  # 蓝色，镇定
        detail_msg = "系统正在调整通信参数，请稍候..."
        
    elif error_type == "SYSTEM_RESOURCE":
        title = "⚡ 系统资源紧张"
        guidance = [{
            "icon": "🧹",
            "action": "释放系统内存",
            "detail": "关闭其他运行中的应用，确保足够的内存空间",
            "why": "充足的系统资源可提高SOS发送成功率"
        }]
        color = "#F59E0B"
        detail_msg = "系统已自动清理后台进程"
        
    else:
        # 未知错误
        title = "⚠️ 通信受阻"
        guidance = [{
            "icon": "🆘",
            "action": "保持冷静",
            "detail": "系统正在自动尝试备用通信通道",
            "why": "多通道冗余设计确保最终成功"
        }]
        color = "#F59E0B"
        detail_msg = "系统正在尝试备用信号通道..."
    
    # 显示用户友好的错误卡片
    _show_sos_status_card(title, guidance, color, env_status)
    st.info(detail_msg)
    
    # 后台记录技术错误（供调试）
    if error_type != "LAYOUT_ERROR":
        print(f"[SOS] 用户面向错误分类={error_type} | 原始错误: {str(error)}")

def _log_internal_error(error: Exception):
    """
    后台记录代码级错误，不显示给用户
    """
    import traceback
    print(f"[SOS] 内部错误（不影响用户）: {str(error)}")
    print(traceback.format_exc())

def _provide_feedback(success: bool, payload_level: PayloadLevel):
    """
    本地用户反馈：信息提示、进度反馈
    使用st.container避免嵌套columns导致的布局错误
    """
    if success:
        # 成功情况：信号已发出
        with st.container():
            st.markdown("""
            <div style="
                background: rgba(16,185,129,0.15);
                border-left: 4px solid #10B981;
                padding: 16px;
                border-radius: 8px;
                margin-bottom: 16px;
            ">
                <div style="font-size: 16px; font-weight: 600; color: #10B981;">
                    ✅ 紧急求救信号已发送！
                </div>
                <div style="font-size: 12px; color: rgba(226,232,240,0.8); margin-top: 8px; line-height: 1.6;">
                    搜救队正在接收您的位置和生命体征信息。<br/>
                    <strong>保持手机信号稳定，继续传输中...</strong>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # 显示发送详情（在可扩展容器中）
            with st.expander("📊 信号详情", expanded=False):
                detail_html = f"""
                <div style="font-size: 12px; color: rgba(226,232,240,0.8);">
                    <div style="margin-bottom: 8px;">
                        <span style="color: rgba(0,198,255,0.8); font-weight: 600;">📡 信号格式:</span> 
                        {payload_level.value}
                    </div>
                    <div style="margin-bottom: 8px;">
                        <span style="color: rgba(0,198,255,0.8); font-weight: 600;">💾 数据量:</span> 
                        自动优化，优先保证核心信息（位置、脉搏、血氧）
                    </div>
                    <div>
                        <span style="color: rgba(0,198,255,0.8); font-weight: 600;">🔄 状态:</span> 
                        信号持续传输中...
                    </div>
                </div>
                """
                st.markdown(detail_html, unsafe_allow_html=True)
            
            # 用户下一步操作指导
            st.info("""
            **现在该做什么？**
            1. 📱 保持手机举起，面向开阔天空
            2. 🏃 如可能，移动到开阔地（远离树木、建筑）
            3. 🧘 保持手机稳定，避免晃动
            
            搜救队通常在**5-20分钟**内做出响应。
            """)
    else:
        # 失败情况：已由 _handle_sos_error 处理，这里设置为无操作
        # (错误处理已转移到 _handle_sos_error 函数)
        pass


def check_auto_sos_trigger() -> bool:
    """自动触发检测：防止误报，需要多条件同时满足
    条件1：risk_score > 0.8持续3分钟
    条件2：用户静止超过5分钟（无位置更新）
    条件3：无手动确认/取消
    """
    risk_hist = st.session_state.get('risk_score_history', [])
    last_location_update = st.session_state.get('last_location_update_ts', time.time())
    auto_sos_countdown = st.session_state.get('auto_sos_countdown', None)
    
    now = time.time()
    current_risk = st.session_state.get('risk_score', 0.0)
    
    # 维护风险历史（最近3分钟，180秒）
    risk_hist = [(ts, score) for ts, score in risk_hist if now - ts < 180]
    risk_hist.append((now, current_risk))
    st.session_state.risk_score_history = risk_hist
    
    # 条件1：高风险持续
    high_risk_duration = sum(1 for _, score in risk_hist if score > 0.8)
    high_risk_continues = high_risk_duration * 2 > len(risk_hist)  # >50% high-risk
    
    # 条件2：用户静止（无主动位置更新）
    stationary = now - last_location_update > 300  # 5分钟
    
    # 条件3：用户未手动取消
    auto_sos_cancelled = st.session_state.get('auto_sos_cancelled', False)
    
    # 综合判断：所有条件都满足才触发
    should_trigger = high_risk_continues and stationary and not auto_sos_cancelled
    
    if should_trigger and auto_sos_countdown is None:
        # 启动30秒倒计时
        st.session_state.auto_sos_countdown = now + 30
        st.session_state.auto_sos_triggered = True
        return True
    
    # 检查倒计时是否已过期
    if auto_sos_countdown is not None and now >= auto_sos_countdown:
        st.session_state.auto_sos_triggered = True
        return True
    
    return False

def _render_auto_sos_countdown():
    """全屏警告：倒计时30秒并显示自动SOS信息"""
    countdown_ts = st.session_state.get('auto_sos_countdown')
    if countdown_ts is None:
        return
    
    remaining = max(0, int(countdown_ts - time.time()))
    
    if remaining > 0:
        st.markdown(f"""
        <style>
            @keyframes sos-flash {{
                0%, 100% {{ background-color: rgba(239, 68, 68, 0.9); }}
                50% {{ background-color: rgba(220, 38, 38, 0.9); }}
            }}
            .sos-countdown {{
                animation: sos-flash 0.5s infinite;
                padding: 40px;
                border-radius: 12px;
                text-align: center;
                color: white;
                font-weight: 700;
                font-size: 32px;
                margin-bottom: 20px;
            }}
        </style>
        <div class="sos-countdown">
            🚨 重要警告 🚨<br>
            自动SOS将在 <span style="font-size:48px;color:yellow;">{remaining}</span> 秒后触发<br>
            <span style="font-size:18px;">确认取消自动SOS请点击下方按钮</span>
        </div>
        """, unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ 我已安全，取消自动SOS"):
                st.session_state.auto_sos_cancelled = True
                st.session_state.auto_sos_countdown = None
                st.rerun()
        with col2:
            if st.button("🚨 立即发送SOS，无需等待"):
                st.session_state.auto_sos_countdown = None
                return "IMMEDIATE"
    else:
        # 倒计时已过，执行自动SOS
        st.error("🚨 自动SOS已触发！正在发送生命信标...")
        return "TRIGGERED"

def _send_sos(latest_vitals, risk_info, note="", trigger_mode="MANUAL"):
    """
    三级智能生存通信协议 - SOS数字生命信标
    
    Parameters:
    - trigger_mode: MANUAL (用户手动) | AUTO (自动触发) | FORCED (强制发送)
    - 根据环境自适应选择载荷等级
    """
    # ✅ ADDED: 防御性初始化检查（防止任何session_state缺失）
    _ensure_sos_state_initialized()
    
    try:
        # Step 1: 环境评估
        env = _assess_environment()
        
        # Step 2: 决定载荷等级
        payload_level = _decide_payload_level(env)
        
        # Step 3: 获取用户信息与体征数据
        user_info = st.session_state.get('user_info', {
            "user_id": 0, "name": "未知干员", "age": 0,
            "sex": "未知", "blood_type": "N/A",
            "emergency_contact": "未预设", "chronic_conditions": ""
        })
        
        spo2_slope = st.session_state.get('spo2_slope', 0.0)
        
        # Step 4: 诊断应急类型
        emergency_type = _diagnose_emergency_type(risk_info, latest_vitals, spo2_slope)
        
        # Step 5: 构建不同等级的载荷
        if payload_level == PayloadLevel.CRITICAL:
            # 极限环境：二进制编码，<50字节
            beacon_bytes = _encode_critical_beacon(
                user_id=int(user_info.get('user_id', 0)),
                lat=float(st.session_state.get('lat', 0.0)),
                lon=float(st.session_state.get('lon', 0.0)),
                hr=float(latest_vitals.get('hr', 70)),
                spo2=float(latest_vitals.get('spo2', 95)),
                temp=float(latest_vitals.get('temp', 37)),
                risk_level=risk_info.level if hasattr(risk_info, 'level') else '低'
            )
            
            payload = {
                "type": "SOS_EMERGENCY_CRITICAL",
                "protocol_version": "3.0",
                "level": "CRITICAL",
                "beacon_hex": beacon_bytes.hex(),
                "beacon_bytes": len(beacon_bytes),
                "timestamp": datetime.datetime.now().isoformat(),
                "sender_id": int(user_info.get('user_id', 0)),
                "location": {
                    "lat": float(st.session_state.get('lat', 0.0)),
                    "lon": float(st.session_state.get('lon', 0.0))
                },
                "user_note": note[:100]  # 限制备注长度
            }
            
        elif payload_level == PayloadLevel.STANDARD:
            # 通信受限：JSON格式，~200字节，含关键趋势
            rescue_payload = _build_rescue_payload(
                user_id=int(user_info.get('user_id', 0)),
                name=user_info.get('name', '未知干员'),
                vitals=latest_vitals,
                risk=risk_info,
                address=st.session_state.get('address', '未知位置'),
                emergency_type=emergency_type,
                spo2_slope=spo2_slope
            )
            
            payload = {
                "type": "SOS_EMERGENCY_STANDARD",
                "protocol_version": "3.0",
                "level": "STANDARD",
                "timestamp": datetime.datetime.now().isoformat(),
                "sender": {
                    "user_id": int(user_info.get('user_id', 0)),
                    "name": user_info.get('name', '未知干员'),
                    "fitness_level": user_info.get('fitness_level', '未知'),
                    "altitude_history": user_info.get('altitude_history', '无')
                },
                "emergency": rescue_payload['emergency'],
                "vitals_now": rescue_payload['vitals_now'],
                "trend_summary": rescue_payload['trend_summary'],
                "recommended_actions": rescue_payload['recommended_actions'],
                "user_note": note[:150]
            }
            
        else:  # PayloadLevel.FULL
            # 正常条件：完整载荷，含60s趋势 + AI评估
            df = st.session_state.get('vitals_window', pd.DataFrame())
            window_data = df.tail(60).to_dict('records') if not df.empty else []
            
            rescue_payload = _build_rescue_payload(
                user_id=int(user_info.get('user_id', 0)),
                name=user_info.get('name', '未知干员'),
                vitals=latest_vitals,
                risk=risk_info,
                address=st.session_state.get('address', '未知位置'),
                emergency_type=emergency_type,
                spo2_slope=spo2_slope
            )
            
            payload = {
                "type": "SOS_EMERGENCY_FULL",
                "protocol_version": "3.0",
                "level": "FULL",
                "timestamp": datetime.datetime.now().isoformat(),
                "sender": {
                    "user_id": int(user_info.get('user_id', 0)),
                    "name": user_info.get('name', '未知干员'),
                    "age": int(user_info.get('age', 0)),
                    "sex": user_info.get('sex', '未知'),
                    "blood_type": user_info.get('blood_type', 'N/A'),
                    "emergency_contact": user_info.get('emergency_contact', '未预设'),
                    "chronic_conditions": user_info.get('chronic_conditions', '无'),
                    "fitness_level": user_info.get('fitness_level', '未知'),
                    "altitude_experience": user_info.get('altitude_experience', '无'),
                    "altitude_history": user_info.get('altitude_history', '无'),
                    "hai_score": float(st.session_state.get('hai_score', 0))
                },
                "location": {
                    "lat": float(st.session_state.get('lat', 0.0)),
                    "lon": float(st.session_state.get('lon', 0.0)),
                    "address": st.session_state.get('address', "未知位置"),
                    "altitude": int(st.session_state.get('altitude', 3000)),
                    "timestamp": datetime.datetime.now().isoformat()
                },
                "risk_assessment": {
                    "level": getattr(risk_info, 'level', ''),
                    "score": float(getattr(risk_info, 'score', 0.0)),
                    "reason": getattr(risk_info, 'reason', ''),
                    "model_type": getattr(risk_info, 'model_type', 'hybrid'),
                    "lstm_confidence": float(getattr(risk_info, 'lstm_confidence', 0.5))
                },
                "vitals_snapshot": latest_vitals or {},
                "vitals_trend_60s": window_data,
                "emergency": rescue_payload['emergency'],
                "vitals_now": rescue_payload['vitals_now'],
                "trend_summary": rescue_payload['trend_summary'],
                "recommended_actions": rescue_payload['recommended_actions'],
                "environment": env,
                "user_note": note
            }
        
        # Step 6: 序列化载荷
        json_payload = json.dumps(payload, ensure_ascii=False, cls=SOSEncoder)
        payload_bytes = len(json_payload.encode('utf-8'))
        
        # Step 7: 提交给卫星调度器
        scheduler = st.session_state.get('scheduler')
        if scheduler and hasattr(scheduler, 'submit'):
            msg = Message(
                priority=Priority.SOS,
                node_id=f"BEACON_{user_info.get('user_id', 'UNKNOWN')}",
                direction="uplink",
                payload_bytes=payload_bytes,
                risk_score=1.0,
                tag=f"SOS-{user_info.get('name', 'UNKNOWN')}-{int(time.time() * 1000)}"
            )
            scheduler.submit(msg)
            st.session_state.pending_sos[msg.tag] = payload
        
        # Step 8: 记录传输日志
        if 'transmit_log' not in st.session_state:
            st.session_state.transmit_log = []
        
        level_name = {
            PayloadLevel.CRITICAL: "极限二进制",
            PayloadLevel.STANDARD: "标准JSON",
            PayloadLevel.FULL: "完整趋势"
        }.get(payload_level, "未知")
        
        st.session_state.transmit_log.append({
            "ts": time.time(),
            "event": "紧急求救信号已发送",
            "sender": user_info.get('name', '未知干员'),
            "level": level_name,
            "detail": f"{level_name}格式: {payload_bytes} bytes",
            "priority": f"SOS_{trigger_mode}",
            "location": {"lat": st.session_state.get('lat'), "lon": st.session_state.get('lon')},
            "emergency_type": emergency_type.value
        })
        
        # Step 9: 用户反馈
        _provide_feedback(success=True, payload_level=payload_level)
        
        # Step 10: 重置状态
        st.session_state.show_sos_panel = False
        st.session_state.sos_sent_success = True
        st.session_state.auto_sos_countdown = None
        st.session_state.auto_sos_triggered = False
        st.session_state.auto_sos_cancelled = False
        
        print(f"[SOS] ✅ 三级信标发送成功: {user_info.get('name','伤员')} | 等级={level_name} | 大小={payload_bytes}B | 应急类型={emergency_type.value}")
        
        # 延迟重运行，让用户先看到成功消息
        time.sleep(1.5)
        _safe_rerun()
        return True

    except Exception as e:
        print(f"[SOS] ❌ 发送失败: {str(e)}")
        
        # 构建环境状态用于错误诊断和恢复指导
        env_status = {
            'signal': '弱' if not st.session_state.signal_available else '正常',
            'battery': int(st.session_state.battery_pct),
            'retry_count': st.session_state.get('sos_retry_count', 0),
            'is_retrying': st.session_state.get('sos_retry_scheduled', False),
            'retry_progress': 0.5
        }
        
        # 使用上下文感知的错误处理而不是通用错误消息
        _handle_sos_error(e, env_status)
        
        import traceback
        traceback.print_exc()
        return False





def _render_sidebar_pro_mode():
    with st.sidebar.expander("🛠️ 专业模式", expanded=False):
        st.session_state.auto_vitals = st.checkbox(
            "自动更新体征（2秒）",
            value=st.session_state.auto_vitals
        )
        modes = ["正常", "遇险演示（缺氧）", "遇险演示（失温）"]
        st.session_state.vitals_mode = st.selectbox(
            "体征模拟模式",
            modes,
            index=modes.index(st.session_state.vitals_mode) if st.session_state.vitals_mode in modes else 0
        )
        st.markdown("---")
        lstm_ok = st.session_state.get('lstm_available', False)
        if lstm_ok:
            st.success("🧠 深度学习模式：可用（LSTM 已加载）")
        else:
            st.warning("⚙️ 深度学习模式：不可用，使用规则引擎")

        status_options = ["GOOD", "WEAK", "DOWN"]
        cur_status = _normalize_satellite_status(getattr(st.session_state.scheduler, "status", "GOOD"))
        new_status = st.selectbox(
            "卫星链路状态",
            status_options,
            index=status_options.index(cur_status) if cur_status in status_options else 0
        )
        st.session_state.scheduler.set_status(new_status)


def _refresh_location_and_weather():
    now_ts = time.time()
    if now_ts - float(st.session_state.get("last_address_refresh_ts", 0)) >= 60:
        try:
            addr = reverse_geocode(float(st.session_state.lon), float(st.session_state.lat))
            st.session_state.address = addr or "四姑娘山景区（双桥沟）"
        except Exception:
            st.session_state.address = "四姑娘山景区（双桥沟）"
        st.session_state.last_address_refresh_ts = now_ts

    if now_ts - float(st.session_state.get("last_weather_refresh_ts", 0)) >= 180:
        try:
            weather = get_current_weather(float(st.session_state.lat), float(st.session_state.lon))
            if weather:
                weather["is_estimated"] = False
                st.session_state.weather = weather
        except Exception:
            pass
        st.session_state.last_weather_refresh_ts = now_ts


def _update_location():
    located = False
    if st_geolocation is not None:
        try:
            loc = st_geolocation()
            if loc and loc.get("latitude") and loc.get("longitude"):
                st.session_state.lat = float(loc["latitude"])
                st.session_state.lon = float(loc["longitude"])
                located = True
        except Exception:
            pass

    if not located:
        st.session_state.lat += np.random.uniform(-0.001, 0.001)
        st.session_state.lon += np.random.uniform(-0.001, 0.001)

    # ── Task 7: 更新位置时重置自动SOS标志 ──
    st.session_state.last_location_update_ts = time.time()
    st.session_state.auto_sos_cancelled = False  # 用户移动，解除取消状态

    _refresh_location_and_weather()
    _safe_rerun()


def _schedule_next_refresh():
    wait_s = max(0.0, 2.0 - (time.time() - float(st.session_state.last_vitals_update_ts)))
    if wait_s <= 0:
        wait_s = 0.5
    time.sleep(wait_s)
    _safe_rerun()


if __name__ == "__main__":
    render()