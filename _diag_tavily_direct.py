"""直接调 Tavily API，不经过 fact_engine，看真实响应。仅诊断，不改服务器。"""
import paramiko

HOST = "59.110.152.9"
USER = "root"
PASSWORD = "xbu~KiZ+F+&~7!H"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)

# 用服务器上的环境跑 Tavily 直接调用，看三组真实查询的命中情况
diag = '''
import sys
sys.path.insert(0, "/root/0418_qqmj")
from config import TAVILY_API_KEY
from tavily import TavilyClient
c = TavilyClient(api_key=TAVILY_API_KEY)
cases = [
    ("土默特左旗 行政区划 隶属", ["gov.cn"]),
    ("土默特左旗 行政区划 隶属", None),
    ("2012 第一批 中国传统村落", ["gov.cn"]),
    ("2012 第一批 中国传统村落", None),
    ("2010 第二批 内蒙古 非物质文化遗产", ["gov.cn"]),
    ("2010 第二批 内蒙古 非物质文化遗产", None),
]
for q, dom in cases:
    try:
        r = c.search(query=q, search_depth="advanced", max_results=5, include_domains=dom) if dom else c.search(query=q, search_depth="advanced", max_results=5)
        hits = r.get("results", [])
        print(f"[{len(hits)} 命中] q={q!r} dom={dom}")
        for h in hits[:3]:
            print(f"   - {h.get('url','')[:90]}")
    except Exception as e:
        print(f"[ERROR] q={q!r} dom={dom}: {e}")
'''
stdin, stdout, stderr = ssh.exec_command(f"cd /root/0418_qqmj && python3 -c '{diag}'")
print(stdout.read().decode())
err = stderr.read().decode()
if err:
    print("STDERR:", err)
ssh.close()
