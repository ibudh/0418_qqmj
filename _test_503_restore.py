"""从服务器上的 .tavily_key.bak 读回原 key 并重启服务。"""
import time
import paramiko

HOST = "59.110.152.9"
USER = "root"
PASSWORD = "xbu~KiZ+F+&~7!H"
REMOTE_DIR = "/root/0418_qqmj"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)

stdin, stdout, _ = ssh.exec_command(f"cat {REMOTE_DIR}/.tavily_key.bak")
backup_line = stdout.read().decode().strip()
if not backup_line.startswith("TAVILY_API_KEY"):
    print("!! 备份文件异常，停止恢复。内容：", repr(backup_line))
    ssh.close()
    raise SystemExit(1)
print(f"读到备份行（长度 {len(backup_line)}）：{backup_line[:40]}...")

# 用 python 回写（比 sed 处理特殊字符更安全）
write_cmd = (
    f"python3 -c \"import re; "
    f"p='{REMOTE_DIR}/config.py'; "
    f"s=open(p,encoding='utf-8').read(); "
    f"bak=open('{REMOTE_DIR}/.tavily_key.bak',encoding='utf-8').read().strip(); "
    f"s2=re.sub(r'^TAVILY_API_KEY.*', bak, s, count=1, flags=re.M); "
    f"open(p,'w',encoding='utf-8').write(s2); "
    f"print('restored')\""
)
stdin, stdout, stderr = ssh.exec_command(write_cmd)
print(stdout.read().decode())
err = stderr.read().decode()
if err:
    print("STDERR:", err)

stdin, stdout, _ = ssh.exec_command(f"grep '^TAVILY_API_KEY' {REMOTE_DIR}/config.py")
print("现在 config.py 里：", stdout.read().decode().strip()[:40] + "...")

ssh.exec_command(f"cd {REMOTE_DIR} && bash stop.sh 2>/dev/null")
time.sleep(2)
stdin, stdout, _ = ssh.exec_command(f"cd {REMOTE_DIR} && bash start.sh 2>&1")
print(stdout.read().decode())
time.sleep(3)

stdin, stdout, _ = ssh.exec_command("curl -s http://localhost:8000/health")
print(f"health: {stdout.read().decode()}")

# 跑一次简单核查，确认 200
stdin, stdout, _ = ssh.exec_command(
    "curl -s -o /tmp/resp.json -w '%{http_code}' -X POST "
    "http://localhost:8000/check/facts -H 'Content-Type: application/json' "
    "-d '{\"content\":\"2023年北京市GDP达到43760亿元，同比增长5.2%。\"}'"
)
code = stdout.read().decode().strip()
print(f"/check/facts HTTP code: {code}")
ssh.close()

if code == "200":
    print("\n✅ 恢复成功，服务返回 200。")
else:
    print(f"\n⚠️  服务返回 {code}，请检查。")
