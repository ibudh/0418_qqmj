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
from geo_lookup import GeoLookup

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
        self.geo = GeoLookup(config.GEO_DATA_PATH)

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
4. **geo** — 地名地点，**只提取有明确上下级关系的情形**：
   - 稿件中出现行政归属描述，如"XX省XX市XX县XX镇XX村"或"位于XX省的XX市"
   - context_hierarchy 填写斜杠分隔的完整上级链，必须包含稿件中声称的每一级，不能跳过中间层：
     正确："内蒙古自治区/呼伦贝尔市"（稿件说呼伦贝尔市）
     错误："内蒙古自治区"（跳过了市级，无法检测市级归属错误）
   - text 填最末一级地名（如是村则填村名，如是镇则填镇名）
   - 涉及村级新闻时：text=村名，context_hierarchy 填到乡镇层（村名本身不核查）
   - 若稿件中无法确定上级，输出 context_missing=true，text 填地名本身
   - 不提取孤立省名/市名（如仅出现"广东"无法验证对错）
   - 不提取自然地理名称（山、河、湖、景区等）
5. **number** — 数字数据（金额、比例、统计数据是否准确、前后是否矛盾）
6. **document** — 文件文献名称（法规、报告、规划的名称是否完整准确）

## 不提取的内容（直接跳过）

- 描述性语句、形容词、修辞
- 个人感悟、观点评论
- 过渡语（"据了解""值得注意的是"）
- 无法通过搜索引擎验证的内容
- **普通市民、村民、农民等个人**（如"村民张某""市民李女士"）的姓名或一般性描述
- **无名村庄、普通自然村**的一般性事务（如"某村修了一条路"）
- 匿名消息来源（如"知情人士表示""业内人士透露"）

## 主体级别与优先级

提取时为每条事实标注 priority（数字越小越重要）：

- **priority=1（必查）**：中央领导人、国家级机关（全国人大、国务院、中央各部委）、
  国家级政策法规、全国性统计数据、国家级重大事件
- **priority=2（重点查）**：省部级及以下各级官员、各级政府机构、政策文件、
  统计数据、知名企业/高校/机构的关键信息、市县级官员与地方性数据

优先提取 priority=1，再填充 priority=2，总数不超过 {max_facts} 条。

