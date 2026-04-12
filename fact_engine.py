# fact_engine.py
# ==========================================
# 事实核查引擎 v2 — 6步流水线
# Step 1: 原子事实分解 (FActScore)
# Step 2: 分类打标 (FacTool)
# Step 3: HyDE 搜索词生成 (Fathom)
# Step 4: Tavily 多角度搜索
# Step 5: 分类型验证判断
# Step 6: 结构化输出
# ==========================================

from __future__ import annotations

import json
import time
import logging
from typing import Callable

from openai import OpenAI
from tavily import TavilyClient

import config
from schemas import (
    AtomicFact, EvidenceItem, VerifiedFact, CheckResponse, FactType,
)

logger = logging.getLogger(__name__)


class FactEngine:
    """事实核查引擎：原子分解 → 分类 → HyDE搜索 → 验证 → 报告"""

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
        """完整 6 步流水线，返回结构化结果"""

        def _p(msg: str) -> None:
            if progress:
                progress(msg)
            logger.info(msg)

        # Step 1 + 2: 原子分解 & 分类打标（合并为一次 LLM 调用）
        _p("Step 1/5 · 正在拆解原子事实并分类...")
        facts = self._step1_decompose_and_classify(article)
        if not facts:
            return CheckResponse(
                risk_level="通过",
                summary="未提取到需核查的硬事实。",
                total_facts=0, error_count=0, doubt_count=0, pass_count=0,
            )
        _p(f"  → 提取到 {len(facts)} 条原子事实")

        # Step 3: HyDE 搜索词生成
        _p("Step 2/5 · 正在生成多角度搜索词...")
        queries_map = self._step3_hyde_queries(facts)
        total_queries = sum(len(qs) for qs in queries_map.values())
        _p(f"  → 生成 {total_queries} 条搜索词")

        # Step 4: Tavily 多角度搜索
        _p("Step 3/5 · 正在联网搜索证据...")
        evidence_map = self._step4_search(queries_map, _p)

        # Step 5: 分类型验证
        _p("Step 4/5 · 正在逐条验证...")
        verified = self._step5_verify(facts, evidence_map, article, _p)

        # Step 6: 结构化输出
        _p("Step 5/5 · 正在生成报告...")
        return self._step6_build_response(verified)

    # ======================================================
    # Step 1 + 2: 原子事实分解 & 分类打标
    # ======================================================

    def _step1_decompose_and_classify(self, article: str) -> list[AtomicFact]:
        prompt = """你是一名资深新闻校对专家。请将以下新闻稿件拆解为"原子事实"列表。

## 什么是原子事实
每条原子事实只包含一个可独立验证的信息点。

## 示例
原文："张伟于2024年3月就任北京市副市长，此前他在清华大学任教20年"
拆解为：
  - "张伟就任北京市副市长"  (type: person)
  - "就任时间为2024年3月"  (type: time)
  - "张伟此前在清华大学任教20年"  (type: data)

## 类型标签
- person: 人物姓名、职务、身份
- data: 数字、统计数据、比例、金额
- geo: 地名、行政区划
- time: 时间、日期
- quote: 引用语、出处
- other: 其他可验证事实

## 提取重点
1. 只提取可用搜索引擎验证的硬事实
2. 忽略观点、评论、形容词
3. 数据类事实要保留原文数值
4. 人物类要保留职务全称

## 输出格式（严格JSON）
{
  "facts": [
    {"text": "原子事实1", "type": "person"},
    {"text": "原子事实2", "type": "data"}
  ]
}"""

        result = self._call_llm_json(prompt, article[:4000])
        raw_facts = result.get("facts", [])

        facts: list[AtomicFact] = []
        for item in raw_facts:
            text = item.get("text", "").strip()
            fact_type = item.get("type", "other")
            if text and fact_type in ("person", "data", "geo", "time", "quote", "other"):
                facts.append(AtomicFact(text=text, type=fact_type))

        return facts

    # ======================================================
    # Step 3: HyDE 搜索词生成
    # ======================================================

    def _step3_hyde_queries(self, facts: list[AtomicFact]) -> dict[int, list[str]]:
        """为每条原子事实生成 2-3 个搜索 query"""

        facts_text = "\n".join(
            f"[{i}] ({f.type}) {f.text}" for i, f in enumerate(facts)
        )

        prompt = """你是一名信息检索专家。针对以下每条原子事实，生成 2-3 个不同角度的中文搜索关键词。

## 要求
1. 搜索词要具体、有针对性，能在搜索引擎中找到权威结果
2. 不同搜索词覆盖不同验证角度（如：官方任命、新闻报道、百科词条）
3. person类：搜索人物+职务+任命
4. data类：搜索具体数据+来源+统计年份
5. geo类：搜索行政区划+最新名称
6. time类：搜索事件+时间+报道

## 输出格式（严格JSON）
{
  "queries": {
    "0": ["搜索词1", "搜索词2", "搜索词3"],
    "1": ["搜索词1", "搜索词2"]
  }
}"""

        result = self._call_llm_json(prompt, facts_text)
        raw = result.get("queries", {})

        queries_map: dict[int, list[str]] = {}
        for key, val in raw.items():
            try:
                idx = int(key)
                if isinstance(val, list) and 0 <= idx < len(facts):
                    queries_map[idx] = [str(q) for q in val[:3]]
            except (ValueError, TypeError):
                continue

        # 兜底：如果某条 fact 没生成 query，用原文
        for i in range(len(facts)):
            if i not in queries_map or not queries_map[i]:
                queries_map[i] = [facts[i].text]

        return queries_map

    # ======================================================
    # Step 4: Tavily 多角度搜索
    # ======================================================

    def _step4_search(
        self,
        queries_map: dict[int, list[str]],
        progress: Callable[[str], None],
    ) -> dict[int, list[EvidenceItem]]:
        """每条 query 调用 Tavily，合并去重"""

        evidence_map: dict[int, list[EvidenceItem]] = {}
        seen_urls: dict[int, set[str]] = {}

        for fact_idx, queries in queries_map.items():
            evidence_map[fact_idx] = []
            seen_urls[fact_idx] = set()

            for q in queries:
                try:
                    resp = self.tavily.search(
                        query=q,
                        search_depth="advanced",
                        max_results=3,
                    )
                    for r in resp.get("results", []):
                        url = r.get("url", "")
                        if url in seen_urls[fact_idx]:
                            continue
                        seen_urls[fact_idx].add(url)
                        evidence_map[fact_idx].append(EvidenceItem(
                            title=r.get("title", ""),
                            url=url,
                            snippet=r.get("content", "")[:500],
                        ))
                except Exception as e:
                    logger.warning(f"搜索失败 [{q}]: {e}")

                time.sleep(0.8)  # 控制频率

            progress(f"  → 事实 {fact_idx+1}: 获得 {len(evidence_map[fact_idx])} 条证据")

        return evidence_map

    # ======================================================
    # Step 5: 分类型验证判断
    # ======================================================

    def _step5_verify(
        self,
        facts: list[AtomicFact],
        evidence_map: dict[int, list[EvidenceItem]],
        original_article: str,
        progress: Callable[[str], None],
    ) -> list[VerifiedFact]:

        verified: list[VerifiedFact] = []

        for i, fact in enumerate(facts):
            evidence = evidence_map.get(i, [])
            evidence_text = "\n".join(
                f"- [{e.title}]({e.url}): {e.snippet}" for e in evidence
            ) or "无搜索结果"

            progress(f"  → 验证 [{i+1}/{len(facts)}]: {fact.text[:20]}...")

            if fact.type == "data":
                result = self._verify_data(fact, evidence_text, original_article)
            else:
                result = self._verify_general(fact, evidence_text)

            evidence_urls = [e.url for e in evidence if e.url]
            verified.append(VerifiedFact(
                fact=fact.text,
                fact_type=fact.type,
                result=result["result"],
                reason=result["reason"],
                evidence_urls=evidence_urls[:3],
                suggestion=result.get("suggestion", ""),
            ))

            time.sleep(0.5)

        return verified

    def _verify_general(self, fact: AtomicFact, evidence: str) -> dict:
        """通用验证：人物/地名/时间/引用"""

        type_labels = {
            "person": "人物职务", "geo": "地名行政区划",
            "time": "时间日期", "quote": "引用出处", "other": "事实",
        }
        label = type_labels.get(fact.type, "事实")

        prompt = f"""你是一名严谨的事实核查员。请核查以下{label}的准确性。

【待核查事实】：{fact.text}
【搜索证据】：
{evidence}

## 核查要求
1. 将事实与证据逐一比对
2. 如果证据明确否定该事实 → "错误"
3. 如果证据不足以判断或有矛盾 → "存疑"
4. 如果证据支持该事实 → "通过"
5. 没有搜索结果时 → "存疑"（不能因为搜不到就判通过）

## 输出格式（严格JSON）
{{
  "result": "错误/存疑/通过",
  "reason": "一句话说明判断依据，引用具体证据",
  "suggestion": "如果有误，给出修改建议；如果通过则为空"
}}"""

        return self._call_llm_json_direct(prompt)

    def _verify_data(self, fact: AtomicFact, evidence: str, article: str) -> dict:
        """数据类验证：搜索比对 + 内部一致性检查"""

        prompt = f"""你是一名严谨的数据核查员。请核查以下数据事实的准确性。

【待核查数据】：{fact.text}
【搜索证据】：
{evidence}

【稿件全文节选（用于检查内部数据一致性）】：
{article[:2000]}

## 核查要求
1. **外部比对**：将数据与搜索证据比对，数值是否一致？
2. **内部一致性**：检查稿件内部是否存在数据矛盾
   - 例如：前文说"总投资3亿元"，后文说"投资2.8亿元"
   - 例如：各分项之和 ≠ 总数
   - 例如：百分比之和超过100%
3. 如果证据明确否定 → "错误"
4. 如果内部数据矛盾 → "错误"
5. 如果证据不足 → "存疑"
6. 如果一致 → "通过"

## 输出格式（严格JSON）
{{
  "result": "错误/存疑/通过",
  "reason": "说明判断依据。如果是内部矛盾，指出具体矛盾点",
  "suggestion": "如果有误，给出修改建议；如果通过则为空"
}}"""

        return self._call_llm_json_direct(prompt)

    # ======================================================
    # Step 6: 结构化输出
    # ======================================================

    def _step6_build_response(self, verified: list[VerifiedFact]) -> CheckResponse:

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
            f"共核查 {len(verified)} 条原子事实，"
            f"发现 {error_count} 处错误、{doubt_count} 处存疑、{pass_count} 处通过。"
        )

        # 排序：错误 > 存疑 > 通过
        order = {"错误": 0, "存疑": 1, "通过": 2}
        sorted_facts = sorted(verified, key=lambda v: order.get(v.result, 3))

        items = []
        for v in sorted_facts:
            items.append({
                "fact": v.fact,
                "type": v.fact_type,
                "result": v.result,
                "reason": v.reason,
                "evidence_urls": v.evidence_urls,
                "suggestion": v.suggestion,
            })

        return CheckResponse(
            risk_level=risk_level,
            summary=summary,
            total_facts=len(verified),
            error_count=error_count,
            doubt_count=doubt_count,
            pass_count=pass_count,
            items=items,
        )

    # ======================================================
    # LLM 调用工具方法
    # ======================================================

    def _call_llm_json(self, system_prompt: str, user_content: str) -> dict:
        """调用 LLM 返回 JSON（带 system + user 消息）"""
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
        """调用 LLM 返回 JSON（单条消息）"""
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
