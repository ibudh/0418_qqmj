"""强制杀掉所有 uvicorn 进程并重启，让新版代码真正上线。"""
import time
import paramiko

HOST = "59.110.152.9"; USER = "root"; PASSWORD = "xbu~KiZ+F+&~7!H"
REMOTE_DIR = "/root/0418_qqmj"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)

# 1. 强杀所有 uvicorn 进程 + 清理 pyc 缓存
stdin, stdout, _ = ssh.exec_command(
    f"pkill -9 -f 'uvicorn api_server' ; "
    f"pkill -9 -f 'python3 -m uvicorn' ; "
    f"sleep 2 ; "
    f"ps -ef | grep -E 'uvicorn|api_server' | grep -v grep ; "
    f"echo --- ; "
    f"find {REMOTE_DIR} -name '__pycache__' -type d -exec rm -rf {{}} + 2>/dev/null ; "
    f"find {REMOTE_DIR} -name '*.pyc' -delete 2>/dev/null ; "
    f"echo pycache cleaned ; "
    f"rm -f {REMOTE_DIR}/server.pid"
)
print("kill result:")
print(stdout.read().decode())

# 2. 确认 8000 端口空了
stdin, stdout, _ = ssh.exec_command("ss -lntp | grep ':8000' || echo '8000 port free'")
print("port check:", stdout.read().decode().strip())

# 3. 启动新服务
stdin, stdout, _ = ssh.exec_command(f"cd {REMOTE_DIR} && bash start.sh 2>&1")
print("start:")
print(stdout.read().decode())

time.sleep(4)

# 4. 确认新进程启动
stdin, stdout, _ = ssh.exec_command("ps -ef | grep -E 'uvicorn|api_server' | grep -v grep")
print("new processes:")
print(stdout.read().decode())

# 5. 健康检查
stdin, stdout, _ = ssh.exec_command("curl -s http://localhost:8000/health")
print("health:", stdout.read().decode())

# 6. 打一次请求看响应字段有没有新字段
stdin, stdout, _ = ssh.exec_command(
    "curl -s -X POST http://localhost:8000/check/facts "
    "-H 'Content-Type: application/json' "
    "-d '{\"content\":\"2023年北京市GDP达到43760亿元，同比增长5.2%。\"}' "
    "| python3 -c 'import sys,json; d=json.load(sys.stdin); "
    "print(\"keys:\", list(d.keys())); "
    "print(\"has no_result_count:\", \"no_result_count\" in d); "
    "print(\"has doubt_count:\", \"doubt_count\" in d)'"
)
print("field check:", stdout.read().decode())

ssh.close()
