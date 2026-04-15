"""按 8000 端口精确杀进程，强制重启。"""
import time
import paramiko

HOST = "59.110.152.9"; USER = "root"; PASSWORD = "xbu~KiZ+F+&~7!H"
REMOTE_DIR = "/root/0418_qqmj"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)

# fuser 按端口杀，最稳
stdin, stdout, _ = ssh.exec_command(
    "fuser -k -9 8000/tcp 2>&1 ; "
    "sleep 2 ; "
    "ss -lntp | grep ':8000' || echo 'port 8000 free' ; "
    "echo --- ; "
    "ps -ef | grep -E 'uvicorn|api_server' | grep -v grep || echo 'no uvicorn'"
)
print(stdout.read().decode())

# 清 pyc
ssh.exec_command(
    f"find {REMOTE_DIR} -name '__pycache__' -type d -exec rm -rf {{}} + 2>/dev/null ; "
    f"find {REMOTE_DIR} -name '*.pyc' -delete 2>/dev/null ; "
    f"rm -f {REMOTE_DIR}/server.pid"
)
time.sleep(1)

# 启动
stdin, stdout, _ = ssh.exec_command(f"cd {REMOTE_DIR} && bash start.sh 2>&1")
print(stdout.read().decode())
time.sleep(5)

stdin, stdout, _ = ssh.exec_command("ps -ef | grep -E 'uvicorn|api_server' | grep -v grep")
print("\nnew processes:")
print(stdout.read().decode())

stdin, stdout, _ = ssh.exec_command("curl -s http://localhost:8000/health")
print("health:", stdout.read().decode())

stdin, stdout, _ = ssh.exec_command(
    "curl -s -X POST http://localhost:8000/check/facts "
    "-H 'Content-Type: application/json' "
    "-d '{\"content\":\"2023年北京市GDP达到43760亿元，同比增长5.2%。\"}' "
    "| python3 -c 'import sys,json; d=json.load(sys.stdin); "
    "print(\"keys:\", list(d.keys()))'"
)
print("field check:", stdout.read().decode())
ssh.close()
