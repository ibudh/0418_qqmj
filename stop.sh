#!/bin/bash
# 停止服务
if [ -f server.pid ]; then
    kill $(cat server.pid) 2>/dev/null
    rm server.pid
    echo "服务已停止"
else
    echo "未找到 server.pid，尝试查找进程..."
    pkill -f "uvicorn api_server:app" && echo "已停止" || echo "未找到运行中的服务"
fi
