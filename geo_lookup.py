# geo_lookup.py
# ==========================================
# 行政区划本地查询（四级：省市区/县街道/乡镇）
# 数据源：国家统计局 2023 年度区划代码
# 来源：github.com/modood/Administrative-divisions-of-China
# ==========================================

from __future__ import annotations

import json
import logging
from typing import Literal

logger = logging.getLogger(__name__)

GeoValidateResult = Literal["valid", "invalid", "not_found"]


class GeoLookup:
    """
    行政区划层级验证，支持省市区乡镇四级。

    核心规则：
    - 验证 context_hierarchy 链条（斜杠分隔，如"湖北省/十堰市/郧阳区/茶店镇"）
    - 村级事实只验证到乡镇层，村名本身不核查
    - 返回 valid / invalid / not_found 三种结果
    """

    def __init__(self, data_path: str) -> None:
        self.hierarchy_set: set[tuple[str, str]] = set()
        self.all_names: set[str] = set()
        self._parent_map: dict[str, set[str]] = {}

        self._load(data_path)
        logger.info(
            f"GeoLookup 加载完成：{len(self.hierarchy_set)} 条层级关系，"
            f"{len(self.all_names)} 个地名"
        )

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @staticmethod
    def parse_chain(hierarchy: str) -> list[str]:
        """将斜杠分隔的层级字符串解析为列表，过滤空段。"""
        return [p.strip() for p in hierarchy.split("/") if p.strip()]

    def validate_chain(self, context_hierarchy: str) -> tuple[GeoValidateResult, str]:
        if not context_hierarchy:
            return "not_found", "未提供行政层级信息"

        parts = self.parse_chain(context_hierarchy)
        if len(parts) < 2:
            # 单个地名无法验证层级关系
            return "not_found", f"'{context_hierarchy}'为单一地名，无法验证层级关系"

        for i in range(len(parts) - 1):
            parent, child = parts[i], parts[i + 1]
            result = self._validate_pair(parent, child)
            if result == "invalid":
                correct = self.get_correct_parents(child)
                correct_str = "、".join(correct) if correct else "（库中有此地名但上级不符）"
                return "invalid", (
                    f"'{child}'不属于'{parent}'管辖，"
                    f"区划库显示其实际上级为：{correct_str}"
                )
            if result == "not_found":
                if child not in self.all_names:
                    return "not_found", f"'{child}'不在2023年区划库中，可能为近年新设或库外地名"
                # parent 不在库中
                return "not_found", f"'{parent}'不在2023年区划库中"

        return "valid", ""

    def get_correct_parents(self, child: str) -> list[str]:
        """返回 child 的实际上级列表（用于错误提示）。"""
        return sorted(self._parent_map.get(child.strip(), set()))

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _validate_pair(self, parent: str, child: str) -> GeoValidateResult:
        parent, child = parent.strip(), child.strip()
        if not parent or not child:
            return "not_found"
        if (parent, child) in self.hierarchy_set:
            return "valid"
        if child in self.all_names:
            return "invalid"
        return "not_found"

    def _load(self, data_path: str) -> None:
        try:
            with open(data_path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            logger.warning(f"区划数据文件未找到：{data_path}，地名本地查询不可用")
            return
        except json.JSONDecodeError as e:
            logger.error(f"区划数据解析失败：{e}")
            return

        for province in data:
            prov_name = province.get("name", "")
            self.all_names.add(prov_name)

            for city_node in province.get("children", []):
                city_name = city_node.get("name", "")
                is_intermediate = city_name in ("市辖区", "县", "林区")

                if not is_intermediate:
                    self.all_names.add(city_name)
                    self._add(prov_name, city_name)

                for district_node in city_node.get("children", []):
                    dist_name = district_node.get("name", "")
                    self.all_names.add(dist_name)
                    self._add(prov_name, dist_name)
                    if not is_intermediate:
                        self._add(city_name, dist_name)

                    for township_node in district_node.get("children", []):
                        town_name = township_node.get("name", "")
                        self.all_names.add(town_name)
                        self._add(dist_name, town_name)
                        if not is_intermediate:
                            self._add(city_name, town_name)

    def _add(self, parent: str, child: str) -> None:
        self.hierarchy_set.add((parent, child))
        self._parent_map.setdefault(child, set()).add(parent)
