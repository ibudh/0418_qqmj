"""远程部署脚本：通过SSH上传文件并启动服务"""
import paramiko
import os

HOST = "59.110.152.9"
USER = "root"
PASSWORD = "xbu~KiZ+F+&~7!H"
REMOTE_DIR = "/root/0418_qqmj"

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
FILES = [
    "config.py",
    "fact_engine.py",
    "schemas.py",
    "api_server.py",
    "geo_lookup.py",
    "pca-code.json",
    "pcas-code.json",
    "requirements.txt",
    "start.sh",
    "stop.sh",
]


def main():
    print(f"连接 {HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    print("SSH 连接成功")

    # 创建远程目录
    ssh.exec_command(f"mkdir -p {REMOTE_DIR}")

    # 上传文件
    sftp = ssh.open_sftp()
    for f in FILES:
        local = os.path.join(PROJECT_DIR, f)
        remote = f"{REMOTE_DIR}/{f}"
        print(f"  上传 {f}...")
        sftp.put(local, remote)
    sftp.close()
    print("文件上传完成")

    # 在服务器上写入真实 API Key（不影响本地的占位符 config）
    print("  写入服务器端 config.py（含真实 API Key）...")
    stdin, stdout, stderr = ssh.exec_command(f"""cat > {REMOTE_DIR}/config.py << 'PYEOF'
# config.py
DEEPSEEK_API_KEY = "sk-740673925ab64b76a1cf314493a0e35e"
TAVILY_API_KEY = "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"
BASE_URL = "https://api.deepseek.com"
MODEL_NAME = "deepseek-chat"
GEO_DATA_PATH = "pcas-code.json"
PYEOF""")
    stdout.read()

    # 安装依赖
    print("安装 Python 依赖...")
    stdin, stdout, stderr = ssh.exec_command(
        f"cd {REMOTE_DIR} && pip3 install -r requirements.txt 2>&1"
    )
    print(stdout.read().decode())

    # 停止旧服务（如果有）
    ssh.exec_command(f"cd {REMOTE_DIR} && bash stop.sh 2>/dev/null")

    # 启动服务
    print("启动 API 服务...")
    stdin, stdout, stderr = ssh.exec_command(
        f"cd {REMOTE_DIR} && bash start.sh 2>&1"
    )
    print(stdout.read().decode())

    # 验证
    print("验证服务...")
    import time
    time.sleep(3)
    stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/health")
    result = stdout.read().decode()
    print(f"  health 响应: {result}")

    ssh.close()

    if '"ok"' in result:
        print(f"\n部署成功！公网地址: http://{HOST}:8000")
        print(f"健康检查: http://{HOST}:8000/health")
        print(f"API文档:  http://{HOST}:8000/docs")
    else:
        print("\n服务可能未正常启动，请检查日志")


if __name__ == "__main__":
    main()
