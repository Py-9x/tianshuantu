import os
import sys
import pickle
import logging
import warnings

# 压制 sklearn 的 feature name 警告（scaler 用 DataFrame 训练但预测时传 numpy array）
warnings.filterwarnings("ignore", message="X does not have valid feature names")

logger = logging.getLogger(__name__)

# 延迟导入 paddle，避免未安装时直接崩溃
# 注意：不在顶部调用 enable_static()，防止与 Streamlit 热重载冲突
_paddle_available = False
try:
    import paddle as _paddle_module
    _paddle_available = True
except ImportError:
    _paddle_module = None
    logger.warning("PaddlePaddle 未安装，LSTM 模型将不可用")
except Exception as e:
    _paddle_module = None
    logger.warning("PaddlePaddle 导入失败: %s", e)


class VitalsLSTMPredictor:
    """
    Paddle static inference wrapper for vitals LSTM model.
    Falls back by raising exceptions; callers should catch and degrade gracefully.
    """

    def __init__(self, model_dir):
        self.model_dir = model_dir
        self._loaded = False
        self._paddle = None
        self._executor = None
        self._infer_program = None
        self._feed_names = None
        self._fetch_targets = None
        self._scaler = None
        self._label_encoder = None

    def _load_pickle(self, filename):
        path = os.path.join(self.model_dir, filename)
        # These artifacts are commonly dumped by joblib. Prefer joblib loader
        # and fall back to pickle for backward compatibility.
        try:
            import joblib

            return joblib.load(path)
        except Exception:
            with open(path, "rb") as f:
                return pickle.load(f)

    def _ensure_loaded(self):
        if self._loaded:
            return

        if not _paddle_available or _paddle_module is None:
            raise RuntimeError("PaddlePaddle 未安装或导入失败，无法加载 LSTM 模型")

        paddle = _paddle_module
        self._paddle = paddle

        # Paddle 2.x defaults to dynamic mode; static inference APIs require static mode.
        # 只在需要时切换一次，避免 Streamlit 热重载时反复切换
        try:
            if hasattr(paddle, "in_dynamic_mode") and paddle.in_dynamic_mode():
                paddle.enable_static()
                logger.info("已切换到 PaddlePaddle 静态图模式")
        except Exception as e:
            logger.warning("切换静态图模式失败（可能已在静态模式）: %s", e)

        # 查找模型文件 - 修正文件后缀名查找逻辑
        model_json = os.path.join(self.model_dir, "vitals_lstm_model_static.json")
        params_file = os.path.join(self.model_dir, "vitals_lstm_model_static.pdiparams")
        if not os.path.exists(model_json):
            model_json = os.path.join(self.model_dir, "vitals_lstm_model.json")
        if not os.path.exists(params_file):
            params_file = os.path.join(self.model_dir, "vitals_lstm_model.pdiparams")

        logger.info("LSTM 模型文件: json=%s (存在=%s), params=%s (存在=%s)",
                     model_json, os.path.exists(model_json),
                     params_file, os.path.exists(params_file))

        if not os.path.exists(model_json) or not os.path.exists(params_file):
            raise FileNotFoundError(
                f"LSTM model not found: json={os.path.exists(model_json)}, "
                f"params={os.path.exists(params_file)}, dir={self.model_dir}"
            )

        self._scaler = self._load_pickle("vitals_scaler.pkl")
        self._label_encoder = self._load_pickle("vitals_label_encoder.pkl")
        logger.info("LSTM scaler/encoder 加载成功")

        place = paddle.CPUPlace()
        self._executor = paddle.static.Executor(place)
        (
            self._infer_program,
            self._feed_names,
            self._fetch_targets,
        ) = paddle.static.load_inference_model(
            path_prefix=self.model_dir,
            executor=self._executor,
            model_filename=os.path.basename(model_json),
            params_filename=os.path.basename(params_file),
        )

        logger.info("LSTM 推理模型加载成功, feed_names=%s", self._feed_names)
        self._loaded = True


    def _class_indices(self):
        classes = getattr(self._label_encoder, "classes_", None)
        if classes is None:
            return 0, 1, 2
        classes = [str(c) for c in list(classes)]

        def find_idx(keys):
            for i, c in enumerate(classes):
                for k in keys:
                    if k in c:
                        return i
            return None

        high = find_idx(["高", "high", "HIGH"])
        mid = find_idx(["中", "mid", "medium", "MED"])
        low = find_idx(["低", "low", "LOW"])

        # Fallback ordering if labels don't match
        if high is None:
            high = 2
        if mid is None:
            mid = 1
        if low is None:
            low = 0
        return low, mid, high

    def predict(self, vitals_records_60):
        """
        vitals_records_60: list of dicts with keys hr/spo2/temp, length >= 1.
        Returns: (risk_score float 0..1, risk_level str, reason str, probs dict)
        """
        self._ensure_loaded()

        if not vitals_records_60:
            return 0.0, "低", "无数据", {"low": 1.0, "mid": 0.0, "high": 0.0}

        # Build fixed length window of 60
        window = vitals_records_60[-60:]
        if len(window) < 60:
            pad = window[0]
            window = [pad] * (60 - len(window)) + window

        import numpy as np

        x = np.array([[r.get("hr", 0.0), r.get("spo2", 0.0), r.get("temp", 0.0)] for r in window], dtype="float32")
        # Scale per timestep
        x_scaled = self._scaler.transform(x)
        x_scaled = x_scaled.reshape([1, 60, 3]).astype("float32")

        outputs = self._executor.run(
            self._infer_program,
            feed={self._feed_names[0]: x_scaled},
            fetch_list=self._fetch_targets,
        )
        logits = outputs[0]

        # Softmax
        exp = np.exp(logits - np.max(logits, axis=1, keepdims=True))
        probs = exp / np.sum(exp, axis=1, keepdims=True)
        probs = probs[0].tolist()

        low_i, mid_i, high_i = self._class_indices()
        p_low, p_mid, p_high = probs[low_i], probs[mid_i], probs[high_i]

        # Risk score mapping
        risk_score = float(p_high * 0.9 + p_mid * 0.6 + p_low * 0.1)

        # Risk level by argmax
        pred_i = int(np.argmax(probs))
        try:
            pred_label = str(self._label_encoder.inverse_transform([pred_i])[0])
        except Exception:
            pred_label = "中"

        if "高" in pred_label or "high" in pred_label.lower():
            risk_level = "高"
        elif "低" in pred_label or "low" in pred_label.lower():
            risk_level = "低"
        else:
            risk_level = "中"

        # Minimal, data-driven reason
        latest = window[-1]
        hr, spo2, temp = float(latest.get("hr", 0.0)), float(latest.get("spo2", 0.0)), float(latest.get("temp", 0.0))
        if spo2 and spo2 < 90:
            reason = "血氧偏低，疑似缺氧风险上升"
        elif temp and temp < 35.0:
            reason = "体温偏低，存在失温风险"
        elif hr and hr > 120:
            reason = "心率偏高，建议立即休息评估"
        else:
            reason = "体征总体平稳，继续观察趋势"

        return risk_score, risk_level, reason, {"low": p_low, "mid": p_mid, "high": p_high}


