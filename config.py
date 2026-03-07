import os

# API Keys - 从环境变量读取，本地可在 .env 文件中配置
QWEATHER_KEY = os.getenv("QWEATHER_KEY", "")
AI_STUDIO_TOKEN = os.getenv("AI_STUDIO_TOKEN", "")
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN", "")
BAIDU_AK = os.getenv("BAIDU_AK", "")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "tianya_new_5/models/lstm/", "vitals_lstm_model.pdparams")
DATA_PATH = os.path.join(BASE_DIR, "dataset", "simulated_vitals.csv")
DB_PATH = os.path.join(BASE_DIR, "tianya.db")
