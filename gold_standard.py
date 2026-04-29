# gold_standard.py
# 金标准前置核查：命中则直接判定，跳过联网搜索
from __future__ import annotations

import json
import logging
import os
import re

from schemas import AtomicFact

logger = logging.getLogger(__name__)

# 中文书名号引号（避免与 f-string 定界符冲突）
_LQ = "“"  # left double quotation mark
_RQ = "”"  # right double quotation mark

# 省份全称 -> 短名
_PROV_NORM: dict[str, str] = {
    "北京市": "北京",
    "天津市": "天津",
    "上海市": "上海",
    "重庆市": "重庆",
    "河北省": "河北",
    "山西省": "山西",
    "辽宁省": "辽宁",
    "吉林省": "吉林",
    "黑龙江省": "黑龙江",
    "江苏省": "江苏",
    "浙江省": "浙江",
    "安徽省": "安徽",
    "福建省": "福建",
    "江西省": "江西",
    "山东省": "山东",
    "河南省": "河南",
    "湖北省": "湖北",
    "湖南省": "湖南",
    "广东省": "广东",
    "海南省": "海南",
    "四川省": "四川",
    "贵州省": "贵州",
    "云南省": "云南",
    "陕西省": "陕西",
    "甘肃省": "甘肃",
    "青海省": "青海",
    "内蒙古自治区": "内蒙古",
    "广西壮族自治区": "广西",
    "西藏自治区": "西藏",
    "宁夏回族自治区": "宁夏",
    "新疆维吾尔自治区": "新疆",
}
_PROVINCES = list(set(_PROV_NORM.values()))


def _norm_prov(raw: str) -> str:
    if raw in _PROV_NORM:
        return _PROV_NORM[raw]
    for full, short in _PROV_NORM.items():
        if raw.startswith(full) or raw.startswith(short):
            return short
    return raw


def _extract_prov(text: str) -> str:
    for p in _PROVINCES:
        if p in text:
            return p
    return ""


