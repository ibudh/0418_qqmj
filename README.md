# 签前秒检

新闻稿件事实核查 API —— 基于原子事实分解、本地区划库与联网搜索验证。

## 核心能力

输入一篇新闻稿件，自动完成：

1. **硬事实提取** — 从稿件中精准提取 11 类可核查事实（人物、职务、时间、地名、数据、法规、机构、文学、诗文、引语、媒体），按三级优先级排序（P1国家/国际、P2省级、P3市县乡），自动绑定时间锚点
2. **并行联网搜索** — 严格限定权威信源（`gov.cn` 优先 → 央媒兜底），绝不使用泛搜，杜绝自媒体干扰；5 线程并行，搜不到判"未搜到"交人工复核
3. **分类型并行验证**：
   - **地名** — 本地区划库优先 + gov.cn 兜底 + 稿件内部地名一致性交叉检查
   - **数据** — Python 算术校验（总分校验/百分比求和）+ LLM 外部证据比对，双层验证
   - **人物/职务** — 时间维度感知验证，区分"时任"与"现任"，避免历史事实误报
4. **结构化报告** — 输出风险等级、错误详情、证据来源、修改建议，附带流水线耗时追踪

## 技术架构

```
初芯平台 Chatflow（前端对话）
        │
        ▼  HTTP POST /check/facts
FastAPI 后端（核查引擎 v3）
        │
   ┌────┼────────┐
   ▼    ▼        ▼
DeepSeek Tavily  GeoLookup
  API    API     本地区划库
                 (统计局2023)
```

### 地名核查：两层漏斗 + 内部一致性

1. **本地区划库**（GeoLookup）— 基于国家统计局 2023 年省市区乡镇四级数据，毫秒级精确匹配，命中即返回
2. **Tavily gov.cn 搜索** — 本地未命中时（新设区划等），定向搜索政府网站兜底验证
3. **稿件内部一致性** — 自动检测同一地名在标题与正文中是否出现不同上级归属（如标题写"呼和浩特"、正文写"呼伦贝尔市"）

### 数据核查：三层漏斗

```
稿件中的数据事实
        │
        ▼ ① LLM 结构化提取
  related_numbers + math_relations
        │
        ▼ ② Python 算术校验（确定性）
  总分校验 / 百分比求和
  ┌──────┴──────┐
  发现矛盾      通过
  → 直接判"错误"    │
  （零token消耗）   ▼ ③ 严格信源搜索
              gov.cn / stats.gov.cn
              ↓ 未命中
              people.com.cn / xinhuanet.com
              ↓ 仍未命中 → 不做泛搜，判"未搜到"
                │
                ▼ ④ LLM 语义比对
          证据 vs 稿件数据
          → 错误 / 通过 / 未搜到
```

1. **结构化提取** — LLM 提取数值及其数学关系（总量/分项/占比），输出 `related_numbers` 和 `math_relations`
2. **Python 硬算** — 总分校验、百分比求和等确定性验证，发现矛盾直接短路返回，零 token 消耗，100% 准确
3. **权威信源搜索** — 仅搜政府/统计局（`gov.cn`）和央媒（`people.com.cn` / `xinhuanet.com`），搜不到即判"未搜到"交人工复核
4. **LLM 语义比对** — 将权威信源证据与稿件数据逐一比对，处理算术无法覆盖的场景（如数据是否过时、口径是否一致）
5. **信源标注** — 每条核查结果标注证据来源层级（`官方` / `央媒` / `其他`），编辑一眼可见数据可信度

### 时空锚点

提取事实时自动绑定时间上下文（如"2015年时任副市长"），搜索和验证阶段均以该时间点为准，避免"用今天的标准核查昨天的事实"导致的误报。

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
  "summary": "共核查 5 条硬事实，发现 2 处错误、2 处通过、1 处未搜到、0 处未检索。",
  "total_facts": 5,
  "error_count": 2,
  "pass_count": 2,
  "no_result_count": 1,
  "not_searched_count": 0,
  "engine": "rmrbtzk-v3",
  "pipeline": {
    "step1_extract": "从稿件中提取了5条原子事实并生成搜索词（耗时2.1秒）",
    "step2_search": "并行搜索5条事实，4条找到证据（耗时3.5秒）",
    "step3_verify": "并行验证5条事实（耗时2.8秒）",
    "total_time": "8.4秒"
  },
  "items": [
    {
      "fact": "北京市副市长张伟",
      "type": "person",
      "priority": 1,
      "result": "错误",
      "reason": "北京市副市长名单中无张伟",
      "evidence_urls": ["..."],
      "suggestion": "请核实人名及职务",
      "query_used": "北京市副市长 张伟",
      "evidence_found": 3,
      "source_tier": "官方"
    }
  ]
}
```

### GET /health

健康检查，返回 `{"status": "ok", "version": "2.0"}`。

## 项目结构

```
├── config.py           # 全局配置（API Key、模型、区划数据路径）
├── fact_engine.py      # 核查引擎 v3（3步流水线：提取→并行搜索→并行验证）
├── schemas.py          # 数据模型（AtomicFact / VerifiedFact / CheckResponse）
├── geo_lookup.py       # 行政区划本地查询（省市区乡镇四级验证）
├── pcas-code.json      # 区划数据：省市区乡镇四级（国家统计局2023）
├── pca-code.json       # 区划数据：省市区三级
├── api_server.py       # FastAPI 服务端点
├── deploy_remote.py    # 远程部署脚本
├── sync_server.py      # 服务器同步脚本
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
