# schemas.py
# 请求/响应数据模型

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ── 请求 ──

@dataclass(frozen=True)
class ArticleInput:
    content: str


# ── 内部流转 ──

FactType = Literal["person", "data", "geo", "time", "quote", "other"]
VerifyResult = Literal["错误", "存疑", "通过"]


@dataclass(frozen=True)
class AtomicFact:
    """Step 1 输出：一条原子事实"""
    text: str
    type: FactType = "other"


@dataclass(frozen=True)
class SearchQuery:
    """Step 3 输出：一条搜索词"""
    query: str
    fact_index: int  # 对应哪条原子事实


@dataclass(frozen=True)
class EvidenceItem:
    """Step 4 输出：一条搜索证据"""
    title: str = ""
    url: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class VerifiedFact:
    """Step 5 输出：一条验证结果"""
    fact: str
    fact_type: FactType
    result: VerifyResult
    reason: str
    evidence_urls: list[str] = field(default_factory=list)
    suggestion: str = ""


# ── API 响应 ──

@dataclass(frozen=True)
class CheckResponse:
    risk_level: str          # 高危 / 存疑 / 通过
    summary: str
    total_facts: int
    error_count: int
    doubt_count: int
    pass_count: int
    items: list[dict] = field(default_factory=list)
