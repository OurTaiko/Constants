#!/usr/bin/env python3
"""
TJA 谱面分析模块
================

封装 API 调用、数据提取、原始定数计算。
返回的 Chart 对象可直接传入 rating.RatingPipeline 计算最终定数。

用法:
    from tja_analysis import TJAChartAnalyzer

    analyzer = TJAChartAnalyzer()
    raw_analysis = analyzer.analyze(tja_content, unit="ms")
    charts = analyzer.process(raw_analysis)
    # charts[i].ratings → ChartRatings(stamina, speed, burst, complex, ...)
"""

from __future__ import annotations

import json
import os
import sys
import importlib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

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
# 数据结构
# ===========================================================================


@dataclass
class ChartRatings:
    """单个谱面分支的算法原始定数输出。

    对应 workflow.md 初始数据：totalNotes + 7 个算法原始值，
    外加滚奏等效及其中间产物。
    """

    stamina: float = 0.0              # 体力
    speed: float = 0.0               # 手速
    burst: float = 0.0               # 爆发
    complex: float = 0.0             # 复合
    complex_ratio: float = 0.0       # 复合占比
    rhythm: float = 0.0              # 节奏
    rhythm_ratio: float = 0.0        # 节奏占比
    roll_equivalent: float = 0.0     # 滚奏等效值
    roll_equivalent_outputs: list = field(
        default_factory=lambda: [[], [], 0]
    )  # 滚奏等效中间产物 [[C1,C2,C3], [], value]
    total_notes: int = 0             # 谱面 judgeable note 总数

    def to_dict(self) -> dict:
        """序列化为 camelCase JSON 友好字典（保持向后兼容）。"""
        return {
            "stamina": self.stamina,
            "speed": self.speed,
            "burst": self.burst,
            "complex": self.complex,
            "complexRatio": self.complex_ratio,
            "rhythm": self.rhythm,
            "rhythmRatio": self.rhythm_ratio,
            "rollEquivalent": self.roll_equivalent,
            "rollEquivalentOutputs": self.roll_equivalent_outputs,
            "totalNotes": self.total_notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChartRatings":
        """从 camelCase 字典（如 JSON 反序列化）构造。"""
        if not d:
            return cls()
        return cls(
            stamina=d.get("stamina", 0.0),
            speed=d.get("speed", 0.0),
            burst=d.get("burst", 0.0),
            complex=d.get("complex", 0.0),
            complex_ratio=d.get("complexRatio", 0.0),
            rhythm=d.get("rhythm", 0.0),
            rhythm_ratio=d.get("rhythmRatio", 0.0),
            roll_equivalent=d.get("rollEquivalent", 0.0),
            roll_equivalent_outputs=d.get(
                "rollEquivalentOutputs", [[], [], 0]
            ),
            total_notes=d.get("totalNotes", 0),
        )


@dataclass
class Chart:
    """单个谱面分支的分析结果（一个 course × branch 组合）。

    course / branch_type 描述谱面来源，ratings 为该分支的算法原始定数。
    """

    course: str = ""                       # 原始 course 名 (如 "oni_p1")
    branch_type: str = "unbranched"        # unbranched / normal / expert / master
    ratings: ChartRatings = field(default_factory=ChartRatings)

    def to_dict(self) -> dict:
        """序列化为 camelCase JSON 友好字典（保持向后兼容）。"""
        return {
            "course": self.course,
            "branchType": self.branch_type,
            "ratings": self.ratings.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Chart":
        """从 camelCase 字典（如 JSON 反序列化）构造。"""
        if not d:
            return cls()
        return cls(
            course=d.get("course", ""),
            branch_type=d.get("branchType", "unbranched"),
            ratings=ChartRatings.from_dict(d.get("ratings", {})),
        )


# ===========================================================================
# TJA 谱面分析器
# ===========================================================================


class TJAChartAnalyzer:
    """封装 TJA 谱面分析全流程：API 调用 → 数据提取 → 原始定数计算。

    每个谱面分支产出一个 Chart 对象（含 ChartRatings）。
    """

    def __init__(self, api_url: Optional[str] = None):
        self.api_url = api_url or os.environ.get("TJA_API_URL", DEFAULT_API_URL)

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
    def calculate_raw_ratings(branch_gaps, note_types=[]) -> ChartRatings:
        """计算单个谱面分支的全部原始定数，返回 ChartRatings。"""
        intervals = TJAChartAnalyzer.extract_intervals(branch_gaps)
        total_notes = TJAChartAnalyzer.count_total_notes(branch_gaps, note_types)

        ratings = ChartRatings(total_notes=total_notes)

        if not intervals:
            return ratings

        # 体力
        try:
            _, _, ratings.stamina = algorithms.calculate_result(intervals)
        except Exception:
            pass

        # 复合
        try:
            ratings.complex, ratings.complex_ratio = (
                algorithms.calculate_complete_compound_difficulty(intervals, note_types)
            )
        except Exception:
            pass

        # 节奏
        try:
            ratings.rhythm, ratings.rhythm_ratio = (
                algorithms.compute_final_rhythm_difficulty(intervals)
            )
        except Exception:
            pass

        # 手速
        try:
            ratings.speed = algorithms.compute_weighted_average(intervals)
        except Exception:
            pass

        # 滚奏等效
        try:
            roll_value, roll_outputs = TJAChartAnalyzer.calc_roll_equivalent(
                intervals, note_types
            )
            ratings.roll_equivalent = roll_value
            ratings.roll_equivalent_outputs = roll_outputs
        except Exception:
            pass

        # 爆发
        try:
            ratings.burst = algorithms.compute_weighted_average(intervals)
        except Exception:
            pass

        return ratings

    # ------------------------------------------------------------------
    # 批量处理
    # ------------------------------------------------------------------

    def process(self, analysis: dict) -> List[Chart]:
        """处理 API 返回的分析数据，为所有谱面分支计算原始定数。

        API 返回扁平结构：每个 key 直接对应一个谱面。
        STYLE:Double 的 p1/p2/single 已打平为 course_side 键。
        """
        charts: List[Chart] = []
        courses = analysis.get("courses", {})
        note_types_all = analysis.get("noteTypes", {})

        for course_name, chart_gaps in courses.items():
            if not isinstance(chart_gaps, dict):
                continue

            chart_note_types = note_types_all.get(course_name, {})
            if not isinstance(chart_note_types, dict):
                chart_note_types = {}

            for branch_type, branch_gaps in chart_gaps.items():
                if not isinstance(branch_gaps, list):
                    continue

                note_types = chart_note_types.get(branch_type, [])
                ratings = self.calculate_raw_ratings(branch_gaps, note_types)

                charts.append(Chart(
                    course=course_name,
                    branch_type=branch_type,
                    ratings=ratings,
                ))

        return charts

    # ------------------------------------------------------------------
    # 便捷方法：一步完成分析+原始定数计算
    # ------------------------------------------------------------------

    def analyze_and_process(
        self, tja_content: str, unit: str = "ms"
    ) -> List[Chart]:
        """分析 TJA 内容并返回所有谱面的原始定数。相当于 analyze() + process()。"""
        raw = self.analyze(tja_content, unit)
        return self.process(raw)
