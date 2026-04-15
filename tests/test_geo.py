"""
地名检测模块评测脚本
直接调用 FactEngine，不走 HTTP，速度快。

运行方式：
  cd h:/00\ Studio/0418_qqmj
  python tests/test_geo.py

可选参数：
  --only-extract   只测 Step 1 提取，不跑完整核查（省 token/时间）
  --id geo_001     只跑指定用例
"""

from __future__ import annotations

import sys
import json
import argparse
import os

# 把项目根目录加入 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_engine import FactEngine

CASES_FILE = os.path.join(os.path.dirname(__file__), "geo_cases.json")

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

# Windows GBK 终端不支持特殊字符，改用 ASCII
PASS_ICON = "[OK]"
FAIL_ICON = "[NG]"


def load_cases(only_id: str | None = None) -> list[dict]:
    with open(CASES_FILE, encoding="utf-8") as f:
        cases = json.load(f)
    # 过滤掉注释行（只有 _comment 字段的条目）
    cases = [c for c in cases if "id" in c]
    if only_id:
        cases = [c for c in cases if c["id"] == only_id]
    return cases


def find_geo_fact(facts_with_queries: list[dict]) -> dict | None:
    """从提取结果中找到第一条 geo 类型的事实"""
    for fq in facts_with_queries:
        if fq["fact"].type == "geo":
            return fq
    return None


def check_extraction(case: dict, facts_with_queries: list[dict]) -> tuple[bool, str]:
    """检查 Step 1 提取结果是否符合预期"""
    expect_extracted = case["expect_extracted"]
    geo_fq = find_geo_fact(facts_with_queries)
    actually_extracted = geo_fq is not None

    if expect_extracted != actually_extracted:
        expected_str = "应提取" if expect_extracted else "不应提取"
        actual_str = "已提取" if actually_extracted else "未提取"
        return False, f"{expected_str} → {actual_str}"

    if not expect_extracted:
        return True, "正确：未提取（符合预期）"

    actual_ch = geo_fq["fact"].context_hierarchy

    # 优先用 expect_ch_contains（关键词包含检查，宽松）
    contains_kw = case.get("expect_ch_contains", "")
    if contains_kw:
        if contains_kw not in actual_ch:
            return False, f"context_hierarchy 缺少关键层级\n    应包含: {contains_kw}\n    实际: {actual_ch or '（空）'}"
        return True, f"提取正确，context_hierarchy={actual_ch}（含{contains_kw}）"

    # 降级：精确匹配（旧用例兼容）
    expected_ch = case.get("expect_context_hierarchy", "")
    if expected_ch and expected_ch != actual_ch:
        return False, f"context_hierarchy 不符\n    期望: {expected_ch}\n    实际: {actual_ch}"

    return True, f"提取正确，context_hierarchy={actual_ch or '（空）'}"


def check_verdict(case: dict, engine: FactEngine) -> tuple[bool, str]:
    """跑完整核查，检查最终判断"""
    expect_result = case["expect_result"]
    if expect_result == "不提取":
        return True, "跳过（不提取类用例）"

    result = engine.check(case["article"])

    # 找到 geo 类型的 item
    geo_items = [it for it in result.items if it.get("type") == "geo"]
    if not geo_items:
        if expect_result in ("未搜到", "未检索", "通过", "错误"):
            return False, f"期望判断 {expect_result}，但未找到 geo 类型条目"
        return True, "未提取，符合预期"

    # 取第一条 geo 结果（用例设计为单一事实）
    actual_result = geo_items[0]["result"]
    if actual_result == expect_result:
        reason = geo_items[0].get("reason", "")
        return True, f"判断正确：{actual_result}（{reason[:40]}...）"
    else:
        reason = geo_items[0].get("reason", "")
        return False, f"期望 {expect_result} → 实际 {actual_result}（{reason[:60]}）"


def print_row(case_id, category, desc, extract_ok, extract_msg, verdict_ok, verdict_msg):
    extract_icon = f"{GREEN}{PASS_ICON}{RESET}" if extract_ok else f"{RED}{FAIL_ICON}{RESET}"
    verdict_icon = f"{GREEN}{PASS_ICON}{RESET}" if verdict_ok else f"{RED}{FAIL_ICON}{RESET}"
    print(f"\n{BOLD}[{case_id}]{RESET} {category} — {desc}")
    print(f"  提取 {extract_icon}  {extract_msg}")
    if verdict_msg:
        print(f"  判断 {verdict_icon}  {verdict_msg}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only-extract", action="store_true", help="只测提取，不跑核查")
    parser.add_argument("--id", help="只跑指定用例ID")
    args = parser.parse_args()

    cases = load_cases(args.id)
    print(f"\n{BOLD}签前秒检 · 地名模块评测{RESET}  共 {len(cases)} 条用例\n{'─'*60}")

    engine = FactEngine()

    extract_pass = extract_fail = 0
    verdict_pass = verdict_fail = 0

    for case in cases:
        # Step 1：提取
        facts_with_queries = engine._step1_extract_and_query(case["article"], max_facts=10)
        e_ok, e_msg = check_extraction(case, facts_with_queries)
        if e_ok:
            extract_pass += 1
        else:
            extract_fail += 1

        # Step 2+3：完整核查
        v_ok, v_msg = True, ""
        if not args.only_extract and case["expect_result"] != "不提取":
            v_ok, v_msg = check_verdict(case, engine)
            if v_ok:
                verdict_pass += 1
            else:
                verdict_fail += 1

        print_row(
            case["id"], case["category"], case["description"],
            e_ok, e_msg, v_ok, v_msg,
        )

    # 汇总
    print(f"\n{'─'*60}")
    total_e = extract_pass + extract_fail
    total_v = verdict_pass + verdict_fail

    e_rate = extract_pass / total_e * 100 if total_e else 0
    v_rate = verdict_pass / total_v * 100 if total_v else 0

    e_color = GREEN if e_rate >= 80 else (YELLOW if e_rate >= 60 else RED)
    v_color = GREEN if v_rate >= 80 else (YELLOW if v_rate >= 60 else RED)

    print(f"{BOLD}提取准确率{RESET}  {extract_pass}/{total_e}  {e_color}{e_rate:.0f}%{RESET}")
    if not args.only_extract:
        print(f"{BOLD}判断准确率{RESET}  {verdict_pass}/{total_v}  {v_color}{v_rate:.0f}%{RESET}")
    print()


if __name__ == "__main__":
    main()
