import paramiko
HOST = "59.110.152.9"; USER = "root"; PASSWORD = "xbu~KiZ+F+&~7!H"
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)
stdin, stdout, _ = ssh.exec_command(
    "tail -n 200 /root/0418_qqmj/server.log | grep -E '搜索失败|致命|Tavily|TAVILY|WARNING|ERROR' | tail -n 40"
)
print(stdout.read().decode(errors="replace"))
ssh.close()