_PREDICTOR = None
_PREDICTOR_LAST_ERROR = None


def get_predictor():
    """Return a loaded VitalsLSTMPredictor or None on failure.

    This helper attempts to eagerly load the Paddle static model so callers
    can detect missing runtime dependencies (like `paddle`) or missing model
    artifacts early and fall back to rule-based logic without confusing
    downstream runtime errors.
    """
    global _PREDICTOR, _PREDICTOR_LAST_ERROR
    if _PREDICTOR is not None:
        return _PREDICTOR

    if not _paddle_available:
        _PREDICTOR_LAST_ERROR = RuntimeError("PaddlePaddle 未安装")
        print(f"[LSTM] PaddlePaddle 未安装，LSTM 模型将不可用", file=sys.stderr)
        return None

    model_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "lstm")
    print(f"[LSTM] 正在加载模型，目录: {model_dir}", file=sys.stderr)
    pred = VitalsLSTMPredictor(model_dir=model_dir)
    try:
        # Try to load the model now to validate environment and artifacts.
        pred._ensure_loaded()
        _PREDICTOR = pred
        _PREDICTOR_LAST_ERROR = None
        print(f"[LSTM] ✅ 模型加载成功!", file=sys.stderr)
        return _PREDICTOR
    except Exception as e:
        # Record the error for diagnostics and return None so callers know
        # the predictor is not usable in this environment.
        _PREDICTOR_LAST_ERROR = e
        _PREDICTOR = None
        print(f"[LSTM] ❌ 模型加载失败: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return None


def get_last_error():
    return _PREDICTOR_LAST_ERROR


