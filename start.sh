#!/bin/bash
# ==========================================
# 稿件硬伤猎手 API — 启动脚本
# 用法: bash start.sh
# ==========================================

echo "=== 稿件硬伤猎手 API 启动中 ==="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

# 检查依赖
pip3 install -r requirements.txt -q

# 启动服务（后台运行，日志写入文件）
nohup python3 -m uvicorn api_server:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 2 \
    > server.log 2>&1 &

echo $! > server.pid
echo "=== 服务已启动 ==="
echo "  PID: $(cat server.pid)"
echo "  地址: http://0.0.0.0:8000"
echo "  健康检查: curl http://localhost:8000/health"
echo "  日志: tail -f server.log"
echo "  停止: bash stop.sh"
