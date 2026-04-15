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

# ── 站点名映射 ──
_SITE_NAMES: dict[str, str] = {
    "people.com.cn": "人民网",
    "xinhuanet.com": "新华网",
    "xinhua.org": "新华社",
    "cctv.com": "央视网",
    "cctvnews.cctv.com": "央视新闻",
    "gmw.cn": "光明网",
    "cnr.cn": "央广网",
    "china.com.cn": "中国网",
    "chinadaily.com.cn": "中国日报",
    "ce.cn": "中国经济网",
    "youth.cn": "中国青年网",
    "stats.gov.cn": "国家统计局",
    "moe.gov.cn": "教育部",
    "ndrc.gov.cn": "国家发改委",
    "mfa.gov.cn": "外交部",
    "mod.gov.cn": "国防部",
    "nhc.gov.cn": "国家卫健委",
    "samr.gov.cn": "国家市场监管总局",
    "miit.gov.cn": "工业和信息化部",
    "mofcom.gov.cn": "商务部",
    "mof.gov.cn": "财政部",
    "pbc.gov.cn": "中国人民银行",
    "court.gov.cn": "最高人民法院",
    "spp.gov.cn": "最高人民检察院",
    "npc.gov.cn": "全国人大",
    "cppcc.gov.cn": "全国政协",
    "baidu.com": "百度百科",
    "wikipedia.org": "维基百科",
}


def _get_site_name(url: str) -> str:
    """从 URL 提取可读站点名"""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip("www.")
        # 精确匹配
        if host in _SITE_NAMES:
            return _SITE_NAMES[host]
        # 子域名匹配（如 news.people.com.cn → 人民网）
        for domain, name in _SITE_NAMES.items():
            if host.endswith("." + domain):
                return name
        # gov.cn 兜底：提取二级域名作为机构名
        if host.endswith(".gov.cn"):
            parts = host.split(".")
            if len(parts) >= 3:
                return f"{parts[-3].upper()} 政府网"
            return "政府官网"
        # 其他：取主域名
        parts = host.split(".")
        return parts[-2] if len(parts) >= 2 else host
    except Exception:
        return ""


# 根据稿件长度决定最大提取条数
MAX_FACTS_SHORT = 5    # <500字
MAX_FACTS_MEDIUM = 8   # 500-2000字
MAX_FACTS_LONG = 10    # >2000字

