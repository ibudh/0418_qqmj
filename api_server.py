# api_server.py
# ==========================================
# FastAPI 服务端 — 签前秒检 API
# ==========================================

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from fact_engine import FactEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

app = FastAPI(
    title="签前秒检 API",
    description="事实核查引擎：原子分解 → 分类 → HyDE搜索 → 验证 → 报告",
    version="2.0",
)

# 启动时初始化引擎（只初始化一次）
engine = FactEngine()


# ── 请求/响应模型（Pydantic，用于 API 文档和校验）──

class ArticleRequest(BaseModel):
    content: str = Field(..., min_length=10, description="待核查的稿件全文")


class FactItem(BaseModel):
    fact: str
    type: str
    result: str
    reason: str
    evidence_urls: list[str] = []
    sources: list[dict] = []     # [{name: "人民网", url: "..."}, ...]
    suggestion: str = ""
    query_used: str = ""
    evidence_found: int = 0
    source_tier: str = ""  # 信源层级：官方 / 央媒 / 其他


class CheckFactsResponse(BaseModel):
    risk_level: str
    summary: str
    total_facts: int
    error_count: int
    pass_count: int
    no_result_count: int      # 搜了没搜到
    not_searched_count: int   # 没搜（配额限制）
    items: list[FactItem]
    pipeline: dict = {}
    engine: str = "rmrbtzk-v3"


# ── 端点 ──

@app.post("/check/facts", response_model=CheckFactsResponse)
async def check_facts(req: ArticleRequest) -> CheckFactsResponse:
    """
    事实核查端点。

    接收稿件全文，执行 6 步流水线：
    1. 原子事实分解
    2. 分类打标
    3. HyDE 搜索词生成
    4. Tavily 多角度搜索
    5. 分类型验证判断
    6. 结构化输出
    """
    try:
        result = engine.check(req.content)
        return CheckFactsResponse(**asdict(result))
    except Exception as e:
        logging.error(f"核查失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"核查过程出错: {str(e)}")


@app.get("/health")
async def health() -> dict:
    """健康检查端点，供初芯平台或监控使用"""
    return {"status": "ok", "version": "2.0"}


# ── 调试端点 ──

class ExtractDebugResponse(BaseModel):
    total: int
    facts: list[dict]


@app.post("/debug/extract", response_model=ExtractDebugResponse)
async def debug_extract(req: ArticleRequest) -> ExtractDebugResponse:
    """
    调试端点：只运行 Step 1，返回提取的原子事实列表，不做搜索和验证。
    用于排查哪些事实被提取、搜索词是什么。
    """
    try:
        article_len = len(req.content)
        if article_len < 500:
            max_facts = 5
        elif article_len < 2000:
            max_facts = 8
        else:
            max_facts = 10

        facts_with_queries = engine._step1_extract_and_query(req.content, max_facts)
        facts = [
            {
                "text": fq["fact"].text,
                "type": fq["fact"].type,
                "priority": fq["fact"].priority,
                "context_hierarchy": fq["fact"].context_hierarchy,
                "context_missing": fq["fact"].context_missing,
                "time_context": fq["fact"].time_context,
                "query": fq["query"],
            }
            for fq in facts_with_queries
        ]
        return ExtractDebugResponse(total=len(facts), facts=facts)
    except Exception as e:
        logging.error(f"调试提取失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"提取过程出错: {str(e)}")


# ── 启动入口 ──

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
