# fact_engine.py
# ==========================================
# 签前秒检 · 事实核查引擎 v3
# 优化：并行搜索/验证、精准提取、智能补搜
# ==========================================

from __future__ import annotations

import json
import time
import logging
from typing import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI
from tavily import TavilyClient

import config
from schemas import (
    AtomicFact, EvidenceItem, VerifiedFact, CheckResponse,
)

logger = logging.getLogger(__name__)

# 根据稿件长度决定最大提取条数
MAX_FACTS_SHORT = 5    # <500字
MAX_FACTS_MEDIUM = 8   # 500-2000字
MAX_FACTS_LONG = 10    # >2000字


class FactEngine:
    """事实核查引擎 v3：精准提取 → 并行搜索 → 并行验证"""

    def __init__(self) -> None:
        self.llm = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.BASE_URL,
        )
        self.tavily = TavilyClient(api_key=config.TAVILY_API_KEY)

    # ======================================================
    # 公开接口
    # ======================================================

    def check(
        self,
        article: str,
        progress: Callable[[str], None] | None = None,
    ) -> CheckResponse:
        """核查流水线：提取 → 并行搜索 → 并行验证 → 报告"""

        def _p(msg: str) -> None:
            if progress:
                progress(msg)
            logger.info(msg)

        # 决定最大提取条数
        article_len = len(article)
        if article_len < 500:
            max_facts = MAX_FACTS_SHORT
        elif article_len < 2000:
            max_facts = MAX_FACTS_MEDIUM
        else:
            max_facts = MAX_FACTS_LONG

        t_total = time.time()

        # Step 1: 提取原子事实 + 生成搜索词（合并为一次LLM调用）
        _p("Step 1/3 · 正在提取硬事实并生成搜索词...")
        t0 = time.time()
        facts_with_queries = self._step1_extract_and_query(article, max_facts)
        t1_cost = round(time.time() - t0, 1)
        if not facts_with_queries:
            return CheckResponse(
                risk_level="通过",
                summary="未提取到需核查的硬事实。",
                total_facts=0, error_count=0, doubt_count=0, pass_count=0,
            )
        facts = [fq["fact"] for fq in facts_with_queries]
        _p(f"  → 提取到 {len(facts)} 条硬事实")

        # Step 2: 并行搜索证据（搜不到自动补搜一次）
        _p("Step 2/3 · 正在并行联网搜索证据...")
        t1 = time.time()
        evidence_map = self._step2_parallel_search(facts_with_queries, _p)
        t2_cost = round(time.time() - t1, 1)
        evidence_found = sum(1 for v in evidence_map.values() if v)

        # Step 3: 并行验证
        _p("Step 3/3 · 正在并行验证...")
        t2 = time.time()
        verified = self._step3_parallel_verify(facts, evidence_map, article, _p)
        t3_cost = round(time.time() - t2, 1)

        total_cost = round(time.time() - t_total, 1)

        # 构建流水线追踪数据
        pipeline = {
            "step1_extract": f"从稿件中提取了{len(facts)}条原子事实并生成搜索词（耗时{t1_cost}秒）",
            "step2_search": f"并行搜索{len(facts)}条事实，{evidence_found}条找到证据（耗时{t2_cost}秒）",
            "step3_verify": f"并行验证{len(facts)}条事实（耗时{t3_cost}秒）",
            "total_time": f"{total_cost}秒",
            "fact_types": {f.type: 0 for f in facts},
        }
        for f in facts:
            pipeline["fact_types"][f.type] += 1

        # 在 items 里加入搜索词和证据数量
        items_extra = []
        for i, fq in enumerate(facts_with_queries):
            items_extra.append({
                "query_used": fq["query"],
                "evidence_found": len(evidence_map.get(i, [])),
            })

        return self._build_response(verified, pipeline, items_extra)

    # ======================================================
    # Step 1: 提取硬事实 + 搜索词（一次LLM调用）
    # ======================================================

    def _step1_extract_and_query(
        self, article: str, max_facts: int,
    ) -> list[dict]:

        prompt = f"""你是一名资深新闻校对专家。请从稿件中提取需要核查的"硬事实"。

## 只提取以下 6 类（其他一律跳过）

1. **person** — 人物姓名（是否存在、是否写错）
2. **title** — 职务头衔（是否准确、是否过时）
3. **time** — 时间日期（是否正确、是否矛盾）
4. **geo** — 地名地点（行政区划是否准确）
5. **number** — 数字数据（金额、比例、统计数据是否准确、前后是否矛盾）
6. **document** — 文件文献名称（法规、报告、规划的名称是否完整准确）

## 不提取的内容（直接跳过）

- 描述性语句、形容词、修辞
- 个人感悟、观点评论
- 过渡语（"据了解""值得注意的是"）
- 无法通过搜索引擎验证的内容

## 提取规则

1. 每条原子事实只包含一个可验证的信息点
2. 最多提取 {max_facts} 条，优先提取最容易出错的
3. 优先级：数字 > 人物职务 > 文件名称 > 时间 > 地点
4. 为每条事实生成 1 个精准搜索词（能在搜索引擎找到权威结果）

## 输出格式（严格JSON）
{{
  "facts": [
    {{"text": "原子事实", "type": "person", "query": "精准搜索关键词"}},
    {{"text": "GDP同比增长5.2%", "type": "number", "query": "2023年北京市GDP增长率 统计局"}}
  ]
}}"""

        result = self._call_llm_json(prompt, article[:4000])
        raw = result.get("facts", [])

        valid_types = {"person", "title", "time", "geo", "number", "document"}
        facts: list[dict] = []
        for item in raw:
            text = item.get("text", "").strip()
            fact_type = item.get("type", "")
            query = item.get("query", text)
            if text and fact_type in valid_types:
                facts.append({
                    "fact": AtomicFact(text=text, type=fact_type),
                    "query": query,
                })
        return facts[:max_facts]

    # ======================================================
    # Step 2: 并行搜索（搜不到自动补搜）
    # ======================================================

    def _step2_parallel_search(
        self,
        facts_with_queries: list[dict],
        progress: Callable[[str], None],
    ) -> dict[int, list[EvidenceItem]]:

        evidence_map: dict[int, list[EvidenceItem]] = {}

        def search_one(idx: int, query: str) -> tuple[int, list[EvidenceItem]]:
            items = self._tavily_search(query)
            # 搜不到时，用原文换个角度补搜一次
            if not items:
                fact_text = facts_with_queries[idx]["fact"].text
                backup_query = f"{fact_text} 最新"
                items = self._tavily_search(backup_query)
            return idx, items

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(search_one, i, fq["query"]): i
                for i, fq in enumerate(facts_with_queries)
            }
            for future in as_completed(futures):
                idx, items = future.result()
                evidence_map[idx] = items

        found = sum(1 for v in evidence_map.values() if v)
        progress(f"  → {found}/{len(facts_with_queries)} 条事实找到证据")
        return evidence_map

    def _tavily_search(self, query: str) -> list[EvidenceItem]:
        """单次 Tavily 搜索"""
        try:
            resp = self.tavily.search(
                query=query,
                search_depth="advanced",
                max_results=3,
            )
            return [
                EvidenceItem(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", "")[:500],
                )
                for r in resp.get("results", [])
            ]
        except Exception as e:
            logger.warning(f"搜索失败 [{query}]: {e}")
            return []

    # ======================================================
    # Step 3: 并行验证
    # ======================================================

    def _step3_parallel_verify(
        self,
        facts: list[AtomicFact],
        evidence_map: dict[int, list[EvidenceItem]],
        article: str,
        progress: Callable[[str], None],
    ) -> list[VerifiedFact]:

        results: dict[int, VerifiedFact] = {}

        def verify_one(idx: int) -> tuple[int, VerifiedFact]:
            fact = facts[idx]
            evidence = evidence_map.get(idx, [])
            evidence_text = "\n".join(
                f"- [{e.title}]({e.url}): {e.snippet}" for e in evidence
            ) or "无搜索结果"

            if fact.type == "number":
                result = self._verify_number(fact, evidence_text, article)
            else:
                result = self._verify_general(fact, evidence_text)

            evidence_urls = [e.url for e in evidence if e.url]
            return idx, VerifiedFact(
                fact=fact.text,
                fact_type=fact.type,
                result=result["result"],
                reason=result["reason"],
                evidence_urls=evidence_urls[:3],
                suggestion=result.get("suggestion", ""),
            )

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(verify_one, i): i
                for i in range(len(facts))
            }
            for future in as_completed(futures):
                idx, verified = future.result()
                results[idx] = verified

        progress(f"  → {len(results)} 条事实验证完成")
        # 按原始顺序返回
        return [results[i] for i in range(len(facts))]

    # ======================================================
    # 验证 prompt
    # ======================================================

    def _verify_general(self, fact: AtomicFact, evidence: str) -> dict:
        """通用验证：人名、职务、时间、地点、文件名称"""

        type_labels = {
            "person": "人物姓名",
            "title": "职务头衔",
            "time": "时间日期",
            "geo": "地名地点",
            "document": "文件文献名称",
        }
        label = type_labels.get(fact.type, "事实")

        prompt = f"""你是一名严谨的事实核查员。请核查以下{label}的准确性。

【待核查事实】：{fact.text}
【搜索证据】：
{evidence}

## 核查要求
1. 将事实与证据逐一比对
2. 证据明确否定 → "错误"
3. 证据不足或有矛盾 → "存疑"
4. 证据支持 → "通过"
5. 无搜索结果 → "存疑"

## 输出格式（严格JSON）
{{
  "result": "错误/存疑/通过",
  "reason": "一句话说明判断依据，引用具体证据",
  "suggestion": "如果有误，给出修改建议；如果通过则为空"
}}"""

        return self._call_llm_json_direct(prompt)

    def _verify_number(self, fact: AtomicFact, evidence: str, article: str) -> dict:
        """数字数据验证：外部比对 + 内部一致性"""

        prompt = f"""你是一名严谨的数据核查员。请核查以下数字数据的准确性。

【待核查数据】：{fact.text}
【搜索证据】：
{evidence}

【稿件全文节选（用于检查内部数据一致性）】：
{article[:2000]}

## 核查要求
1. **外部比对**：数值与搜索证据是否一致？
2. **内部一致性**：稿件内部是否存在数据矛盾？
   - 例如：前文说"总投资3亿元"，后文说"投资2.8亿元"
   - 例如：各分项之和 ≠ 总数
   - 例如：百分比之和超过100%
3. 证据明确否定 或 内部矛盾 → "错误"
4. 证据不足 → "存疑"
5. 一致 → "通过"

## 输出格式（严格JSON）
{{
  "result": "错误/存疑/通过",
  "reason": "说明判断依据，内部矛盾需指出矛盾点",
  "suggestion": "如果有误，给出修改建议；如果通过则为空"
}}"""

        return self._call_llm_json_direct(prompt)

    # ======================================================
    # 结构化输出
    # ======================================================

    def _build_response(
        self,
        verified: list[VerifiedFact],
        pipeline: dict,
        items_extra: list[dict],
    ) -> CheckResponse:

        error_count = sum(1 for v in verified if v.result == "错误")
        doubt_count = sum(1 for v in verified if v.result == "存疑")
        pass_count = sum(1 for v in verified if v.result == "通过")

        if error_count > 0:
            risk_level = "高危"
        elif doubt_count > 0:
            risk_level = "存疑"
        else:
            risk_level = "通过"

        summary = (
            f"共核查 {len(verified)} 条硬事实，"
            f"发现 {error_count} 处错误、{doubt_count} 处存疑、{pass_count} 处通过。"
            f"\n\n—— 签前秒检 · rmrbtzk-v3 引擎"
        )

        # 按风险排序，保留原始索引以匹配 items_extra
        order = {"错误": 0, "存疑": 1, "通过": 2}
        indexed = sorted(enumerate(verified), key=lambda x: order.get(x[1].result, 3))

        items = [
            {
                "fact": v.fact,
                "type": v.fact_type,
                "result": v.result,
                "reason": v.reason,
                "evidence_urls": v.evidence_urls,
                "suggestion": v.suggestion,
                "query_used": items_extra[i]["query_used"],
                "evidence_found": items_extra[i]["evidence_found"],
            }
            for i, v in indexed
        ]

        return CheckResponse(
            risk_level=risk_level,
            summary=summary,
            total_facts=len(verified),
            error_count=error_count,
            doubt_count=doubt_count,
            pass_count=pass_count,
            items=items,
            pipeline=pipeline,
        )

    # ======================================================
    # LLM 调用
    # ======================================================

    def _call_llm_json(self, system_prompt: str, user_content: str) -> dict:
        try:
            res = self.llm.chat.completions.create(
                model=config.MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            return json.loads(res.choices[0].message.content)
        except Exception as e:
            logger.error(f"LLM JSON 调用失败: {e}")
            return {}

    def _call_llm_json_direct(self, prompt: str) -> dict:
        try:
            res = self.llm.chat.completions.create(
                model=config.MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            return json.loads(res.choices[0].message.content)
        except Exception as e:
            logger.error(f"LLM JSON 调用失败: {e}")
            return {"result": "存疑", "reason": f"API调用失败: {e}", "suggestion": ""}