# 地名核查权威信源：国家统计局区划代码（本地库的上游数据源）
GEO_AUTHORITY_SOURCE = EvidenceItem(
    title="国家统计局·统计用区划代码和城乡划分代码",
    url="https://www.stats.gov.cn/sj/tjbz/tjyqhdmhcxhfdm/2023/",
    snippet="国家统计局发布的最新年度县级以上行政区划代码（每年更新）",
    source_name="国家统计局",
)


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
                total_facts=0, error_count=0, pass_count=0,
                no_result_count=0, not_searched_count=0,
            )
        facts = [fq["fact"] for fq in facts_with_queries]
        _p(f"  → 提取到 {len(facts)} 条硬事实")

        # Step 2: 并行搜索证据（搜不到自动补搜一次）
        _p("Step 2/3 · 正在并行联网搜索证据...")
        t1 = time.time()
        evidence_map = self._step2_parallel_search(facts_with_queries, _p)
        t2_cost = round(time.time() - t1, 1)
        evidence_found = sum(1 for v in evidence_map.values() if v)

        # 构建 number_context 映射（仅 number 类型有）
        number_context_map = {
            i: fq["number_context"]
            for i, fq in enumerate(facts_with_queries)
            if "number_context" in fq
        }

        # Step 3: 并行验证
        _p("Step 3/3 · 正在并行验证...")
        t2 = time.time()
        verified = self._step3_parallel_verify(
            facts, evidence_map, article, _p, number_context_map,
        )
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

        # 在 items 里加入搜索词、证据数量和来源层级
        items_extra = []
        for i, fq in enumerate(facts_with_queries):
            evidence_items = evidence_map.get(i, [])
            items_extra.append({
                "query_used": fq["query"],
                "evidence_found": len(evidence_items),
                "source_tier": self._classify_source_tier(evidence_items),
            })

        return self._build_response(verified, pipeline, items_extra)

    # ======================================================
    # Step 1: 提取硬事实 + 搜索词（一次LLM调用）
    # ======================================================

    def _step1_extract_and_query(
        self, article: str, max_facts: int,
    ) -> list[dict]:

        prompt = f"""你是一名资深新闻校对专家。请从稿件中提取需要核查的"硬事实"。

## 原子分解原则

一条事实中如果同时包含多个独立可核查点，必须拆成多条，每条只包含一个可核查断言。
如果只有一个核查点，不要强行拆分。

示例：
原句："2010年，炕围画被列入第二批内蒙古自治区级非物质文化遗产名录。"
应拆为 3 条：
- "炕围画列入非遗名录的时间为2010年"（type=time，核查时间是否正确）
- "炕围画属于内蒙古自治区级非遗"（type=regulation，核查归属省份是否正确）
- "炕围画列入的是第二批非遗名录"（type=regulation，核查批次是否正确）

不拆的情况：
- "北京市副市长张伟" → 只有一个核查点（张伟是否是北京市副市长），不拆

## 只提取以下 11 类（其他一律跳过）

1. **person** — 人物姓名（是否存在、是否写错）
   - 含身份信息（如人大代表、政协委员）
   - 外国人名翻译是否准确
2. **title** — 职务头衔（是否准确、是否过时）
   - 领导人排序是否正确
   - 必须从上下文中提取该职务对应的时间，填入 time_context
   - 例如"2015年，时任副市长张伟" → time_context="2015年"
   - 例如"原市长李明" → time_context="往届/前任"
   - 如果是现任且无特定时间标记 → time_context 留空
3. **time** — 时间日期（是否正确、是否矛盾）
   - 重要事件时间
   - 朝代、帝号纪年与公元纪年的换算
   - 人物生卒年、名作名篇年代
4. **geo** — 地名地点，**只提取有明确上下级关系的行政区划**：
   - 稿件中出现行政归属描述，如"XX省XX市XX县XX镇XX村"或"位于XX省的XX市"
   - context_hierarchy 填写斜杠分隔的完整上级链，必须包含稿件中声称的每一级，不能跳过中间层：
     正确："内蒙古自治区/呼伦贝尔市"（稿件说呼伦贝尔市）
     错误："内蒙古自治区"（跳过了市级，无法检测市级归属错误）
   - text 填最末一级地名（如是村则填村名，如是镇则填镇名）
   - 涉及村级新闻时：text=村名，context_hierarchy 填到乡镇层（村名本身不核查）
   - 若稿件中无法确定上级，输出 context_missing=true，text 填地名本身
   - 不提取孤立省名/市名（如仅出现"广东"无法验证对错）
   - 不提取自然地理名称（山、河、湖、景区等）
   - **【关键】逐句忠实提取，严禁跨段修正**：
     context_hierarchy 必须严格按照该句原文填写，绝不能用标题或其他段落的信息替换正文中的表述。
     例如标题写"呼和浩特土默特左旗"，正文写"呼伦贝尔市土默特左旗"，
     正文这条必须提取为 context_hierarchy="呼伦贝尔市"，不能偷换成"呼和浩特市"。
   - **稿件内部地名矛盾检测**：如果同一地名在稿件不同位置出现了不同的上级归属（如标题写A市、正文写B市），
     必须将每处表述都作为独立的 geo 事实提取，各自保留原文的 context_hierarchy。系统会自动交叉核查。
5. **number** — 数字数据（金额、比例、统计数据是否准确、前后是否矛盾）
   - 注意单位是否正确（公斤/千克、亿元/万元）
   - 必须从上下文中提取该数据对应的时间，填入 time_context
   - 例如"2023年GDP同比增长5.2%" → time_context="2023年"
   - 如果无明确时间 → time_context 留空
   - **结构化数据提取**：必须提取与该数据相关的所有数值及其数学关系：
     - related_numbers：稿件中与该数据相关联的所有数值（含总量、分项、占比、基数等），
       每个元素为 {{"label": "描述", "value": 数值, "unit": "单位"}}
       注意：value 必须统一为相同单位的数值。如"3.5亿元"应写为 {{"label": "总投资", "value": 35000, "unit": "万元"}}
       或 {{"label": "总投资", "value": 3.5, "unit": "亿元"}}，但同组数据必须用同一单位
     - math_relations：数值之间的数学关系，支持以下类型：
       - {{"type": "sum_check", "total_label": "总量标签", "part_labels": ["分项1标签", "分项2标签"]}}
       - {{"type": "percent_sum", "part_labels": ["占比1标签", "占比2标签"], "max_sum": 100}}
     - 如果该数据没有关联数据（孤立数据点），related_numbers 和 math_relations 为空数组
6. **regulation** — 法律法规、政策文件（名称是否完整准确、条文序号和内容是否正确）
7. **organization** — 重要机构名称、重要会议名称、重要名词/提法/概念
8. **literary** — 名人名作名篇（书名、作者、出处是否正确）
9. **poem** — 诗文内容（诗词原文是否准确、作者和出处是否正确）
   - 必须逐字核对原文，任何一字之差都应标记
10. **quotation** — 引语引文（马恩列毛邓等著作引语、名言警句、俗语熟语是否准确）
11. **media** — 歌曲名/歌词、影视剧名、重要节目栏目名

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
  国家级政策法规、全国性统计数据、国家级重大事件、国际组织、外国领导人姓名及翻译
- **priority=2（重点查）**：省部级官员及机构、省级政策文件、省级统计数据、
  知名企业/高校/机构的关键信息
- **priority=3（补充查）**：市县级官员与地方性数据、乡镇及以下事务

优先提取 priority=1，再填充 priority=2，最后补充 priority=3，总数不超过 {max_facts} 条。

## 时间上下文（time_context）

对 person、title、number、regulation、organization、literary 类型，必须从稿件上下文中提取事实对应的时间信息：
- 明确时间：如"2015年""2023年第三季度""任期2018-2022" → 填入具体时间
- 历史/前任标记：如"原市长""时任""曾任" → 填"往届/前任"
- 当前/无时间标记：留空（默认按当前时间核查）

## 输出格式（严格JSON）
{{
  "facts": [
    {{"text": "原子事实", "type": "person", "priority": 1, "time_context": "", "query": "精准搜索关键词"}},
    {{"text": "时任副市长张伟", "type": "title", "priority": 1, "time_context": "2015年", "query": "2015年 北京市副市长 张伟"}},
    {{"text": "总投资800万元，其中一期500万元，二期400万元", "type": "number", "priority": 2, "time_context": "2024年",
      "related_numbers": [
        {{"label": "总投资", "value": 800, "unit": "万元"}},
        {{"label": "一期投资", "value": 500, "unit": "万元"}},
        {{"label": "二期投资", "value": 400, "unit": "万元"}}
      ],
      "math_relations": [{{"type": "sum_check", "total_label": "总投资", "part_labels": ["一期投资", "二期投资"]}}],
      "query": "2024年 该项目投资总额"}},
    {{"text": "GDP同比增长5.2%", "type": "number", "priority": 2, "time_context": "2023年",
      "related_numbers": [], "math_relations": [],
      "query": "2023年北京市GDP增长率 统计局"}},
    {{"text": "朝阳区", "type": "geo", "priority": 2, "context_hierarchy": "北京市", "context_missing": false, "query": "北京市朝阳区行政区划"}},
    {{"text": "张家村", "type": "geo", "priority": 2, "context_hierarchy": "湖北省/十堰市/郧阳区/茶店镇", "context_missing": false, "query": "郧阳区茶店镇行政区划"}},
    {{"text": "某地", "type": "geo", "priority": 2, "context_hierarchy": "", "context_missing": true, "query": "某地行政区划"}},
    {{"text": "《中华人民共和国民法典》第184条", "type": "regulation", "priority": 1, "time_context": "", "query": "民法典第184条内容"}},
    {{"text": "床前明月光，疑是地上霜", "type": "poem", "priority": 2, "time_context": "", "query": "李白 静夜思 原文"}}
  ]
}}"""

        result = self._call_llm_json(prompt, article[:4000])
        raw = result.get("facts", [])

        valid_types = {
            "person", "title", "time", "geo", "number",
            "regulation", "organization", "literary", "poem", "quotation", "media",
        }
        facts: list[dict] = []
        for item in raw:
            text = item.get("text", "").strip()
            fact_type = item.get("type", "")
            query = item.get("query", text)
            priority = int(item.get("priority", 2))
            context_hierarchy = item.get("context_hierarchy", "").strip()
            context_missing = bool(item.get("context_missing", False))
            time_context = item.get("time_context", "").strip()
            if text and fact_type in valid_types:
                entry: dict = {
                    "fact": AtomicFact(
                        text=text,
                        type=fact_type,
                        priority=priority,
                        context_hierarchy=context_hierarchy,
                        context_missing=context_missing,
                        time_context=time_context,
                    ),
                    "query": query,
                }
                # number 类型：保留结构化数据用于 Python 算术验证
                if fact_type == "number":
                    entry["number_context"] = {
                        "related_numbers": item.get("related_numbers", []),
                        "math_relations": item.get("math_relations", []),
                    }
                facts.append(entry)
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

        # 信源白名单（include_domains 参数比 site: 字符串操作符更可靠）
        GOV_DOMAINS = ["gov.cn"]
        MEDIA_DOMAINS = [
            "people.com.cn", "xinhuanet.com", "gmw.cn",
            "cctv.com", "cnr.cn", "ce.cn", "china.com.cn", "chinadaily.com.cn",
        ]

        def search_one(idx: int, query: str) -> tuple[int, list[EvidenceItem]]:
            fact = facts_with_queries[idx]["fact"]

            # geo 类型：先走本地区划库，命中则直接返回空证据列表（验证阶段走本地逻辑）
            if fact.type == "geo" and not fact.context_missing and fact.context_hierarchy:
                is_village = len(self.geo.parse_chain(fact.context_hierarchy)) >= 3
                check_chain = fact.context_hierarchy if is_village else f"{fact.context_hierarchy}/{fact.text}"
                local_result, _ = self.geo.validate_chain(check_chain)

                # 判错时不带稿件的（可能错误的）层级，只用地名本身搜索
                # 判对/未定时才带省份增强相关性
                if local_result == "invalid":
                    geo_query = f"{fact.text} 行政区划 隶属"
                else:
                    chain_parts = self.geo.parse_chain(fact.context_hierarchy)
                    province = chain_parts[0] if chain_parts else ""
                    geo_query = f"{province} {fact.text} 行政区划".strip()

                if local_result != "not_found":
                    # 本地可判断：国家统计局权威源打头，Tavily 补齐
                    items: list[EvidenceItem] = [GEO_AUTHORITY_SOURCE]
                    items += self._tavily_search(geo_query, include_domains=GOV_DOMAINS)
                    if len(items) < 3:
                        items += self._tavily_search(geo_query, include_domains=MEDIA_DOMAINS)
                    return idx, items[:3]

                # 本地未命中（新设区划等）→ gov.cn 多查询 + 央媒兜底
                items = self._tavily_search(geo_query, include_domains=GOV_DOMAINS)
                if len(items) < 3:
                    items += self._tavily_search(
                        f"{fact.text} 撤县设区 设市", include_domains=GOV_DOMAINS,
                    )
                if len(items) < 3:
                    items += self._tavily_search(geo_query, include_domains=MEDIA_DOMAINS)
                return idx, items[:3]

            # geo 类型兜底：context_missing 或无上下文时，仍限定权威信源
            if fact.type == "geo":
                items = self._tavily_search(
                    f"{fact.text} 行政区划", include_domains=GOV_DOMAINS,
                )
                if len(items) < 3:
                    items += self._tavily_search(
                        f"{fact.text} 行政区划", include_domains=MEDIA_DOMAINS,
                    )
                return idx, items[:3]

            # number 类型：严格限定信源，不够3条则补搜央媒
            if fact.type == "number":
                base_query = query
                if fact.time_context and fact.time_context not in base_query:
                    base_query = f"{fact.time_context} {base_query}"
                items = self._tavily_search(base_query, include_domains=GOV_DOMAINS)
                if len(items) < 3:
                    items += self._tavily_search(base_query, include_domains=MEDIA_DOMAINS)
                return idx, items[:3]

            # 其他类型：严格限定信源，不够3条则补搜央媒
            if fact.time_context and fact.time_context not in query:
                query = f"{fact.time_context} {query}"
            items = self._tavily_search(query, include_domains=GOV_DOMAINS)
            if len(items) < 3:
                items += self._tavily_search(query, include_domains=MEDIA_DOMAINS)
            return idx, items[:3]

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

    def _tavily_search(
        self,
        query: str,
        include_domains: list[str] | None = None,
        max_results: int = 5,
    ) -> list[EvidenceItem]:
        """单次 Tavily 搜索。使用 include_domains 参数限定信源，比 site: 操作符更可靠。"""
        try:
            kwargs = {
                "query": query,
                "search_depth": "advanced",
                "max_results": max_results,
            }
            if include_domains:
                kwargs["include_domains"] = include_domains
            resp = self.tavily.search(**kwargs)
            return [
                EvidenceItem(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", "")[:500],
                    source_name=_get_site_name(r.get("url", "")),
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
        number_context_map: dict[int, dict] | None = None,
    ) -> list[VerifiedFact]:

        nc_map = number_context_map or {}
        results: dict[int, VerifiedFact] = {}

        def verify_one(idx: int) -> tuple[int, VerifiedFact]:
            fact = facts[idx]
            evidence = evidence_map.get(idx, [])
            evidence_text = "\n".join(
                f"- 【{e.source_name or '未知来源'}】{e.title}（{e.url}）: {e.snippet}"
                for e in evidence
            ) or "无搜索结果"

            if fact.type == "number":
                result = self._verify_number(
                    fact, evidence_text, article, nc_map.get(idx),
                )
            elif fact.type == "geo":
                result = self._verify_geo(fact, evidence_text, evidence, article)
            else:
                result = self._verify_general(fact, evidence_text)

            evidence_urls = [e.url for e in evidence if e.url]
            sources = [
                {"name": e.source_name or e.url, "url": e.url}
                for e in evidence if e.url
            ]
            return idx, VerifiedFact(
                fact=fact.text,
                fact_type=fact.type,
                result=result["result"],
                reason=result["reason"],
                evidence_urls=evidence_urls[:3],
                sources=sources[:3],
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
            "regulation": "法律法规",
            "organization": "机构/会议/名词",
            "literary": "名人名作",
            "poem": "诗文内容",
            "quotation": "引语/名言",
            "media": "影视/歌曲/栏目",
        }
        label = type_labels.get(fact.type, "事实")

        # 构建时间锚点提示
        time_hint = ""
        time_rule = ""
        if fact.time_context:
            time_hint = f"\n【时间上下文】：{fact.time_context}"
            time_rule = (
                f'\n0. **时间维度优先**：该事实的时间上下文为"{fact.time_context}"，'
                f"核查时必须以该时间点的信息为准。"
                f"如果证据显示的是其他时间段的信息（如现任 vs 当时），不构成否定依据。"
                f'例如：稿件写"2015年时任副市长张伟"，即使张伟现在不是副市长也不算错误。'
            )

        prompt = f"""你是一名严谨的事实核查员。请核查以下{label}的准确性。

【待核查事实】：{fact.text}{time_hint}
【搜索证据】：
{evidence}

## 核查要求{time_rule}
1. 将事实与证据逐一比对
2. **证据与稿件陈述冲突 → "错误"**
   - 即使证据没有直接否定，但关键要素（主体/时间/地点/级别）不一致即判错
   - 例如：稿件说"X被列入内蒙古自治区级非遗"，证据显示"X是山西省非遗" → 判错误
   - 例如：稿件说"2010年列入"，证据显示"2008年列入" → 判错误
   - 不要以"证据未直接提及本稿件的说法"为由回避判断
3. 证据明确支持 → "通过"
4. gov.cn/央媒均无相关结果 → "未搜到"

## 输出格式（严格JSON）
{{
  "result": "错误/通过/未搜到",
  "reason": "一句话说明判断依据，引用具体证据（指出冲突点）",
  "suggestion": "如果有误，给出修改建议；如果通过则为空"
}}"""

        return self._call_llm_json_direct(prompt)

    def _verify_number(
        self,
        fact: AtomicFact,
        evidence: str,
        article: str,
        number_context: dict | None = None,
    ) -> dict:
        """数字数据验证：Python 算术校验（确定性） + LLM 外部比对（语义）"""

        # ── L2: Python 算术校验（零 token，100% 准确） ──
        math_error = self._check_number_consistency(number_context)
        if math_error:
            # 确定性错误，直接返回，无需消耗 LLM 调用
            return {
                "result": "错误",
                "reason": f"稿件内部数据矛盾（算术校验）：{math_error}",
                "suggestion": "请核实各项数据，确保总量与分项一致",
            }

        # ── L3: LLM 外部比对 + 语义分析 ──
        time_hint = ""
        if fact.time_context:
            time_hint = f"\n【时间上下文】：{fact.time_context}（核查时以该时间点的数据为准）"

        # 如果有结构化数据但算术通过，告知 LLM 内部一致性已通过
        math_note = ""
        if number_context and number_context.get("math_relations"):
            math_note = "\n\n注意：该数据的内部算术一致性已通过 Python 校验（总分校验/百分比求和均正确），你只需关注外部证据比对。"

        prompt = f"""你是一名严谨的数据核查员。请核查以下数字数据的准确性。

【待核查数据】：{fact.text}{time_hint}
【搜索证据】：
{evidence}

【稿件全文节选（用于检查内部数据一致性）】：
{article[:2000]}{math_note}

## 核查要求
1. **外部比对**：数值与搜索证据是否一致？
2. **内部一致性**：稿件内部是否存在数据矛盾？
   - 例如：前文说"总投资3亿元"，后文说"投资2.8亿元"
   - 例如：各分项之和 ≠ 总数
   - 例如：百分比之和超过100%
3. **证据与稿件数据冲突 或 内部矛盾 → "错误"**
   - 即使数量级/口径略有不同，只要关键数字不一致即判错
   - 例如：稿件"同比增长5.2%"，证据"同比增长4.8%" → 判错误
4. 数据与权威证据一致 → "通过"
5. gov.cn/统计局均无相关数据 → "未搜到"

## 输出格式（严格JSON）
{{
  "result": "错误/通过/未搜到",
  "reason": "说明判断依据，内部矛盾需指出矛盾点",
  "suggestion": "如果有误，给出修改建议；如果通过则为空"
}}"""

        return self._call_llm_json_direct(prompt)

    def _verify_geo(
        self,
        fact: AtomicFact,
        evidence: str,
        evidence_items: list[EvidenceItem],
        article: str,
    ) -> dict:
        """地名专用验证：本地区划库优先，Tavily gov.cn 兜底，稿件内部一致性检查"""

        # context_missing：无法判断上下级关系 → 未搜到
        if fact.context_missing or not fact.context_hierarchy:
            return {
                "result": "未搜到",
                "reason": f"稿件中未明确'{fact.text}'的上级行政单位，无法核实层级关系，建议人工核查",
                "suggestion": "请补全行政归属，如'XX省XX市XX区'",
            }

        chain = fact.context_hierarchy
        is_village_fact = len(self.geo.parse_chain(chain)) >= 3
        check_chain = chain if is_village_fact else f"{chain}/{fact.text}"
        village_note = f"；村名'{fact.text}'不做独立核查" if is_village_fact else ""

        local_result, reason = self.geo.validate_chain(check_chain)

        # 无论本地库结果如何，都检查稿件内部地名一致性
        consistency_issue = self._check_geo_consistency(fact, article)

        if local_result == "valid":
            if consistency_issue:
                return {
                    "result": "错误",
                    "reason": f"国家统计局区划库确认 {check_chain} 层级正确，但稿件内部存在矛盾：{consistency_issue}",
                }
            return {
                "result": "通过",
                "reason": f"国家统计局区划库确认 {check_chain} 层级正确{village_note}",
            }
        if local_result == "invalid":
            extra = f"；此外稿件内部也存在矛盾：{consistency_issue}" if consistency_issue else ""
            return {"result": "错误", "reason": f"{reason}{extra}"}

        # not_found：走 LLM + gov.cn 证据判断
        consistency_hint = ""
        if consistency_issue:
            consistency_hint = f"\n【稿件内部矛盾】：{consistency_issue}\n"

        prompt = f"""你是一名严谨的新闻校对专家，专职核查行政区划准确性。

【待核查地名】：{fact.text}
【稿件中标注的上级】：{fact.context_hierarchy}{consistency_hint}
【gov.cn 搜索证据】：
{evidence}

## 核查规则（严格按优先级执行）

1. **稿件内部矛盾**：若稿件中同一地名在不同位置出现了不同的上级归属
   （如标题写A市、正文写B市）→ 判"错误"，指出矛盾并给出正确归属
2. **级别错位**：若证据显示 {fact.text} 的行政层级与 {fact.context_hierarchy} 不匹配
   （如直辖市直管区被误写为某市下辖）→ 判"错误"，给出正确归属
3. **新旧交替**：若证据显示近年发生撤县设区/设市等变更、且稿件仍用已撤销的旧名
   → 判"错误"，给出正确新名
4. **简称/全称/别称**：广东=广东省，均视为正确 → 判"通过"
5. **证据不足**：gov.cn 无明确结论 → 判"未搜到"

## 输出格式（严格JSON）
{{
  "result": "错误/通过/未搜到",
  "reason": "一句话说明判断依据，引用证据中的关键信息",
  "suggestion": "如有误，给出具体修改建议；如通过则为空"
}}"""

        return self._call_llm_json_direct(prompt)

    # ======================================================
    # 信源层级判定
    # ======================================================

    @staticmethod
    def _classify_source_tier(evidence_items: list[EvidenceItem]) -> str:
        """根据证据 URL 判定信源层级。

        返回值：
        - "官方" — 政府网站 / 统计局
        - "央媒" — 人民网 / 新华网
        - "其他" — 其他来源
        - ""     — 无证据
        """
        if not evidence_items:
            return ""

        gov_domains = ("gov.cn", "stats.gov.cn")
        media_domains = ("people.com.cn", "xinhuanet.com")

        has_gov = False
        has_media = False
        for e in evidence_items:
            url = e.url.lower()
            if any(d in url for d in gov_domains):
                has_gov = True
            elif any(d in url for d in media_domains):
                has_media = True

        if has_gov:
            return "官方"
        if has_media:
            return "央媒"
        if evidence_items:
            return "其他"
        return ""

    # ======================================================
    # 数据算术验证（Python 硬算，不依赖 LLM）
    # ======================================================

    @staticmethod
    def _check_number_consistency(number_context: dict | None) -> str:
        """用 Python 做纯算术校验，返回错误描述或空字符串。

        支持的校验类型：
        - sum_check: 分项之和 == 总量
        - percent_sum: 百分比之和 <= max_sum（默认100）
        """
        if not number_context:
            return ""

        related = number_context.get("related_numbers", [])
        relations = number_context.get("math_relations", [])
        if not related or not relations:
            return ""

        # 构建 label → value 映射
        label_map: dict[str, float] = {}
        for item in related:
            label = item.get("label", "")
            value = item.get("value")
            if label and value is not None:
                try:
                    label_map[label] = float(value)
                except (ValueError, TypeError):
                    continue

        if not label_map:
            return ""

        errors: list[str] = []

        for rel in relations:
            rel_type = rel.get("type", "")

            if rel_type == "sum_check":
                total_label = rel.get("total_label", "")
                part_labels = rel.get("part_labels", [])
                if total_label not in label_map:
                    continue
                part_values = [
                    label_map[p] for p in part_labels if p in label_map
                ]
                if not part_values or len(part_values) != len(part_labels):
                    continue  # 数据不完整，跳过
                total_val = label_map[total_label]
                parts_sum = sum(part_values)
                if abs(parts_sum - total_val) > 0.01:
                    parts_desc = " + ".join(
                        f"{p}({label_map[p]})" for p in part_labels
                    )
                    errors.append(
                        f"{parts_desc} = {parts_sum}，"
                        f"但{total_label}为{total_val}，差额{abs(parts_sum - total_val)}"
                    )

            elif rel_type == "percent_sum":
                part_labels = rel.get("part_labels", [])
                max_sum = float(rel.get("max_sum", 100))
                part_values = [
                    label_map[p] for p in part_labels if p in label_map
                ]
                if not part_values or len(part_values) != len(part_labels):
                    continue
                pct_sum = sum(part_values)
                if pct_sum > max_sum + 0.01:
                    parts_desc = " + ".join(
                        f"{p}({label_map[p]}%)" for p in part_labels
                    )
                    errors.append(
                        f"百分比之和 {parts_desc} = {pct_sum}%，超过{max_sum}%"
                    )

        return "；".join(errors)

    def _check_geo_consistency(self, fact: AtomicFact, article: str) -> str:
        """扫描稿件原文，检查同一地名前是否出现了与区划库矛盾的上级地名。

        核心思路：在稿件中定位 child 的每一次出现，提取其前方的短文本窗口，
        检查窗口中是否包含已知地名但该地名并非 child 的合法上级。
        这能捕获 LLM 提取时"用标题修正正文"导致错误被吞掉的情况。
        """
        chain_parts = self.geo.parse_chain(fact.context_hierarchy)
        if not chain_parts:
            return ""

        # 构建需要检查的 (stated_parent, child) 对
        pairs_to_check: list[tuple[str, str]] = []
        for i in range(len(chain_parts) - 1):
            pairs_to_check.append((chain_parts[i], chain_parts[i + 1]))

        for stated_parent, child in pairs_to_check:
            correct_parents = self.geo.get_correct_parents(child)
            if not correct_parents:
                continue

            # 在稿件中找到 child 的每一次出现
            search_start = 0
            while True:
                pos = article.find(child, search_start)
                if pos == -1:
                    break
                # 取 child 前方最多 15 个字符作为检测窗口
                prefix = article[max(0, pos - 15):pos]
                # 在窗口中查找已知地名（≥2字符），检查是否为非法上级
                for name in self.geo.all_names:
                    if len(name) >= 2 and name in prefix and name != child:
                        if name not in correct_parents:
                            return (
                                f"稿件中'{child}'前出现了'{name}'，"
                                f"但区划库显示'{child}'实际属于"
                                f"'{'、'.join(sorted(correct_parents))}'，"
                                f"而非'{name}'"
                            )
                search_start = pos + 1

        return ""

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
        pass_count = sum(1 for v in verified if v.result == "通过")
        no_result_count = sum(1 for v in verified if v.result == "未搜到")
        not_searched_count = sum(1 for v in verified if v.result == "未检索")

        if error_count > 0:
            risk_level = "高危"
        else:
            risk_level = "通过"

        summary = (
            f"共核查 {len(verified)} 条硬事实，"
            f"发现 {error_count} 处错误、{pass_count} 处通过、"
            f"{no_result_count} 处未搜到、{not_searched_count} 处未检索。"
            f"\n\n—— 签前秒检 · rmrbtzk-v3 引擎"
        )

        # 按风险排序，保留原始索引以匹配 items_extra
        order = {"错误": 0, "未搜到": 1, "未检索": 2, "通过": 3}
        indexed = sorted(enumerate(verified), key=lambda x: order.get(x[1].result, 3))

        items = [
            {
                "fact": v.fact,
                "type": v.fact_type,
                "priority": v.priority,
                "result": v.result,
                "reason": v.reason,
                "evidence_urls": v.evidence_urls,
                "sources": v.sources,
                "suggestion": v.suggestion,
                "query_used": items_extra[i]["query_used"],
                "evidence_found": items_extra[i]["evidence_found"],
                "source_tier": items_extra[i]["source_tier"],
            }
            for i, v in indexed
        ]

        return CheckResponse(
            risk_level=risk_level,
            summary=summary,
            total_facts=len(verified),
            error_count=error_count,
            pass_count=pass_count,
            no_result_count=no_result_count,
            not_searched_count=not_searched_count,
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
            return {"result": "未搜到", "reason": f"搜索接口异常: {e}", "suggestion": ""}