## 输出格式（严格JSON）
{{
  "facts": [
    {{"text": "原子事实", "type": "person", "priority": 1, "query": "精准搜索关键词"}},
    {{"text": "GDP同比增长5.2%", "type": "number", "priority": 2, "query": "2023年北京市GDP增长率 统计局"}},
    {{"text": "朝阳区", "type": "geo", "priority": 2, "context_hierarchy": "北京市", "context_missing": false, "query": "北京市朝阳区行政区划"}},
    {{"text": "张家村", "type": "geo", "priority": 2, "context_hierarchy": "湖北省/十堰市/郧阳区/茶店镇", "context_missing": false, "query": "郧阳区茶店镇行政区划"}},
    {{"text": "某地", "type": "geo", "priority": 2, "context_hierarchy": "", "context_missing": true, "query": "某地行政区划"}}
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
            priority = int(item.get("priority", 2))
            context_hierarchy = item.get("context_hierarchy", "").strip()
            context_missing = bool(item.get("context_missing", False))
            if text and fact_type in valid_types:
                facts.append({
                    "fact": AtomicFact(
                        text=text,
                        type=fact_type,
                        priority=priority,
                        context_hierarchy=context_hierarchy,
                        context_missing=context_missing,
                    ),
                    "query": query,
                })
        # priority=1 优先，同级按原始顺序
        facts.sort(key=lambda x: x["fact"].priority)
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
            fact = facts_with_queries[idx]["fact"]

            # geo 类型：先走本地区划库，命中则直接返回空证据列表（验证阶段走本地逻辑）
            if fact.type == "geo" and not fact.context_missing and fact.context_hierarchy:
                chain_levels = [p.strip() for p in fact.context_hierarchy.split("/") if p.strip()]
                is_village = len(chain_levels) >= 3
                check_chain = fact.context_hierarchy if is_village else f"{fact.context_hierarchy}/{fact.text}"
                local_result, _ = self.geo.validate_chain(check_chain)
                if local_result != "not_found":
                    # 本地可判断，返回数据库来源作为证据
                    return idx, [EvidenceItem(
                        title="国家统计局统计用区划代码（2023年）",
                        url="https://www.stats.gov.cn/sj/tjbz/tjyqhdmhcxhfdm/",
                        snippet="基于国家统计局2023年度统计用区划代码库进行本地精确匹配验证。",
                    )]

                # 本地未命中（2024年后新设等）→ 定向搜索 gov.cn
                query = f"{fact.context_hierarchy} {fact.text} 行政区划 site:gov.cn"
                items = self._tavily_search(query)
                if not items:
                    items = self._tavily_search(f"{fact.text} 撤县设区 site:gov.cn")
                return idx, items

            # geo 类型兜底：context_missing 或无上下文时，仍限定 gov.cn
            if fact.type == "geo":
                query = f"{fact.text} 行政区划 site:gov.cn"
                items = self._tavily_search(query)
                if not items:
                    items = self._tavily_search(f"{fact.text} 行政区划调整 site:gov.cn")
                return idx, items

            # 其他类型：原有逻辑
            items = self._tavily_search(query)
            if not items:
                backup_query = f"{fact.text} 最新"
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
            elif fact.type == "geo":
                result = self._verify_geo(fact, evidence_text, evidence)
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
                priority=fact.priority,
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

    def _verify_geo(
        self, fact: AtomicFact, evidence: str, evidence_items: list[EvidenceItem]
    ) -> dict:
        """地名专用验证：本地区划库优先，Tavily gov.cn 兜底"""

        # context_missing：无法判断上下级关系 → 存疑
        if fact.context_missing or not fact.context_hierarchy:
            return {
                "result": "存疑",
                "reason": f"稿件中未明确'{fact.text}'的上级行政单位，无法核实层级关系，建议人工核查",
                "suggestion": "请补全行政归属，如'XX省XX市XX区'",
            }

        # 判断是否为村级事实（context_hierarchy 含乡镇层，即有3个以上斜杠分隔层级）
        chain = fact.context_hierarchy
        chain_levels = [p.strip() for p in chain.split("/") if p.strip()] if chain else []
        is_village_fact = len(chain_levels) >= 3  # 链条含乡镇，text 是村名

        if is_village_fact:
            # 村级：只验证乡镇层级链，村名本身不核查
            local_result, reason = self.geo.validate_chain(chain)
            if local_result == "valid":
                return {
                    "result": "通过",
                    "reason": f"区划库（2023年）确认 {chain} 层级链正确；村名'{fact.text}'不做独立核查",
                    "suggestion": "",
                }
            if local_result == "invalid":
                return {"result": "错误", "reason": reason, "suggestion": "请核实行政归属"}
            # not_found：走 Tavily 兜底（下方统一处理）
        else:
            # 非村级：text 本身是待验证地名，context_hierarchy 是其上级链
            full_chain = f"{chain}/{fact.text}" if chain else fact.text
            local_result, reason = self.geo.validate_chain(full_chain)
            if local_result == "valid":
                return {
                    "result": "通过",
                    "reason": f"区划库（2023年）确认：{fact.text}属于{chain}管辖，层级正确",
                    "suggestion": "",
                }
            if local_result == "invalid":
                return {"result": "错误", "reason": reason, "suggestion": "请核实行政归属"}
            # not_found：走 Tavily 兜底

        # not_found：走 LLM + gov.cn 证据判断
        prompt = f"""你是一名严谨的新闻校对专家，专职核查行政区划准确性。

【待核查地名】：{fact.text}
【稿件中标注的上级】：{fact.context_hierarchy}
【gov.cn 搜索证据】：
{evidence}

## 核查规则（严格按优先级执行）

1. **级别错位**：若证据显示 {fact.text} 的行政层级与 {fact.context_hierarchy} 不匹配
   （如直辖市直管区被误写为某市下辖）→ 判"错误"，给出正确归属
2. **新旧交替**：若证据显示近年发生撤县设区/设市等变更
   → 判"存疑"，注明变更依据和建议改法
3. **简称/全称/别称**：广东=广东省，均视为正确 → 判"通过"
4. **证据不足**：gov.cn 无明确结论 → 判"存疑"

## 输出格式（严格JSON）
{{
  "result": "错误/存疑/通过",
  "reason": "一句话说明判断依据，引用证据中的关键信息",
  "suggestion": "如有误，给出具体修改建议；如通过则为空"
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
                "priority": v.priority,
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
