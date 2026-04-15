"""完整打印 /check/facts 返回，查看字段名是新的还是旧的。"""
import json
import requests

resp = requests.post(
    "http://59.110.152.9:8000/check/facts",
    json={"content": "2023年北京市GDP达到43760亿元，同比增长5.2%。"},
    timeout=180,
)
print(f"HTTP {resp.status_code}")
data = resp.json()
print("顶层字段：", list(data.keys()))
print(f"含 doubt_count? {'doubt_count' in data}")
print(f"含 no_result_count? {'no_result_count' in data}")
print(f"含 not_searched_count? {'not_searched_count' in data}")
print("\n完整响应：")
print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
