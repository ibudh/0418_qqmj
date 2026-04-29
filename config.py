# config.py
# ==========================================
# 全局配置中心 — API Key 从环境变量读取
# ==========================================
import os

# 从环境变量读取（本地开发可在 .env 文件中设置）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BOCHA_API_KEY    = os.environ.get("BOCHA_API_KEY", "")
BAIDU_API_KEY    = os.environ.get("BAIDU_API_KEY", "")

# DeepSeek 官方 API 地址
BASE_URL = "https://api.deepseek.com"

# 模型名称
MODEL_NAME = "deepseek-chat"

# 行政区划本地数据（国家统计局2023年省市区乡镇四级，41352个乡镇街道）
GEO_DATA_PATH = "data/pcas-code.json"
DATA_DIR      = "data"
