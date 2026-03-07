import json
import re
import os
import sys
import time
import logging
from openai import OpenAI

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import AI_STUDIO_TOKEN

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self):
        self.client = None
        self.lstm_available = False
        self._lstm_predictor = None
        if AI_STUDIO_TOKEN:
            try:
                self.client = OpenAI(
                    api_key=AI_STUDIO_TOKEN,
                    base_url="https://aistudio.baidu.com/llm/lmapi/v3",
                )
            except Exception:
                self.client = None

        # Preload LSTM predictor once. If unavailable, fall back to rule-based risk.
        try:
            from services.lstm_risk import get_predictor

            self._lstm_predictor = get_predictor()
            self.lstm_available = self._lstm_predictor is not None
        except Exception:
            self._lstm_predictor = None
            self.lstm_available = False

    @staticmethod
    def _set_risk_model_state(model_name):
        try:
            import streamlit as st

            st.session_state.risk_model = model_name
        except Exception:
            pass

    def predict_risk(self, vitals_window, use_lstm=False):
        # use_lstm=True means force model inference for this call.
        force_lstm = bool(use_lstm)

        if force_lstm and (not self.lstm_available or self._lstm_predictor is None):
            self._set_risk_model_state("rule")
            raise RuntimeError("LSTM predictor unavailable")

        # Default behavior: prefer trend model, auto fallback to rules when unavailable.
        if self.lstm_available and self._lstm_predictor is not None:
            try:
                risk_score, risk_level, reason, _probs = self._lstm_predictor.predict(vitals_window)
                self._set_risk_model_state("lstm")
                return float(risk_score), str(risk_level), str(reason)
            except Exception as e:
                # Runtime failure: mark unavailable for subsequent calls.
                self.lstm_available = False
                self._lstm_predictor = None
                if force_lstm:
                    self._set_risk_model_state("rule")
                    raise RuntimeError(f"LSTM inference failed: {e}") from e

        if force_lstm:
            self._set_risk_model_state("rule")
            raise RuntimeError("LSTM predictor unavailable after runtime failure")

        if not vitals_window:
            self._set_risk_model_state("rule")
            return 0.0, "低", "无数据"

        latest = vitals_window[-1]
        hr, spo2, temp = latest["hr"], latest["spo2"], latest["temp"]

        if hr > 120 and spo2 < 90:
            self._set_risk_model_state("rule")
            return 0.9, "高", "心率过高且血氧严重偏低"
        if hr > 100 or spo2 < 94:
            self._set_risk_model_state("rule")
            return 0.6, "中", "心率偏高或血氧偏低"
        if temp < 35.0:
            self._set_risk_model_state("rule")
            return 0.8, "高", "体温过低，有失温风险"
        self._set_risk_model_state("rule")
        return 0.1, "低", "体征平稳"

    def _call_llm(self, system_prompt, user_prompt, temperature=0.3):
        if not self.client:
            return "AI服务暂不可用，请参考标准户外手册。"

        last_err = None
        for attempt in range(1, 4):
            try:
                response = self.client.chat.completions.create(
                    model="ernie-5.0",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                )
                return response.choices[0].message.content
            except Exception as e:
                last_err = e
                logger.warning("LLM call failed at attempt %s/3: %s", attempt, str(e))
                if attempt < 3:
                    time.sleep(1)
        return f"AI调用失败: {str(last_err)}"

    def call_wenxin(self, system_prompt, user_prompt, temperature=0.3):
        return self._call_llm(system_prompt, user_prompt, temperature=temperature)

    def _extract_json(self, text):
        if not text:
            return None

        stripped = str(text).strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except Exception:
                pass

        try:
            return json.loads(text)
        except Exception:
            pass

        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fenced:
            try:
                return json.loads(fenced.group(1).strip())
            except Exception:
                pass

        candidate = re.search(r"\{[\s\S]*\}", text)
        if candidate:
            try:
                return json.loads(candidate.group(0))
            except Exception:
                pass

        list_candidate = re.search(r"\[[\s\S]*\]", text)
        if list_candidate:
            try:
                return json.loads(list_candidate.group(0))
            except Exception:
                return None
        return None

    def generate_trip_advice(self, destination, start_date, end_date, forecasts):
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

        def _wind_score(wc):
            # wc_day examples: "3级" / "4-5级" / "微风"
            if not wc:
                return 0.0
            s = str(wc)
            nums = re.findall(r"\d+(?:\.\d+)?", s)
            if not nums:
                return 0.5 if ("大" in s or "强" in s) else 0.0
            try:
                return max(float(n) for n in nums)
            except Exception:
                return 0.0

        def _day_risk_score(day):
            text = str(day.get("text_day") or "")
            precip = _to_float(day.get("precip"))
            humidity = _to_int(day.get("humidity"))
            wind = _wind_score(day.get("wc_day"))

            score = 0.0
            if "雪" in text:
                score += 3.0
            if "雨" in text:
                score += 2.0
            if "雷" in text:
                score += 2.5
            if "雾" in text:
                score += 1.2
            if precip is not None:
                score += min(3.0, max(0.0, precip) / 3.0)
            if humidity is not None and humidity >= 80:
                score += 0.8
            score += max(0.0, wind - 3.0) * 0.6
            return score

        def _pick_best_date(days, fallback_date):
            if not days:
                return fallback_date
            best = min(days, key=_day_risk_score)
            return str(best.get("date") or fallback_date)[:10]

        forecasts = forecasts if isinstance(forecasts, list) else []
        best_date_fallback = _pick_best_date(forecasts, start_date)
        default_report = {
            "best_date": best_date_fallback,
            "risk_dates": "",
            "risk_level": "中",
            "risk_factors": ["高原反应", "天气变化", "地形复杂"],
            "equipment_tips": ["冲锋衣", "保暖层", "登山鞋", "头灯", "急救包", "备用电源"],
            "general_advice": "避免在强风/降水日进入高海拔暴露地形；行前查封闭与路况；结伴、留撤退余量。",
            "is_mock": True,
        }

        system_prompt = (
            "你是一名持有WMAI（国际野外医学协会）认证的户外探险规划与风险控制专家，"
            "拥有15年高原救援与户外风险评估经验。"
            "你的分析必须基于气象数据的客观事实，不得编造数据或做无根据推测。"
            "你只输出合法 JSON，禁止输出任何说明文字、Markdown格式或代码块标记。"
        )
        user_prompt = (
            f"目的地：{destination}\n"
            f"用户空闲窗口：{start_date} 至 {end_date}\n"
            f"逐日天气（仅窗口期，字段包含 date/text_day/low/high/wd_day/wc_day/precip/humidity）："
            f"{json.dumps(forecasts, ensure_ascii=False)}\n\n"
            "字段说明：precip=降水量(mm)，humidity=湿度(%)，wc_day=风力等级。\n\n"
            "【风险评估框架】请逐日评估以下维度（不要编造不存在的数值）：\n"
            "1. 降水风险：降水>5mm为高风险，应避免高暴露路线\n"
            "2. 温度风险：最低温<0°C需防失温，>35°C需防中暑\n"
            "3. 风力风险：>=5级为高风险因子，影响行进安全\n"
            "4. 湿度风险：湿度>80%叠加低温时失温风险倍增\n"
            "5. 综合天气：雷暴/暴雨/大雾为高危天气\n\n"
            "【任务】基于完整气象数据，先逐日分析风险，再综合输出。\n\n"
            "【输出要求】\n"
            "1) 仅输出一段合法JSON，不要Markdown、不要代码块、不要额外文字\n"
            "2) 字段必须齐全；基于实际天气数据分析，不确定用保守默认\n"
            "3) best_date 必须落在用户窗口期内（YYYY-MM-DD格式）\n"
            "4) risk_level 只能是：低/中/高\n"
            "5) equipment_tips 每项格式为'物品名称（使用场景与理由）'\n\n"
            "JSON字段：\n"
            "{\"best_date\":\"YYYY-MM-DD\",\"risk_dates\":\"具体日期和原因\",\"risk_level\":\"低|中|高\","
            "\"risk_factors\":[\"因素1（数据支撑）\"],\"equipment_tips\":[\"物品（场景）\"],\"general_advice\":\"<=100字综合建议\"}\n"
            "示例：\n"
            "{\"best_date\":\"2026-05-03\",\"risk_dates\":\"2026-05-04 午后降水8mm+5级风\",\"risk_level\":\"中\","
            "\"risk_factors\":[\"5月4日降水量8mm超安全阈值\",\"窗口期内风力波动3-5级\"],"
            "\"equipment_tips\":[\"冲锋衣（应对午后阵雨）\",\"保暖中层（夜间温差10°C+）\"],"
            "\"general_advice\":\"上午尽早出发，避开午后降水高峰，通过暴露路段前确认风力。\"}"
        )

        try:
            raw = self.call_wenxin(system_prompt, user_prompt, temperature=0.1)
            parsed = self._extract_json(raw)
            if not isinstance(parsed, dict):
                logger.warning("generate_trip_advice parse failed, fallback to mock.")
                return default_report

            report = {
                "best_date": str(parsed.get("best_date") or default_report["best_date"])[:10],
                "risk_dates": str(parsed.get("risk_dates") or ""),
                "risk_level": str(parsed.get("risk_level") or default_report["risk_level"]),
                "risk_factors": parsed.get("risk_factors") if isinstance(parsed.get("risk_factors"), list) else default_report["risk_factors"],
                "equipment_tips": parsed.get("equipment_tips") if isinstance(parsed.get("equipment_tips"), list) else default_report["equipment_tips"],
                "general_advice": str(parsed.get("general_advice") or default_report["general_advice"]),
                "is_mock": False,
            }

            if report["risk_level"] not in ("低", "中", "高"):
                report["risk_level"] = "中"

            # Guard: best_date must be inside the provided window (inclusive).
            if not (str(start_date) <= report["best_date"] <= str(end_date)):
                report["best_date"] = default_report["best_date"]

            # Backward compatibility for older UI keys (if any other views still read them).
            report["gear_list"] = report.get("equipment_tips", [])
            return report
        except Exception:
            logger.exception("generate_trip_advice failed, fallback to mock.")
            return default_report

    def recommend_trip_window(self, destination, start_date, end_date, forecasts):
        """
        Phase 1: Recommend best trip date within [start_date, end_date] based on weather window.

        Returns:
            {
              "best_date": "YYYY-MM-DD",
              "reason": "...",
              "alt_dates": ["YYYY-MM-DD", ...],
              "high_risk_dates": "... or ''",
              "is_mock": bool
            }
        """
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

        def _wind_score(wc):
            if not wc:
                return 0.0
            s = str(wc)
            nums = re.findall(r"\d+(?:\.\d+)?", s)
            if not nums:
                return 0.5 if ("大" in s or "强" in s) else 0.0
            try:
                return max(float(n) for n in nums)
            except Exception:
                return 0.0

        def _day_score(day):
            # Lower is better.
            text = str(day.get("text_day") or "")
            precip = _to_float(day.get("precip"))
            humidity = _to_int(day.get("humidity"))
            wind = _wind_score(day.get("wc_day"))

            score = 0.0
            if "雪" in text:
                score += 3.0
            if "雨" in text:
                score += 2.0
            if "雷" in text:
                score += 2.5
            if "雾" in text:
                score += 1.2
            if precip is not None:
                score += min(3.0, max(0.0, precip) / 3.0)
            if humidity is not None and humidity >= 80:
                score += 0.8
            score += max(0.0, wind - 3.0) * 0.6
            return score

        forecasts = forecasts if isinstance(forecasts, list) else []
        scored = []
        for d in forecasts:
            date_str = str(d.get("date") or "")[:10]
            if not date_str:
                continue
            scored.append((date_str, _day_score(d), d))
        scored.sort(key=lambda x: x[1])

        best_date_fallback = scored[0][0] if scored else str(start_date)[:10]
        alt_dates_fallback = [x[0] for x in scored[1:3]] if len(scored) > 1 else []

        default_rec = {
            "best_date": best_date_fallback,
            "reason": "基于窗口期天气，优先选择降水更少、风力更小、体感更稳定的日期以降低风险并提升体验。",
            "alt_dates": alt_dates_fallback,
            "high_risk_dates": "",
            "is_mock": True,
        }

        system_prompt = (
            "你是一名持有WMAI认证的户外出行风险控制规划专家，擅长气象分析与日期优选。"
            "你的推荐必须基于逐日天气数据对比分析，不可凭空推测。"
            "你只输出合法 JSON，不含任何额外文字。"
        )
        user_prompt = (
            f"目的地：{destination}\n"
            f"空闲窗口：{start_date} 至 {end_date}\n"
            f"逐日天气（仅窗口期）："
            f"{json.dumps(forecasts, ensure_ascii=False)}\n\n"
            "字段说明：precip=降水量(mm)，humidity=湿度(%)，wc_day=风力等级。\n\n"
            "【分析步骤】\n"
            "1. 逐日计算综合风险分（考虑降水、风力、温差、湿度、天气类型）\n"
            "2. 选出风险最低的日期作为 best_date\n"
            "3. 选出1-2个次优日期作为 alt_dates\n"
            "4. 标注明显高风险的日期段\n\n"
            "风险判定标准：降水>5mm=高；湿度>80%且低温=失温风险；风力>=5级=高。\n\n"
            "【输出要求】\n"
            "1) 仅输出合法JSON，不要Markdown、不要代码块\n"
            "2) reason 字段需引用实际天气数据作为依据，<=120字\n"
            "3) best_date 和 alt_dates 必须在窗口期内（YYYY-MM-DD）\n\n"
            "JSON字段：\n"
            "{\"best_date\":\"YYYY-MM-DD\",\"reason\":\"基于数据的推荐理由\",\"alt_dates\":[\"YYYY-MM-DD\"],\"high_risk_dates\":\"具体日期+原因\"}\n"
            "示例：\n"
            "{\"best_date\":\"2026-05-03\",\"reason\":\"当日降水仅1mm且风力2级，体感温度适宜（8~16C），综合风险最低。\",\"alt_dates\":[\"2026-05-02\"],\"high_risk_dates\":\"2026-05-04 午后降水8mm+5级阵风\"}"
        )

        try:
            raw = self.call_wenxin(system_prompt, user_prompt, temperature=0.1)
            parsed = self._extract_json(raw)
            if not isinstance(parsed, dict):
                logger.warning("recommend_trip_window parse failed, fallback to mock.")
                return default_rec

            best_date = str(parsed.get("best_date") or default_rec["best_date"])[:10]
            alt_dates = parsed.get("alt_dates")
            if not isinstance(alt_dates, list):
                alt_dates = default_rec["alt_dates"]
            alt_dates = [str(x)[:10] for x in alt_dates if str(x)[:10]]

            rec = {
                "best_date": best_date,
                "reason": str(parsed.get("reason") or default_rec["reason"]),
                "alt_dates": alt_dates[:2],
                "high_risk_dates": str(parsed.get("high_risk_dates") or ""),
                "is_mock": False,
            }

            if not (str(start_date) <= rec["best_date"] <= str(end_date)):
                rec["best_date"] = default_rec["best_date"]
            rec["alt_dates"] = [d for d in rec["alt_dates"] if str(start_date) <= d <= str(end_date)]
            return rec
        except Exception:
            logger.exception("recommend_trip_window failed, fallback to mock.")
            return default_rec

    def generate_trip_detail(
        self,
        destination,
        trip_date,
        day_forecast,
        window_forecasts=None,
        elevation=None,
        activity_type=None,
        season=None,
    ):
        """
        Phase 2: Generate a detailed report for a chosen trip date.

        Returns:
            {
              "risk_level": "低|中|高",
              "risk_factors": ["..."],
              "equipment_tips": ["..."],
              "general_advice": "...",
              "is_mock": bool
            }
        """
        window_forecasts = window_forecasts if isinstance(window_forecasts, list) else []

        def _to_float(v):
            if v is None or v == "":
                return None
            try:
                return float(v)
            except Exception:
                return None

        def _safe_int(v):
            if v is None or v == "":
                return None
            try:
                return int(float(v))
            except Exception:
                return None

        def _season_from_date(d):
            try:
                m = int(str(d)[5:7])
            except Exception:
                return "未知季节"
            if m in (12, 1, 2):
                return "冬季"
            if m in (3, 4, 5):
                return "春季"
            if m in (6, 7, 8):
                return "夏季"
            return "秋季"

        def _fallback_equipment(day, elevation_m, act_type):
            low = _to_float(day.get("low"))
            precip = _to_float(day.get("precip"))
            humidity = _safe_int(day.get("humidity"))
            tips = [
                "高频口哨（紧急求救，远距离可识别）",
                "急救毯（突发失温时快速保温）",
                "头灯（含备用电池，确保夜间可视）",
                "备用电源（保障通信与定位设备续航）",
                "防水火柴（潮湿环境下点火应急）",
                "排汗内层（速干材质，减少湿冷）",
                "抓绒/轻羽中层（保暖层，应对温差）",
                "防风防水外层（冲锋衣裤，阻挡风雨）",
                "高帮防水登山鞋（保护踝关节并提升抓地）",
                "羊毛袜（保暖排汗，降低磨脚风险）",
            ]
            if elevation_m is not None and elevation_m > 3000:
                tips.extend(
                    [
                        "血氧仪（高海拔实时监测血氧）",
                        "便携氧气（高反时应急缓解）",
                        "高原应急药品（如抗高反药，按医嘱使用）",
                    ]
                )
            if low is not None and low < 0:
                tips.extend(
                    [
                        "羽绒服（低温环境核心保暖）",
                        "保暖手套（防冻伤，保持手部灵活）",
                    ]
                )
            if precip is not None and precip > 5:
                tips.extend(
                    [
                        "背包防水罩（保护保暖层与电子设备）",
                        "防水手套/防水外套（长时间降水下保持体温）",
                    ]
                )
            if humidity is not None and humidity > 80:
                tips.append("快干备用内层（高湿环境下快速更换）")
            if str(act_type or "") == "多日露营":
                tips.extend(
                    [
                        "睡袋（按预期最低温标定）",
                        "防潮垫（隔绝地面传导失温）",
                        "帐篷（抗风等级满足山地环境）",
                        "炊具与燃料（热食热水补给）",
                    ]
                )
            if str(act_type or "") == "技术攀登":
                tips.extend(
                    [
                        "攀登头盔（落石与碰撞防护）",
                        "安全带（技术路段保护核心）",
                        "主锁与下降器（保护站连接与下降）",
                    ]
                )
            # Deduplicate while keeping order.
            seen = set()
            out = []
            for item in tips:
                if item not in seen:
                    seen.add(item)
                    out.append(item)
            return out

        elevation_m = _to_float(elevation)
        if elevation_m is not None:
            elevation_m = int(elevation_m)
        resolved_activity = str(activity_type or "单日徒步")
        resolved_season = str(season or _season_from_date(trip_date))

        low = _to_float((day_forecast or {}).get("low"))
        precip = _to_float((day_forecast or {}).get("precip"))
        wind_text = str((day_forecast or {}).get("wc_day") or "")
        humidity = _safe_int((day_forecast or {}).get("humidity"))

        risk_factors = ["地形复杂", "天气变化"]
        if elevation_m is not None and elevation_m > 3000:
            risk_factors.append("高原反应")
        if precip is not None and precip > 5:
            risk_factors.append("强降水")
        if humidity is not None and humidity > 80:
            risk_factors.append("高湿失温")
        if "5" in wind_text or "6" in wind_text or "7" in wind_text:
            risk_factors.append("大风路段")
        if low is not None and low < 0:
            risk_factors.append("低温失温")

        default_detail = {
            "risk_level": "中" if len(risk_factors) <= 3 else "高",
            "risk_factors": risk_factors,
            "equipment_tips": _fallback_equipment(day_forecast or {}, elevation_m, resolved_activity),
            "general_advice": "遵循保守出发与分层穿着原则，午后天气转差前通过暴露路段；高海拔持续监测血氧并保留撤退窗口。",
            "is_mock": True,
        }

        system_prompt = (
            "你是一名拥有国际野外医学协会（WMAI）认证的户外探险顾问，精通装备选择与风险管理。"
            "你的装备建议必须基于目的地特征、天气、海拔和活动类型，按照户外装备层次结构（应急生存、服装系统、足部保护、过夜系统、特殊装备）给出，并符合国家标准《家用防灾应急包》（GB/T 36750-2025）。"
            "输出仅限 JSON，不含任何解释。"
        )
        user_prompt = (
            f"目的地：{destination}\n"
            f"出行日期：{trip_date}\n"
            f"海拔范围：{(str(elevation_m) + '米') if elevation_m is not None else '未知（若无精确数据请根据目的地名称推测）'}\n"
            f"单人任务模式：{resolved_activity}（规范化类别：单日徒步/多日露营/技术攀登）\n"
            f"当日天气：{json.dumps(day_forecast or {}, ensure_ascii=False)}\n"
            f"窗口期参考天气（可用于对比）：{json.dumps(window_forecasts, ensure_ascii=False)}\n\n"
            f"季节：{resolved_season}（根据日期判断）\n\n"
            "请生成一份符合户外专业标准的装备清单，按以下分类输出（每类用数组表示）：\n"
            "1. 应急生存装备（必须包含：高频口哨、急救毯、头灯、备用电源、防水火柴）\n"
            "2. 服装系统（遵循三层穿衣法：排汗内层、保暖中层、防风防水外层；根据温度和降水推荐具体材质）\n"
            "3. 足部装备（高帮防水登山鞋、羊毛袜、雪套/冰爪，依地形判断）\n"
            "4. 过夜系统（若为多日活动：睡袋、防潮垫、帐篷、炊具）\n"
            "5. 特殊装备（根据风险判断：海拔>3000m需血氧仪、氧气；雪地需墨镜、防晒；技术路线需绳索、安全带等）\n\n"
            "风险原则：\n"
            "- 海拔>3000米：必须包含血氧仪与高原应急物品\n"
            "- 温度<0°C：必须包含羽绒服、保暖手套\n"
            "- 降水量>5mm：必须包含防水冲锋衣、背包防水罩\n"
            "- 多日活动：必须包含过夜系统全套\n"
            "- 攀登技术路线：需包含头盔、安全带、主锁、下降器\n\n"
            "任务：为该日期生成详细风险分析与装备建议，输出整体风险等级、关键风险因素、装备建议、综合安全提示。\n"
            "输出要求：\n"
            "1) 仅输出合法JSON（可json.loads解析），不要Markdown、不要代码块、不要额外文字。\n"
            "2) risk_level 只能是：低/中/高。\n"
            "3) risk_factors 与 equipment_tips 必须是字符串列表。\n\n"
            "JSON字段：\n"
            "{\"risk_level\":\"低|中|高\",\"risk_factors\":[\"...\"],\"equipment_tips\":[\"...\"],\"general_advice\":\"<=120字\"}\n"
            "示例：\n"
            "{\"risk_level\":\"中\",\"risk_factors\":[\"高原反应\",\"午后阵雨\"],\"equipment_tips\":[\"高频口哨（紧急求救用）\",\"急救毯（突发失温时包裹身体）\",\"头灯（带备用电池）\",\"排汗羊毛内衣（保持干燥）\",\"抓绒衣（保暖层）\",\"冲锋衣（防风防雨）\",\"高帮防水登山鞋（保护脚踝）\",\"羊毛袜（保暖排汗）\",\"便携氧气瓶（高海拔预防）\",\"血氧仪（监测身体状况）\"],\"general_advice\":\"上午尽早出发，避开午后雷雨。注意保暖，严防失温。\"}"
        )

        try:
            raw = self.call_wenxin(system_prompt, user_prompt, temperature=0.1)
            parsed = self._extract_json(raw)
            if not isinstance(parsed, dict):
                logger.warning("generate_trip_detail parse failed, fallback to mock.")
                return default_detail

            detail = {
                "risk_level": str(parsed.get("risk_level") or default_detail["risk_level"]),
                "risk_factors": parsed.get("risk_factors") if isinstance(parsed.get("risk_factors"), list) else default_detail["risk_factors"],
                "equipment_tips": parsed.get("equipment_tips") if isinstance(parsed.get("equipment_tips"), list) else default_detail["equipment_tips"],
                "general_advice": str(parsed.get("general_advice") or default_detail["general_advice"]),
                "is_mock": False,
            }
            if detail["risk_level"] not in ("低", "中", "高"):
                detail["risk_level"] = "中"
            return detail
        except Exception:
            logger.exception("generate_trip_detail failed, fallback to mock.")
            return default_detail

    def generate_pre_trip_report(self, destination, weather):
        system_prompt = (
            "你是一名持有WMAI认证的户外探险顾问，负责生成结构化行前评估报告。"
            "基于目的地特征和天气数据给出专业但简洁的评估，每条建议必须可操作。"
        )
        user_prompt = (
            f"目的地：{destination}。天气预报：{weather}。\n"
            "请生成包含：1. 风险等级评估（低/中/高，附理由）"
            "2. 关键风险提示（最多3条，每条含量化标准）"
            "3. 核心装备建议（最多5件，每件附使用场景）。"
            "输出简洁，总计不超过200字。"
        )
        return self._call_llm(system_prompt, user_prompt)

    def generate_mid_trip_action(self, vitals, risk_level, weather):
        system_prompt = (
            "你是雪山搜救队守望官，持有WMAI高级急救证，"
            "必须基于最新生理数据和环境情报输出1-3条精准可执行的自救指令。"
            "每条指令必须包含具体量化阈值（如时间、距离、数值）。"
        )
        user_prompt = (
            f"探险者当前状态：心率{vitals['hr']}bpm，血氧{vitals['spo2']}%，体温{vitals['temp']}C。\n"
            f"风险等级：{risk_level}。天气：{weather}。\n"
            "请给出1-3条简短的强制自救指令（格式：动作+量化标准+紧急备案），"
            "总计不超过150字。"
        )
        return self._call_llm(system_prompt, user_prompt)

    def generate_post_trip_report(self, adventure_id, vitals_summary):
        system_prompt = (
            "你是一名户外探险数据分析师，负责基于体征数据生成结构化行后复盘。"
            "重点分析：体征异常时段、风险事件、恢复表现。"
            "以数据为依据，不要编造不存在的数据。简洁输出，每段2-4行。"
        )
        user_prompt = (
            f"请根据以下探险数据生成行后总结报告：{vitals_summary}。\n"
            "必须包含且仅包含：\n"
            "1. 行程概况（时长、关键节点）\n"
            "2. 体征波动分析（异常时段+原因推测）\n"
            "3. 改进建议（基于数据给出具体可执行建议）"
        )
        return self._call_llm(system_prompt, user_prompt)

    def generate_actions(self, vitals_dict, risk_level, address, weather_dict):
        default_actions = [
            {
                "title": "补水与能量",
                "detail": "保持水分，每30分钟饮水200ml并补充少量能量。",
                "fallback": "若无法进食，至少每15分钟小口补水一次。",
            },
            {
                "title": "保暖检查",
                "detail": "注意保暖，检查衣物和袜子是否潮湿，及时更换内层。",
                "fallback": "风大时优先进入背风处再进行衣物整理。",
            },
            {
                "title": "监测血氧",
                "detail": "留意血氧变化，若持续下降或头晕加重，立即休息。",
                "fallback": "连续两次低于阈值时停止上升并准备撤离。",
            },
        ]

        system_prompt = (
            "你是一名持有WMAI认证的野外急救与户外风险控制专家。"
            "基于实时体征、风险等级和环境数据，输出精准可执行的自救行动。"
            "每条行动必须包含量化执行标准（时间/距离/数值阈值）。"
            "输出仅限合法JSON数组，不含任何额外文字。"
        )
        user_prompt = (
            "输入信息：\n"
            f"- 体征：心率{vitals_dict.get('hr')} bpm，血氧{vitals_dict.get('spo2')}%，体温{vitals_dict.get('temp')}C\n"
            f"- 风险等级：{risk_level}\n"
            f"- 位置：{address}\n"
            f"- 天气：{json.dumps(weather_dict, ensure_ascii=False)}\n\n"
            "【分级处置原则】\n"
            "- 高风险：必须包含'停止前进/就地保温/准备求援'之一，优先级=1\n"
            "- 中风险：暂停活动，执行保守干预，密切监测关键指标\n"
            "- 低风险：常规预防，维持观察频率\n\n"
            "任务：给出 2-3 条具体自救/避险行动，按优先级排列。\n"
            "输出要求：仅输出合法JSON数组，不要Markdown、不要代码块。\n"
            "每条格式：{\"title\":\"<=12字动词开头\",\"detail\":\"<=45字含量化阈值\",\"fallback\":\"<=35字备用方案\"}。\n"
        )

        try:
            raw = self.call_wenxin(system_prompt, user_prompt, temperature=0.2)
            parsed = self._extract_json(raw)
            if isinstance(parsed, list):
                cleaned = []
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    cleaned.append(
                        {
                            "title": str(item.get("title") or "行动建议"),
                            "detail": str(item.get("detail") or ""),
                            "fallback": str(item.get("fallback") or ""),
                        }
                    )
                if cleaned:
                    return cleaned[:3]
            return default_actions
        except Exception:
            return default_actions

    def generate_post_report(self, summary):
        default_report = (
            "## 行程概览\n"
            "AI生成失败，使用模板。已完成一次探险行程沉淀。\n\n"
            "## 健康分析\n"
            "体征总体可控，建议持续关注心率与血氧波动。\n\n"
            "## 风险复盘\n"
            "重点风险来自天气变化与地形复杂性，建议后续增加分段补给计划。\n\n"
            "## 保险理赔建议\n"
            "本次记录可作为意外险辅助凭证，请保留轨迹与体征日志。"
        )

        system_prompt = (
            "你是一名探险数据分析师，持有WMAI认证。"
            "基于行程数据生成结构化、克制、可读的复盘报告。"
            "分析必须以数据为依据：标注异常值、分析趋势，不可编造数据。"
            "输出必须为中文Markdown格式。"
        )
        user_prompt = (
            "根据以下行程数据生成总结报告（Markdown格式，中文）：\n"
            f"{json.dumps(summary, ensure_ascii=False)}\n\n"
            "硬性要求：\n"
            "- 必须包含且仅包含以下四个二级标题：\n"
            "## 行程概览\n"
            "## 健康分析\n"
            "## 风险复盘\n"
            "## 保险理赔建议\n\n"
            "分析框架：\n"
            "- 健康分析：标注体征异常时段（心率>120/血氧<93/体温<35.5），分析原因和恢复趋势\n"
            "- 风险复盘：总结风险触发事件、应对是否得当、改进建议\n"
            "- 保险理赔建议：建议保留哪些数据作为凭证\n\n"
            "以数据为依据，不编造海拔/里程等。每段2-4行。"
        )
        try:
            result = self.call_wenxin(system_prompt, user_prompt, temperature=0.2)
            if not isinstance(result, str):
                return default_report
            text = result.strip()
            if "## 行程概览" not in text:
                return default_report
            return text
        except Exception:
            return default_report
