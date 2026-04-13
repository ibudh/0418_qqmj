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
    suggestion: str = ""


class CheckFactsResponse(BaseModel):
    risk_level: str
    summary: str
    total_facts: int
    error_count: int
    doubt_count: int
    pass_count: int
    items: list[FactItem]


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


# ── 启动入口 ──

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
