# 稿件硬伤猎手

新闻稿件事实核查 API —— 基于原子事实分解与联网搜索验证。

## 核心能力

输入一篇新闻稿件，自动完成：

1. **原子事实分解** — 将稿件拆解为可独立验证的最小事实单元
2. **智能分类** — 按人物职务、数据、地名、时间、引用等类型打标
3. **多角度搜索** — 为每条事实生成多维搜索词（HyDE），联网获取权威证据
4. **分类型验证** — 人物类搜索比对，数据类额外做内部一致性检查
5. **结构化报告** — 输出风险等级、错误详情、证据来源、修改建议

## 技术架构

```
初芯平台 Chatflow（前端对话）
        │
        ▼  HTTP POST /check/facts
FastAPI 后端（核查引擎）
        │
   ┌────┴────┐
   ▼         ▼
DeepSeek   Tavily
  API       API
```

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key（编辑 config.py）
# 填入你的 DeepSeek 和 Tavily API Key

# 3. 启动服务
python api_server.py

# 4. 测试
curl http://localhost:8000/health
```

## API 接口

### POST /check/facts

请求：
```json
{
  "content": "待核查的新闻稿件全文"
}
```

响应：
```json
{
  "risk_level": "高危",
  "summary": "共核查 14 条原子事实，发现 8 处错误、3 处存疑、3 处通过。",
  "total_facts": 14,
  "error_count": 8,
  "doubt_count": 3,
  "pass_count": 3,
  "items": [
    {
      "fact": "北京市副市长张伟",
      "type": "person",
      "result": "错误",
      "reason": "北京市副市长名单中无张伟",
      "evidence_urls": ["..."],
      "suggestion": "请核实人名及职务"
    }
  ]
}
```

### GET /health

健康检查，返回 `{"status": "ok", "version": "2.0"}`。

## 项目结构

```
├── config.py           # API Key 配置
├── fact_engine.py      # 核查引擎（6步流水线）
├── schemas.py          # 数据模型
├── api_server.py       # FastAPI 服务端点
├── requirements.txt    # Python 依赖
├── start.sh            # Linux 启动脚本
├── stop.sh             # Linux 停止脚本
├── DEPLOY_GUIDE.md     # 部署与初芯平台配置指南
└── README.md
```

## 部署

详见 [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md)。

## 参考

核查引擎融合了以下学术思路：

- **FActScore** — 原子事实分解方法
- **FacTool** — 分类型多策略验证
- **Fathom/HyDE** — 假设文档嵌入搜索词生成
