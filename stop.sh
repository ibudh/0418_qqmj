#!/bin/bash
# 停止服务 —— 按 8000 端口精确清理，避免 pkill 漏掉 uvicorn worker
# 历史教训：uvicorn --workers N 的 worker 进程命令行不含 "uvicorn api_server"，
# pkill 按模式匹配会漏杀；fuser 按端口杀最稳。

PORT=8000
PIDS=$(fuser ${PORT}/tcp 2>/dev/null | tr -d ' \n')

if [ -n "$PIDS" ]; then
    fuser -k -9 ${PORT}/tcp 2>/dev/null
    sleep 2
    if fuser ${PORT}/tcp 2>/dev/null >/dev/null; then
        echo "警告：端口 ${PORT} 上仍有进程未能杀掉"
        exit 1
    fi
    echo "服务已停止（清理了 PID: $PIDS）"
else
    # 兜底：按进程名模式清理可能存在的孤儿进程
    pkill -9 -f "uvicorn api_server" 2>/dev/null
    pkill -9 -f "api_server:app" 2>/dev/null
    echo "端口 ${PORT} 空闲，已尝试清理残留进程"
fi

rm -f server.pid
