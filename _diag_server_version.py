"""诊断服务器上跑的代码版本。"""
import paramiko
HOST = "59.110.152.9"; USER = "root"; PASSWORD = "xbu~KiZ+F+&~7!H"
REMOTE_DIR = "/root/0418_qqmj"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)

# 关键标志串：新版 api_server 有 TavilyFatalError；新版 schemas 有 no_result_count/not_searched_count
checks = [
    ("TavilyFatalError in api_server.py",
     f"grep -c 'TavilyFatalError' {REMOTE_DIR}/api_server.py"),
    ("no_result_count in schemas.py",
     f"grep -c 'no_result_count' {REMOTE_DIR}/schemas.py"),
    ("GEO_AUTHORITY_SOURCE in fact_engine.py",
     f"grep -c 'GEO_AUTHORITY_SOURCE' {REMOTE_DIR}/fact_engine.py"),
    ("TavilyFatalError class in fact_engine.py",
     f"grep -c 'class TavilyFatalError' {REMOTE_DIR}/fact_engine.py"),
    ("fact_engine.py mtime",
     f"stat -c '%y' {REMOTE_DIR}/fact_engine.py"),
    ("api_server.py mtime",
     f"stat -c '%y' {REMOTE_DIR}/api_server.py"),
    ("schemas.py mtime",
     f"stat -c '%y' {REMOTE_DIR}/schemas.py"),
    ("start.sh content",
     f"cat {REMOTE_DIR}/start.sh"),
]
for label, cmd in checks:
    stdin, stdout, _ = ssh.exec_command(cmd)
    out = stdout.read().decode().strip()
    print(f"[{label}]\n{out}\n")
ssh.close()
