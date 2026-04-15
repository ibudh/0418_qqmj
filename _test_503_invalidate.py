"""临时把服务器 TAVILY_API_KEY 改成无效值并重启。验证完必须跑 _test_503_restore.py 恢复。"""
import time
import paramiko

HOST = "59.110.152.9"
USER = "root"
PASSWORD = "xbu~KiZ+F+&~7!H"
REMOTE_DIR = "/root/0418_qqmj"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)

# 备份当前 key 行到服务器（恢复时从这里读）
stdin, stdout, _ = ssh.exec_command(
    f"grep '^TAVILY_API_KEY' {REMOTE_DIR}/config.py > {REMOTE_DIR}/.tavily_key.bak && "
    f"cat {REMOTE_DIR}/.tavily_key.bak"
)
print("backup ok:", stdout.read().decode().strip()[:40])

stdin, stdout, _ = ssh.exec_command(
    f"sed -i 's|^TAVILY_API_KEY.*|TAVILY_API_KEY = \"tvly-invalid-for-test\"|' {REMOTE_DIR}/config.py && "
    f"grep '^TAVILY_API_KEY' {REMOTE_DIR}/config.py"
)
print("after sed:", stdout.read().decode().strip())

ssh.exec_command(f"cd {REMOTE_DIR} && bash stop.sh 2>/dev/null")
time.sleep(2)
stdin, stdout, _ = ssh.exec_command(f"cd {REMOTE_DIR} && bash start.sh 2>&1")
print(stdout.read().decode())
time.sleep(3)

stdin, stdout, _ = ssh.exec_command("curl -s http://localhost:8000/health")
print(f"health: {stdout.read().decode()}")

# 预热：确认 /check/facts 返回 HTTP 503
stdin, stdout, _ = ssh.exec_command(
    "curl -s -o /tmp/resp.json -w '%{http_code}' -X POST "
    "http://localhost:8000/check/facts -H 'Content-Type: application/json' "
    "-d '{\"content\":\"2023年北京市GDP达到43760亿元，同比增长5.2%。\"}'"
    " ; echo ; head -c 300 /tmp/resp.json"
)
print("/check/facts 响应：")
print(stdout.read().decode())
ssh.close()

print("\n==> 现在去初芯前端发一篇稿件，应该看到：")
print("    ⚠️ 审查服务暂不可用")
print("    原因：搜索服务不可用（后端连接失败、超时或返回非 200）...")
print("==> 验证完请立即运行 python _test_503_restore.py 恢复真 Key！")
