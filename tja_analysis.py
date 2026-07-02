#!/usr/bin/env python3
"""
TJA 谱面分析模块
================

封装 API 调用、数据提取、原始定数计算。
返回的 chart dict 可直接传入 rating.RatingPipeline 计算最终定数。

用法:
    from tja_analysis import TJAChartAnalyzer

    analyzer = TJAChartAnalyzer()
    raw_analysis = analyzer.analyze(tja_content, unit="ms")
    charts = analyzer.process(raw_analysis)
    # charts[i]["ratings"] → {stamina, speed, burst, complex, complexRatio, ...}
"""

from __future__ import annotations

import json
import os
import sys
import importlib
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import algorithms
# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DEFAULT_API_URL = "https://tja-analysis.ourtaiko.org"

# ---------------------------------------------------------------------------
# 算法模块 — 通过 Unicode 码点导入中文文件名
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ALGORITHMS_DIR = os.path.join(_SCRIPT_DIR, "algorithms")
if _ALGORITHMS_DIR not in sys.path:
    sys.path.insert(0, _ALGORITHMS_DIR)

_roll = importlib.import_module("\u6eda\u594f\u7b49\u6548")   # 滚奏等效


# ===========================================================================
# TJA 谱面分析器
# ===========================================================================


class TJAChartAnalyzer:
    """封装 TJA 谱面分析全流程：API 调用 → 数据提取 → 原始定数计算。

    每个谱面分支产出一个 dict:
        {
            "course": str,        # 原始 course 名 (如 "oni_p1")
            "difficulty": str,    # 标准化难度 (easy/normal/hard/oni/edit)
            "baseDifficulty": str,
            "branchType": str,    # "unbranched" / "normal" / "expert" / "master"
            "ratings": {          # 算法原始输出
                "stamina", "speed", "burst", "complex", "complexRatio",
                "rhythm", "rhythmRatio", "rollEquivalent", "rollEquivalentOutputs",
                "totalNotes"
            }
        }
    """

    def __init__(self, api_url: Optional[str] = None):
        self.api_url = api_url or os.environ.get("TJA_API_URL", DEFAULT_API_URL)

    # ------------------------------------------------------------------
    # 难度名称标准化
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_difficulty_name(name: str) -> str:
        """标准化难度名称，处理 course_side 格式（如 edit_p1 → edit）。"""
        mapping = {"0": "easy", "1": "normal", "2": "hard", "3": "oni", "4": "edit"}
        base = str(name).lower()
        for suffix in ("_p1", "_p2", "_single"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return mapping.get(base, base)

    # ------------------------------------------------------------------
    # API 调用
    # ------------------------------------------------------------------

    def analyze(self, tja_content: str, unit: str = "ms") -> dict:
        """将 TJA 内容 POST 到 API，返回原始分析结果（gaps + noteTypes）。"""
        url = f"{self.api_url}/?unit={unit}&longNoteHandling=skip"
        data = tja_content.encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "User-Agent": "taiko-constants-workflow/1.0",
            },
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)

        if "error" in result:
            raise RuntimeError(f"API error: {result['error']}")

        return result

    # ------------------------------------------------------------------
    # 数据提取
    # ------------------------------------------------------------------

    @staticmethod
    def extract_intervals(branch_gaps) -> List[float]:
        """从 per-bar gap 数组中提取 flat 间隔数组（过滤 null 和 <=0 值）。"""
        intervals: List[float] = []
        if not branch_gaps or not isinstance(branch_gaps, list):
            return intervals
        for bar in branch_gaps:
            if bar and isinstance(bar, list):
                for g in bar:
                    if g is not None and g > 0:
                        intervals.append(float(g))
        return intervals

    @staticmethod
    def count_total_notes(branch_gaps, note_types=[]) -> int:
        """统计谱面 judgeable note 总数。"""
        if isinstance(note_types, list):
            return sum(1 for v in note_types if v in (1, 2, 3, 4))
        total = 0
        if branch_gaps and isinstance(branch_gaps, list):
            for bar in branch_gaps:
                if bar and isinstance(bar, list):
                    total += len(bar)
        return total

    # ------------------------------------------------------------------
    # 滚奏等效
    # ------------------------------------------------------------------

    @staticmethod
    def calc_roll_equivalent(intervals: List[float], note_types=[]) -> tuple:
        """计算滚奏等效值，返回 (max_c, [C1, C2, C3])。"""
        if not intervals:
            return 0.0, [[], [], 0]

        an = intervals.copy()
        results = _roll.process(an, note_types)

        if not results:
            return 0.0, [[], [], 0]

        candidates = []
        if isinstance(results, tuple) and len(results) == 3:
            candidates = [results]
        elif isinstance(results, list):
            for item in results:
                if isinstance(item, (tuple, list)) and len(item) == 3:
                    candidates.append(item)

        if not candidates:
            return 0.0, [[], [], 0]

        max_c = max((item[2] for item in candidates), default=0)
        primary = candidates[0]
        return float(max_c), [
            primary[0] if isinstance(primary[0], list) else [],
            primary[1] if isinstance(primary[1], list) else [],
            float(primary[2]) if isinstance(primary[2], (int, float)) else 0.0,
        ]

    # ------------------------------------------------------------------
    # 原始定数计算 (8 个算法维度)
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_raw_ratings(branch_gaps, note_types=[]) -> Dict[str, Any]:
        """计算单个谱面分支的全部原始定数。"""
        intervals = TJAChartAnalyzer.extract_intervals(branch_gaps)
        total_notes = TJAChartAnalyzer.count_total_notes(branch_gaps, note_types)

        ratings: Dict[str, Any] = {
            "stamina": 0.0,
            "complex": 0.0,
            "complexRatio": 0.0,
            "rhythm": 0.0,
            "rhythmRatio": 0.0,
            "speed": 0.0,
            "rollEquivalent": 0.0,
            "rollEquivalentOutputs": [[], [], 0],
            "burst": 0.0,
            "totalNotes": total_notes,
        }

        if not intervals:
            return ratings

        # 体力
        try:
            _, _, ratings["stamina"] = algorithms.calculate_result(intervals)
        except Exception:
            pass

        # 复合
        try:
            ratings["complex"], ratings["complexRatio"] = (
                algorithms.calculate_complete_compound_difficulty(intervals, note_types)
            )
        except Exception:
            pass

        # 节奏
        try:
            ratings["rhythm"], ratings["rhythmRatio"] = (
                algorithms.compute_final_rhythm_difficulty(intervals)
            )
        except Exception:
            pass

        # 手速
        try:
            ratings["speed"] = algorithms.compute_weighted_average(intervals)
        except Exception:
            pass

        # 滚奏等效
        try:
            roll_value, roll_outputs = TJAChartAnalyzer.calc_roll_equivalent(
                intervals, note_types
            )
            ratings["rollEquivalent"] = roll_value
            ratings["rollEquivalentOutputs"] = roll_outputs
        except Exception:
            pass

        # 爆发
        try:
            ratings["burst"] = algorithms.compute_weighted_average(intervals)
        except Exception:
            pass

        return ratings

    # ------------------------------------------------------------------
    # 批量处理
    # ------------------------------------------------------------------

    def process(self, analysis: dict) -> List[dict]:
        """处理 API 返回的分析数据，为所有谱面分支计算原始定数。

        API 返回扁平结构：每个 key 直接对应一个谱面。
        STYLE:Double 的 p1/p2/single 已打平为 course_side 键。
        """
        charts: List[dict] = []
        courses = analysis.get("courses", {})
        note_types_all = analysis.get("noteTypes", {})

        for course_name, chart_gaps in courses.items():
            if not isinstance(chart_gaps, dict):
                continue

            normalized_diff = self.normalize_difficulty_name(course_name)
            chart_note_types = note_types_all.get(course_name, {})
            if not isinstance(chart_note_types, dict):
                chart_note_types = {}

            for branch_type, branch_gaps in chart_gaps.items():
                if not isinstance(branch_gaps, list):
                    continue

                note_types = chart_note_types.get(branch_type, [])
                ratings = self.calculate_raw_ratings(branch_gaps, note_types)

                charts.append({
                    "course": course_name,
                    "difficulty": normalized_diff,
                    "baseDifficulty": (
                        "oni" if normalized_diff == "edit" else normalized_diff
                    ),
                    "branchType": branch_type,
                    "ratings": ratings,
                })

        return charts

    # ------------------------------------------------------------------
    # 便捷方法：一步完成分析+原始定数计算
    # ------------------------------------------------------------------

    def analyze_and_process(
        self, tja_content: str, unit: str = "ms"
    ) -> List[dict]:
        """分析 TJA 内容并返回所有谱面的原始定数。相当于 analyze() + process()。"""
        raw = self.analyze(tja_content, unit)
        return self.process(raw)
