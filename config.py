# config.py
# ==========================================
# 全局配置中心
# ==========================================

# 👇 请务必在这里填入你的 API Key
DEEPSEEK_API_KEY = "your-deepseek-api-key-here"
TAVILY_API_KEY = "your-tavily-api-key-here"

# DeepSeek 官方 API 地址
BASE_URL = "https://api.deepseek.com"

# 模型名称
MODEL_NAME = "deepseek-chat"

# 行政区划本地数据（国家统计局2023年省市区三级）
GEO_DATA_PATH = "pcas-code.json"  # 省市区乡镇四级（2023年，41352个乡镇街道）