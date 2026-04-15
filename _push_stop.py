"""把修好的 stop.sh 推到服务器。"""
import os
import paramiko
HOST = "59.110.152.9"; USER = "root"; PASSWORD = "xbu~KiZ+F+&~7!H"
REMOTE_DIR = "/root/0418_qqmj"
HERE = os.path.dirname(os.path.abspath(__file__))

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)

sftp = ssh.open_sftp()
sftp.put(os.path.join(HERE, "stop.sh"), f"{REMOTE_DIR}/stop.sh")
sftp.close()

# 确保权限 + 转 LF（Windows 换行会让 bash 报错）
ssh.exec_command(f"chmod +x {REMOTE_DIR}/stop.sh && sed -i 's/\\r$//' {REMOTE_DIR}/stop.sh")
print("stop.sh pushed & normalized")
ssh.close()
