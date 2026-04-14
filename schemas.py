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

FactType = Literal["person", "title", "time", "geo", "number", "document"]
VerifyResult = Literal["错误", "存疑", "通过"]


@dataclass(frozen=True)
class AtomicFact:
    """Step 1 输出：一条原子事实"""
    text: str
    type: FactType = "other"
    priority: int = 2            # 1=必查(国家级) 2=重点查(省部级及以下)
    context_hierarchy: str = ""  # geo专用：稿件中明确的上级行政单位
    context_missing: bool = False  # geo专用：稿件中未提供上下级关系
    time_context: str = ""       # 时空锚点：事实对应的时间上下文（如"2015年""任期2018-2022"）


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
    source_name: str = ""   # 可读站点名，如"人民网"、"国家统计局"


@dataclass(frozen=True)
class VerifiedFact:
    """Step 5 输出：一条验证结果"""
    fact: str
    fact_type: FactType
    result: VerifyResult
    reason: str
    evidence_urls: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # [{name, url}, ...]
    suggestion: str = ""
    priority: int = 2


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
    pipeline: dict = field(default_factory=dict)
    engine: str = "rmrbtzk-v3"