class GoldStandard:
    """从本地 JSON 加载金标准数据，对原子事实做前置判定。"""

    def __init__(self, data_dir: str) -> None:
        self._5a:         dict[str, dict]        = {}
        self._hist_city:  dict[str, dict]        = {}
        self._heritage:   dict[str, dict]        = {}
        self._intangible: dict[str, list[dict]]  = {}
        self._village:    dict[str, list[dict]]  = {}
        self._load_all(data_dir)

    # ── 加载 ──────────────────────────────────────────────────────

    def _load_all(self, data_dir: str) -> None:
        self._load_5a(data_dir)
        self._load_hist_city(data_dir)
        self._load_heritage(data_dir)
        self._load_intangible(data_dir)
        self._load_village(data_dir)
        logger.info(
            "金标准加载: 5A景区%d | 历史文化名城%d | 工业遗产%d | 非遗%d | 传统村落索引%d",
            len(self._5a), len(self._hist_city), len(self._heritage),
            len(self._intangible), len(self._village),
        )

    def _load_5a(self, d: str) -> None:
        try:
            path = os.path.join(d, "全国5A级旅游景区.json")
            with open(path, encoding="utf-8") as f:
                for e in json.load(f)["entries"]:
                    self._5a[e["name"]] = e
        except Exception as ex:
            logger.warning("5A景区数据加载失败: %s", ex)

    def _load_hist_city(self, d: str) -> None:
        try:
            path = os.path.join(d, "中国历史文化名城.json")
            with open(path, encoding="utf-8") as f:
                for e in json.load(f)["entries"]:
                    self._hist_city[e["name"]] = e
        except Exception as ex:
            logger.warning("历史文化名城数据加载失败: %s", ex)

    def _load_heritage(self, d: str) -> None:
        try:
            path = os.path.join(d, "国家工业遗产.json")
            with open(path, encoding="utf-8") as f:
                for e in json.load(f)["heritage_list"]:
                    name_key = "名称"
                    self._heritage[e[name_key]] = e
        except Exception as ex:
            logger.warning("工业遗产数据加载失败: %s", ex)

    def _load_intangible(self, d: str) -> None:
        try:
            path = os.path.join(d, "国家级非物质文化遗产名录.json")
            with open(path, encoding="utf-8") as f:
                for e in json.load(f):
                    t = e.get("title", "")
                    if t:
                        self._intangible.setdefault(t, []).append(e)
        except Exception as ex:
            logger.warning("非遗数据加载失败: %s", ex)

    def _load_village(self, d: str) -> None:
        try:
            path = os.path.join(d, "中国传统村落.json")
            with open(path, encoding="utf-8") as f:
                for e in json.load(f)["entries"]:
                    name = e["name"]
                    self._village.setdefault(name, []).append(e)
                    short = re.split(r"街道|办事处|乡|镇", name)[-1]
                    if short and short != name and len(short) >= 3:
                        self._village.setdefault(short, []).append(e)
        except Exception as ex:
            logger.warning("传统村落数据加载失败: %s", ex)

    # ── 公开接口 ─────────────────────────────────────────────────

    def verify(self, fact: AtomicFact, article: str) -> dict | None:
        """
        对原子事实做金标准前置判定。
        返回 None  -> 无对应金标准，走正常联网核查。
        返回 dict  -> 命中，含 result/reason/suggestion/source。
        """
        text = fact.text
        if fact.context_hierarchy:
            text = fact.context_hierarchy + " " + text

        for fn in (
            self._check_5a,
            self._check_hist_city,
            self._check_heritage,
            self._check_intangible,
            self._check_village,
        ):
            result = fn(fact, text, article)
            if result is not None:
                return result
        return None

    # ── 各数据集核查 ──────────────────────────────────────────────

    def _check_5a(self, fact: AtomicFact, text: str, article: str = "") -> dict | None:
        if not any(k in text for k in ("5A级", "AAAAA", "5A景区", "5A旅游")):
            return None
        hit = self._find_one(text, self._5a)
        if not hit and article and self._PRONOUN_RE.search(fact.text):
            hit = self._find_one(article, self._5a)
        if not hit:
            return None
        name, entry = hit
        prov = entry["province"]
        gs_p = _norm_prov(prov)
        art_p = _extract_prov(text)
        if art_p and art_p != gs_p:
            return {
                "result": "错误",
                "reason": (
                    "金标准（文旅部）显示"
                    + _LQ + name + _RQ
                    + "位于" + prov
                    + "，非文中所称的" + art_p
                ),
                "suggestion": "将省份改为" + _LQ + prov + _RQ,
                "source": "全国5A级旅游景区名单（文旅部）",
            }
        year = entry.get("approval_year", "")
        return {
            "result": "通过",
            "reason": (
                "金标准确认"
                + _LQ + name + _RQ
                + "为全国5A级旅游景区，位于"
                + prov + "（" + year + "评定）"
            ),
            "suggestion": "",
            "source": "全国5A级旅游景区名单（文旅部）",
        }

    def _check_hist_city(self, fact: AtomicFact, text: str, article: str = "") -> dict | None:
        if not any(k in text for k in ("历史文化名城", "国家历史文化名城")):
            return None
        hit = self._find_one(text, self._hist_city)
        if not hit and article and self._PRONOUN_RE.search(fact.text):
            hit = self._find_one(article, self._hist_city)
        if not hit:
            return None
        name, entry = hit
        prov = entry["province"]
        gs_p = _norm_prov(prov)
        art_p = _extract_prov(text)
        if art_p and art_p != gs_p:
            return {
                "result": "错误",
                "reason": (
                    "金标准（住建部）显示历史文化名城"
                    + _LQ + name + _RQ
                    + "位于" + prov
                    + "，非文中所称的" + art_p
                ),
                "suggestion": "将省份改为" + _LQ + prov + _RQ,
                "source": "中国历史文化名城名单（住建部）",
            }
        batch = entry.get("batch_num", "")
        return {
            "result": "通过",
            "reason": (
                "金标准确认"
                + _LQ + name + _RQ
                + "为第" + str(batch) + "批中国历史文化名城，位于" + prov
            ),
            "suggestion": "",
            "source": "中国历史文化名城名单（住建部）",
        }

    def _check_heritage(self, fact: AtomicFact, text: str, article: str = "") -> dict | None:
        if not any(k in text for k in ("工业遗产", "国家工业遗产")):
            return None
        hit = self._find_one(text, self._heritage)
        if not hit and article and self._PRONOUN_RE.search(fact.text):
            hit = self._find_one(article, self._heritage)
        if not hit:
            return None
        name, entry = hit
        addr_key = "地址"
        batch_key = "批次"
        address = entry.get(addr_key, "")
        batch = entry.get(batch_key, "")
        gs_p = _extract_prov(address)
        art_p = _extract_prov(text)
        if art_p and gs_p and art_p != gs_p:
            return {
                "result": "错误",
                "reason": (
                    "金标准（工信部）显示"
                    + _LQ + name + _RQ
                    + "位于" + address
                    + "，非文中所称的" + art_p
                ),
                "suggestion": "将地址改为" + _LQ + address + _RQ,
                "source": "国家工业遗产名单（工信部）",
            }
        return {
            "result": "通过",
            "reason": (
                "金标准确认"
                + _LQ + name + _RQ
                + "为" + batch + "国家工业遗产，位于" + address
            ),
            "suggestion": "",
            "source": "国家工业遗产名单（工信部）",
        }

    # 省/区/市级非遗限定词：含这些词时不走国家级金标准
    _REGIONAL_QUALIFIERS = ("省级", "市级", "区级", "县级", "自治区级", "自治州级")

    def _check_intangible(self, fact: AtomicFact, text: str, article: str = "") -> dict | None:
        if not any(k in text for k in ("非遗", "非物质文化遗产", "国家级非遗")):
            return None
        if any(q in text for q in self._REGIONAL_QUALIFIERS):
            return None  # 省/区/市级非遗，不适用国家级金标准
        hit = self._find_multi(text, self._intangible)
        if not hit and article and self._PRONOUN_RE.search(fact.text):
            hit = self._find_multi(article, self._intangible)
        if not hit:
            return None
        title, entries = hit
        art_p = _extract_prov(text)
        if art_p:
            matched = any(
                art_p in e.get("province", "") or art_p == _norm_prov(e.get("province", ""))
                for e in entries
            )
            if not matched:
                gs_provs = "、".join(sorted({_norm_prov(e.get("province", "")) for e in entries if e.get("province")}))
                return {
                    "result": "错误",
                    "reason": (
                        "金标准（文旅部）显示国家级非遗"
                        + _LQ + title + _RQ
                        + "的申报地区不含" + art_p
                        + "，已收录地区为：" + gs_provs
                    ),
                    "suggestion": "请核实申报省份",
                    "source": "国家级非物质文化遗产名录（文旅部）",
                }
        type_str = entries[0].get("type", "")
        return {
            "result": "通过",
            "reason": (
                "金标准确认"
                + _LQ + title + _RQ
                + "已列入国家级非物质文化遗产名录（类别：" + type_str + "）"
            ),
            "suggestion": "",
            "source": "国家级非物质文化遗产名录（文旅部）",
        }

    def _check_village(self, fact: AtomicFact, text: str, article: str = "") -> dict | None:
        if not any(k in text for k in ("传统村落", "中国传统村落")):
            return None
        hit = self._find_multi(text, self._village)
        if not hit and article and self._PRONOUN_RE.search(fact.text):
            hit = self._find_multi(article, self._village)
        if not hit:
            return None
        name, entries = hit

        # 匹配名称太短（如仅"村"一字），视为噪声，跳过金标准
        if len(name) < 3:
            return None

        # ── 批次校验 ──
        _BATCH = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
        batch_m = re.search(r"第([一二三四五六\d]+)批", text)
        if batch_m:
            raw = batch_m.group(1)
            art_batch_num = _BATCH.get(raw) or (int(raw) if raw.isdigit() else None)
            if art_batch_num is not None:
                batch_entries = [e for e in entries if e.get("batch") == art_batch_num]
                if not batch_entries:
                    actual = "、".join(sorted({e.get("batch_name", "") for e in entries if e.get("batch_name")}))
                    return {
                        "result": "错误",
                        "reason": (
                            "金标准（住建部）显示中国传统村落"
                            + _LQ + name + _RQ
                            + "入选批次为" + actual
                            + "，与文中所称的第" + raw + "批不符"
                        ),
                        "suggestion": "请核实入选批次，实际为" + actual,
                        "source": "中国传统村落名单（住建部）",
                    }
                entries = batch_entries  # 缩小到匹配批次的条目

        # ── 省份校验 ──
        art_p = _extract_prov(text)
        if art_p:
            matched = any(art_p in _norm_prov(e.get("province", "")) for e in entries)
            if not matched:
                gs_provs = "、".join(sorted({_norm_prov(e.get("province", "")) for e in entries}))
                return {
                    "result": "错误",
                    "reason": (
                        "金标准（住建部）显示中国传统村落"
                        + _LQ + name + _RQ
                        + "不属于" + art_p
                        + "，所在地为：" + gs_provs
                    ),
                    "suggestion": "请核实行政归属",
                    "source": "中国传统村落名单（住建部）",
                }
        e0 = entries[0]
        return {
            "result": "通过",
            "reason": (
                "金标准确认"
                + _LQ + name + _RQ
                + "已列入" + e0.get("batch_name", "")
                + "（" + str(e0.get("year", "")) + "年）中国传统村落名单，位于"
                + e0.get("province", "") + e0.get("county", "")
            ),
            "suggestion": "",
            "source": "中国传统村落名单（住建部）",
        }

    # ── 工具方法 ──────────────────────────────────────────────────

    # 中文指代词模式：该X、此X（后接1-6字非标点内容）
    _PRONOUN_RE = re.compile(r"该.{1,6}|此.{1,6}")

    @staticmethod
    def _find_one(text: str, index: dict[str, dict]) -> tuple[str, dict] | None:
        hits = [(n, e) for n, e in index.items() if n in text]
        if not hits:
            return None
        hits.sort(key=lambda x: len(x[0]), reverse=True)
        return hits[0]

    @staticmethod
    def _find_multi(text: str, index: dict[str, list]) -> tuple[str, list] | None:
        hits = [(n, es) for n, es in index.items() if n in text]
        if not hits:
            return None
        hits.sort(key=lambda x: len(x[0]), reverse=True)
        return hits[0]
