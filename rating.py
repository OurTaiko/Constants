#!/usr/bin/env python3
"""
定数计算管线 — 实现 rating.xlsx「拉表」的运算逻辑
====================================================

第二阶段模块：将 tja_analysis 产出的原始算法值转换为最终 8 个输出字段。
完整批量工作流请使用 batch_workflow.py（ese_mapping → 本地 tja → 最终定数）。

用法:
    from tja_analysis import TJAChartAnalyzer
    from rating import ChartRawData, RatingPipeline

    analyzer = TJAChartAnalyzer()
    charts = analyzer.analyze_and_process(tja_content)

    # 动态校准：从数据集自身推导 13 个全局参考值
    datas = [ChartRawData.from_chart(c) for c in charts]
    ref_values = RatingPipeline.calibrate(datas)

    pipeline = RatingPipeline(ref_values)
    results = pipeline.compute_all(charts)
    for r in results:
        print(r.sub_constant_1, r.main_constant, r.sub_constant_2)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from tja_analysis import Chart, ChartRatings

# ---------------------------------------------------------------------------
# 全局参考值（13 个 MIN/MAX）现全部由 RatingPipeline.calibrate() 从数据集动态推导，
# 不再保留 rating.xlsx 的固定值。键名 = min_/max_ + workflow.md 中文名。
# ---------------------------------------------------------------------------


# ===========================================================================
# 数据结构
# ===========================================================================


@dataclass
class ChartRawData:
    """单个谱面的原始数据，来自 tja_analysis 的算法输出。

    对应 workflow.md 的初始数据（totalNotes + 7 个算法原始值）:
        totalNotes, stamina, speed, burst, complex, complexRatio, rhythm, rhythmRatio
    """

    course: str = ""
    branch_type: str = "unbranched"

    # ---- 谱面基础 ----
    total_notes: int = 0

    # ---- 算法原始输出 ----
    stamina_raw: float = 0.0  # 体力算法输出
    speed_raw: float = 0.0  # 手速算法输出
    burst_raw: float = 0.0  # 爆发算法输出
    complex_raw: float = 0.0  # 复合算法输出
    complex_ratio: float = 0.0  # 复合占比
    rhythm_raw: float = 0.0  # 节奏算法输出
    rhythm_ratio: float = 0.0  # 节奏占比

    @classmethod
    def from_workflow_ratings(
        cls,
        course: str,
        branch_type: str,
        ratings: ChartRatings,
    ) -> "ChartRawData":
        """从 tja_analysis 的 ChartRatings 构造。"""
        return cls(
            course=course,
            branch_type=branch_type,
            total_notes=ratings.total_notes,
            stamina_raw=ratings.stamina,
            speed_raw=ratings.speed,
            burst_raw=ratings.burst,
            complex_raw=ratings.complex,
            complex_ratio=ratings.complex_ratio,
            rhythm_raw=ratings.rhythm,
            rhythm_ratio=ratings.rhythm_ratio,
        )

    @classmethod
    def from_chart(cls, chart: Chart) -> "ChartRawData":
        """从 tja_analysis 的 Chart 对象构造。"""
        return cls.from_workflow_ratings(
            course=chart.course,
            branch_type=chart.branch_type,
            ratings=chart.ratings,
        )


@dataclass
class ChartConstantResult:
    """最终 8 个输出字段，对应 workflow.md 的最终定数。"""

    sub_constant_1: float = 0.0  # 归一75定数
    main_constant: float = 0.0  # 归一主定数
    sub_constant_2: float = 0.0  # 最终99定数
    stamina: float = 0.0  # 体力 (0-15.5 归一化)
    handspeed: float = 0.0  # 手速 (0-15.5 归一化)
    burst: float = 0.0  # 爆发 (0-15.5 归一化)
    complex: float = 0.0  # 复合 (0-15.5 归一化)
    rhythm: float = 0.0  # 节奏 (0-15.5 归一化)

    # 关联的原始数据（可选）
    source: Optional[ChartRawData] = None

    def as_dict(self) -> dict:
        return {
            "sub_constant_1": self.sub_constant_1,
            "main_constant": self.main_constant,
            "sub_constant_2": self.sub_constant_2,
            "stamina": self.stamina,
            "handspeed": self.handspeed,
            "burst": self.burst,
            "complex": self.complex,
            "rhythm": self.rhythm,
        }

    def __repr__(self) -> str:
        return (
            f"ChartConstantResult(\n"
            f"  sub_constant_1={self.sub_constant_1:.4f}\n"
            f"  main_constant ={self.main_constant:.4f}\n"
            f"  sub_constant_2={self.sub_constant_2:.4f}\n"
            f"  stamina  ={self.stamina:.4f}\n"
            f"  handspeed={self.handspeed:.4f}\n"
            f"  burst    ={self.burst:.4f}\n"
            f"  complex  ={self.complex:.4f}\n"
            f"  rhythm   ={self.rhythm:.4f}\n"
            f")"
        )


# ===========================================================================
# 定数计算管线
# ===========================================================================


class RatingPipeline:
    """实现 workflow.md「拉表」的完整定数推导管线。

    输入:  ChartRawData（原始算法值 + 总 note 数）
    输出:  ChartConstantResult（8 个最终字段）

    内部变量名与 workflow.md 保持一致（体力换算、手速换算、粗糙主定数 等），
    不使用列编号。
    """

    def __init__(self, ref_values: Dict[str, float]):
        """构造计算管线。

        ref_values 必须由 RatingPipeline.calibrate() 从数据集动态推导得到，
        不再提供固定默认值。
        """
        self.ref: Dict[str, float] = ref_values

    # ------------------------------------------------------------------
    # Step 1: 原始值 → 换算值（体力换算 ~ 节奏换算）
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_stamina(raw: float) -> float:
        """体力换算 — sigmoid 映射到 [0, 15.5]"""
        val = 16.3783 / (1.0 + math.exp(-0.6764 * (raw - 7.2836))) - 0.6012
        return max(min(val, 15.5), 0.0)

    @staticmethod
    def _convert_speed(raw: float) -> float:
        """手速换算 — 分段函数"""
        if raw < 5.0:
            return 3.0 * (raw / 5.0) ** (35.0 / 3.0)
        if raw < 6.0:
            return 7.0 * raw - 32.0
        if raw < 10.0:
            return 11.0 / 8.0 * raw + 1.75
        return 15.5

    @staticmethod
    def _convert_burst(raw: float) -> float:
        """爆发换算 — 分段函数"""
        threshold = 35.5 / 7.0  # ≈ 5.0714
        if raw < threshold:
            return (14.0 * raw / 71.0) ** (71.0 / 2.0)
        if raw < 6.0:
            return 7.0 * raw - 34.5
        if raw < 10.0:
            return 7.0 / 4.0 * raw - 3.0
        if raw < 12.75:
            return (8.0 * raw + 239.0) / 22.0
        return 15.5

    @staticmethod
    def _convert_complex_ratio(ratio: float) -> float:
        """复合占比换算 — tanh 映射"""
        val = 18200.736 * math.tanh(4.491 * ratio + 3.86) - 18184.558
        return max(min(val, 15.5), 0.0)

    @staticmethod
    def _complex_upper(total_notes: int) -> float:
        """复合上限 — 基于总 note 数的 sigmoid 上限"""
        return 17.7743 / (1.0 + math.exp(-0.0083 * total_notes + 2.8484)) - 0.9613

    def _convert_complex(self, 复合占比换算: float, 复合上限: float) -> float:
        """复合换算 — 用全局复合占比换算的 MIN/MAX 归一后取 min 与上限"""
        ref = self.ref
        归一值 = (
            (复合占比换算 - ref["min_复合占比换算"])
            / (ref["max_复合占比换算"] - ref["min_复合占比换算"])
            * 15.5
        )
        return min(归一值, 复合上限)

    @staticmethod
    def _convert_rhythm_ratio(ratio: float) -> float:
        """节奏占比换算 — sigmoid 映射"""
        return 20.1353 / (1.0 + math.exp(-18.0625 * (ratio - 0.0692))) - 4.4496

    @staticmethod
    def _rhythm_upper(total_notes: int) -> float:
        """节奏上限 — 基于总 note 数的 sigmoid 上限"""
        return 17.4097 / (1.0 + math.exp(-0.007 * total_notes + 2.7059)) - 1.0787

    @staticmethod
    def _convert_rhythm(节奏占比换算: float, 节奏上限: float) -> float:
        """节奏换算 — min(节奏占比换算, 节奏上限)"""
        return min(节奏占比换算, 节奏上限)

    # ------------------------------------------------------------------
    # Step 2: 归一化到 [0, 15.5]（体力 ~ 节奏）
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(val: float, min_val: float, max_val: float) -> float:
        if max_val == min_val:
            return 0.0
        return (val - min_val) / (max_val - min_val) * 15.5

    # ------------------------------------------------------------------
    # Step 3: 75定数 / sub_constant_1（归一75定数）
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_75_constant(
        体力: float, 手速: float, 复合: float, max_粗糙75定数: float
    ) -> float:
        粗糙75定数 = math.sqrt((体力 * 体力 + 手速 * 手速 + 复合 * 复合) / 3.0)
        return 15.5 * 粗糙75定数 / max_粗糙75定数

    # ------------------------------------------------------------------
    # Step 4a: 粗糙主定数（纯函数，供 calibrate 复用）
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_raw_main_constant(
        体力: float, 手速: float, 爆发: float, 复合: float, 节奏: float
    ) -> float:
        """粗糙主定数 — 加权 RMS，未做 13.3 软上限归一。"""
        # 定数粗略值
        最小维度 = min(体力, 手速, 爆发, 复合, 节奏)
        平方和 = 体力 * 体力 + 手速 * 手速 + 爆发 * 爆发 + 复合 * 复合 + 节奏 * 节奏
        定数粗略值 = math.sqrt((平方和 - 0.9 * 最小维度 * 最小维度) / 4.1)

        # 比较值参数
        平均Minus10 = 0.5 * math.tanh(1.0 * (定数粗略值 - 10.0)) + 0.5
        体力Minus平均 = 0.5 * math.tanh(3.0 * (体力 - 定数粗略值 + 0.5)) + 0.5
        体力Minus14_5 = 0.5 * math.tanh(3.0 * (体力 - 14.5)) + 0.5
        爆发Minus平均 = 0.5 * math.tanh(3.0 * (爆发 - 定数粗略值 + 0.5)) + 0.5
        复合Minus平均 = 0.5 * math.tanh(3.0 * (复合 - 定数粗略值 + 0.5)) + 0.5
        节奏Minus平均 = 0.5 * math.tanh(3.0 * (节奏 - 定数粗略值 + 0.5)) + 0.5

        # 条件判断
        体力条件判断 = 平均Minus10 * 体力Minus平均 * (1.0 - 体力Minus14_5)
        手速条件判断 = 平均Minus10
        爆发条件判断 = 1.0 - 爆发Minus平均
        复合条件判断 = 平均Minus10 * 复合Minus平均
        节奏条件判断 = 平均Minus10 * 节奏Minus平均

        # 主定数权重
        if 最小维度 == 体力:
            体力权重 = 0.1
        else:
            体力权重 = 0.7 * (1.0 - 体力条件判断) + 0.3
        if 最小维度 == 手速:
            手速权重 = 0.9 * 手速条件判断 + 0.1
        else:
            手速权重 = 1.0
        if 最小维度 == 爆发:
            爆发权重 = 0.1
        else:
            爆发权重 = 0.9 * 爆发条件判断 + 0.1
        if 最小维度 == 复合:
            复合权重 = 0.1
        else:
            复合权重 = 0.9 * (1.0 - 复合条件判断) + 0.1
        if 最小维度 == 节奏:
            节奏权重 = 0.1
        else:
            节奏权重 = 0.9 * (1.0 - 节奏条件判断) + 0.1

        # 粗糙主定数：加权 RMS
        平方 = [体力 * 体力, 手速 * 手速, 爆发 * 爆发, 复合 * 复合, 节奏 * 节奏]
        权重 = [体力权重, 手速权重, 爆发权重, 复合权重, 节奏权重]
        加权平方和 = sum(s * w for s, w in zip(平方, 权重))
        权重和 = sum(权重)
        return math.sqrt(加权平方和 / 权重和) if 权重和 > 0.0 else 0.0

    # ------------------------------------------------------------------
    # Step 4: 主定数 / main_constant（归一主定数）
    # ------------------------------------------------------------------

    def _calc_main_constant(
        self, 体力: float, 手速: float, 爆发: float, 复合: float, 节奏: float
    ) -> float:
        ref = self.ref
        粗糙主定数 = self._calc_raw_main_constant(体力, 手速, 爆发, 复合, 节奏)
        # 归一主定数：13.3 软上限
        if 粗糙主定数 > 13.3:
            return (
                13.3
                + (15.5 - 13.3) * (粗糙主定数 - 13.3) / (ref["max_粗糙主定数"] - 13.3)
            )
        return 粗糙主定数

    # ------------------------------------------------------------------
    # Step 5a: 粗糙99定数（纯函数，供 calibrate 复用）
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_raw_99_constant(
        体力: float,
        手速: float,
        爆发: float,
        复合: float,
        节奏: float,
        归一主定数: float,
    ) -> float:
        """粗糙99定数 — 加权 RMS，未做 13.3 软上限归一。"""
        # 主定数范围
        if 归一主定数 > 14.5:
            主定数范围 = 1
        elif 归一主定数 > 13.5:
            主定数范围 = 2
        elif 归一主定数 > 12.5:
            主定数范围 = 3
        elif 归一主定数 > 11.5:
            主定数范围 = 4
        elif 归一主定数 > 9.5:
            主定数范围 = 5
        else:
            主定数范围 = 6

        # 主定数权重（独立重算以避免与主定数方法耦合）
        平方和 = 体力 * 体力 + 手速 * 手速 + 爆发 * 爆发 + 复合 * 复合 + 节奏 * 节奏
        最小维度 = min(体力, 手速, 爆发, 复合, 节奏)
        定数粗略值 = math.sqrt((平方和 - 0.9 * 最小维度 * 最小维度) / 4.1)

        平均Minus10 = 0.5 * math.tanh(1.0 * (定数粗略值 - 10.0)) + 0.5
        体力Minus平均 = 0.5 * math.tanh(3.0 * (体力 - 定数粗略值 + 0.5)) + 0.5
        体力Minus14_5 = 0.5 * math.tanh(3.0 * (体力 - 14.5)) + 0.5
        爆发Minus平均 = 0.5 * math.tanh(3.0 * (爆发 - 定数粗略值 + 0.5)) + 0.5
        复合Minus平均 = 0.5 * math.tanh(3.0 * (复合 - 定数粗略值 + 0.5)) + 0.5
        节奏Minus平均 = 0.5 * math.tanh(3.0 * (节奏 - 定数粗略值 + 0.5)) + 0.5

        体力条件判断 = 平均Minus10 * 体力Minus平均 * (1.0 - 体力Minus14_5)
        手速条件判断 = 平均Minus10
        爆发条件判断 = 1.0 - 爆发Minus平均
        复合条件判断 = 平均Minus10 * 复合Minus平均
        节奏条件判断 = 平均Minus10 * 节奏Minus平均

        if 最小维度 == 体力:
            体力权重 = 0.1
        else:
            体力权重 = 0.7 * (1.0 - 体力条件判断) + 0.3
        if 最小维度 == 手速:
            手速权重 = 0.9 * 手速条件判断 + 0.1
        else:
            手速权重 = 1.0
        if 最小维度 == 爆发:
            爆发权重 = 0.1
        else:
            爆发权重 = 0.9 * 爆发条件判断 + 0.1
        if 最小维度 == 复合:
            复合权重 = 0.1
        else:
            复合权重 = 0.9 * (1.0 - 复合条件判断) + 0.1
        if 最小维度 == 节奏:
            节奏权重 = 0.1
        else:
            节奏权重 = 0.9 * (1.0 - 节奏条件判断) + 0.1

        # 99定数比较值参数
        体力Minus14 = 0.5 * math.tanh(3.0 * (体力 - 14.0)) + 0.5
        体力Minus13_5 = 0.5 * math.tanh(3.0 * (体力 - 13.5)) + 0.5
        手速Minus11 = 0.5 * math.tanh(3.0 * (手速 - 11.0)) + 0.5
        爆发Minus15 = 0.5 * math.tanh(3.0 * (爆发 - 15.0)) + 0.5
        爆发Minus8_5 = 0.5 * math.tanh(3.0 * (爆发 - 8.5)) + 0.5
        节奏Minus主定数 = 0.5 * math.tanh(3.0 * (节奏 - 归一主定数)) + 0.5

        # 99定数权重
        if 主定数范围 == 3:
            体力99定数权重 = 1.0 * 体力Minus14 + 体力权重 * (1.0 - 体力Minus14)
        elif 主定数范围 == 4:
            体力99定数权重 = 1.0 * 体力Minus13_5 + 体力权重 * (1.0 - 体力Minus13_5)
        elif 主定数范围 in (5, 6):
            体力99定数权重 = 0.1
        else:
            体力99定数权重 = 体力权重

        if 主定数范围 == 5:
            手速99定数权重 = 0.5 * 手速Minus11 + 0.5
        elif 主定数范围 == 6:
            手速99定数权重 = 0.9 * 手速Minus11 + 0.1
        else:
            手速99定数权重 = 手速权重

        if 主定数范围 == 1:
            爆发99定数权重 = 1.0
        elif 主定数范围 == 2:
            爆发99定数权重 = 0.5
        elif 主定数范围 == 3:
            爆发99定数权重 = 0.5 * 爆发Minus15 + 爆发权重 * (1.0 - 爆发Minus15)
        elif 主定数范围 == 4:
            爆发99定数权重 = 0.5
        elif 主定数范围 == 5:
            爆发99定数权重 = 0.3 * 爆发Minus8_5 + 0.5
        elif 主定数范围 == 6:
            爆发99定数权重 = 0.9 * 爆发Minus8_5 + 0.1
        else:
            爆发99定数权重 = 爆发权重

        if 主定数范围 in (1, 2):
            复合99定数权重 = 复合权重
        else:
            复合99定数权重 = 0.1

        if 主定数范围 == 1:
            节奏99定数权重 = 0.3
        elif 主定数范围 == 2:
            节奏99定数权重 = 0.5
        elif 主定数范围 == 3:
            节奏99定数权重 = 0.5
        elif 主定数范围 == 4:
            节奏99定数权重 = 0.8
        elif 主定数范围 == 5:
            节奏99定数权重 = 0.5 * 节奏Minus主定数 + 0.3
        elif 主定数范围 == 6:
            节奏99定数权重 = 1.0
        else:
            节奏99定数权重 = 节奏权重

        # 粗糙99定数：加权 RMS
        平方 = [体力 * 体力, 手速 * 手速, 爆发 * 爆发, 复合 * 复合, 节奏 * 节奏]
        权重 = [
            体力99定数权重,
            手速99定数权重,
            爆发99定数权重,
            复合99定数权重,
            节奏99定数权重,
        ]
        加权平方和 = sum(s * w for s, w in zip(平方, 权重))
        权重和 = sum(权重)
        return math.sqrt(加权平方和 / 权重和) if 权重和 > 0.0 else 0.0

    # ------------------------------------------------------------------
    # Step 5: 99定数 / sub_constant_2（最终99定数）
    # ------------------------------------------------------------------

    def _calc_99_constant(
        self,
        体力: float,
        手速: float,
        爆发: float,
        复合: float,
        节奏: float,
        归一主定数: float,
    ) -> float:
        ref = self.ref
        粗糙99定数 = self._calc_raw_99_constant(
            体力, 手速, 爆发, 复合, 节奏, 归一主定数
        )

        # 归一99定数：13.3 软上限
        if 粗糙99定数 > 13.3:
            归一99定数 = (
                13.3
                + (15.5 - 13.3) * (粗糙99定数 - 13.3) / (ref["max_粗糙99定数"] - 13.3)
            )
        else:
            归一99定数 = 粗糙99定数

        # 手速限制
        手速限制 = 1.0 / 8.0 * 手速 * 手速 + 10.0

        # 最终99定数：取 min
        return min(归一99定数, 手速限制)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def compute(self, data: ChartRawData) -> ChartConstantResult:
        """从原始算法值计算 8 个最终输出字段。"""
        ref = self.ref

        # Step 1: 原始值 → 换算值
        体力换算 = self._convert_stamina(data.stamina_raw)
        手速换算 = self._convert_speed(data.speed_raw)
        爆发换算 = self._convert_burst(data.burst_raw)
        复合占比换算 = self._convert_complex_ratio(data.complex_ratio)
        复合上限 = self._complex_upper(data.total_notes)
        复合换算 = self._convert_complex(复合占比换算, 复合上限)
        节奏占比换算 = self._convert_rhythm_ratio(data.rhythm_ratio)
        节奏上限 = self._rhythm_upper(data.total_notes)
        节奏换算 = self._convert_rhythm(节奏占比换算, 节奏上限)

        # Step 2: 归一化 [0, 15.5]
        体力 = self._normalize(体力换算, ref["min_体力换算"], ref["max_体力换算"])
        手速 = self._normalize(手速换算, 0.0, ref["max_手速换算"])
        爆发 = self._normalize(爆发换算, 0.0, ref["max_爆发换算"])
        复合 = self._normalize(复合换算, ref["min_复合换算"], ref["max_复合换算"])
        节奏 = self._normalize(节奏换算, ref["min_节奏换算"], ref["max_节奏换算"])

        # Step 3: 75定数 (sub_constant_1)
        sub1 = self._calc_75_constant(体力, 手速, 复合, ref["max_粗糙75定数"])

        # Step 4: 主定数 (main_constant)
        主定数 = self._calc_main_constant(体力, 手速, 爆发, 复合, 节奏)

        # Step 5: 99定数 (sub_constant_2)
        sub2 = self._calc_99_constant(体力, 手速, 爆发, 复合, 节奏, 主定数)

        return ChartConstantResult(
            sub_constant_1=sub1,
            main_constant=主定数,
            sub_constant_2=sub2,
            stamina=体力,
            handspeed=手速,
            burst=爆发,
            complex=复合,
            rhythm=节奏,
            source=data,
        )

    def compute_from_chart(self, chart: Chart) -> ChartConstantResult:
        """从 tja_analysis process() 输出的 Chart 对象直接计算。"""
        return self.compute(ChartRawData.from_chart(chart))

    def compute_all(self, charts: List[Chart]) -> List[ChartConstantResult]:
        """批量计算多个谱面。"""
        return [self.compute_from_chart(c) for c in charts]

    # ------------------------------------------------------------------
    # 动态校准 — 从全量数据集计算 13 个全局参考值
    # ------------------------------------------------------------------

    @classmethod
    def calibrate(cls, all_data: List[ChartRawData]) -> Dict[str, float]:
        """从全量数据集自身计算 13 个全局 MIN/MAX 参考值。

        忠实于 workflow.md 的「所有乐曲」语义：归一化所需的全局极值
        均由本次输入数据集推导（全动态，不依赖任何固定值）。

        按 workflow.md 的依赖层级分阶段计算：
          A 每谱: 体力换算,手速换算,爆发换算,复合占比换算,复合上限,节奏占比换算,节奏上限
          B 全局: min_复合占比换算, max_复合占比换算
          C 每谱: 复合换算=min((复合占比换算-min)/(max-min)*15.5, 复合上限);  节奏换算=min(节奏占比换算, 节奏上限)
          D 全局: min/max_体力换算, max_手速换算, max_爆发换算, min/max_复合换算, min/max_节奏换算
          E 每谱: 体力,手速,爆发,复合,节奏 (归一化，使用本阶段刚算出的全局极值)
          F 每谱: 粗糙75定数=sqrt((体力²+手速²+复合²)/3);  粗糙主定数=_calc_raw_main_constant
          G 全局: max_粗糙75定数, max_粗糙主定数
          H 每谱: 归一主定数 (13.3 软上限，用 max_粗糙主定数)
          I 每谱: 粗糙99定数=_calc_raw_99_constant(..., 归一主定数)
          J 全局: max_粗糙99定数
        """
        if not all_data:
            raise ValueError("calibrate 需要非空数据集")

        n = len(all_data)

        # Stage A: 每谱换算值
        体力换算 = [cls._convert_stamina(d.stamina_raw) for d in all_data]
        手速换算 = [cls._convert_speed(d.speed_raw) for d in all_data]
        爆发换算 = [cls._convert_burst(d.burst_raw) for d in all_data]
        复合占比换算 = [cls._convert_complex_ratio(d.complex_ratio) for d in all_data]
        复合上限 = [cls._complex_upper(d.total_notes) for d in all_data]
        节奏占比换算 = [cls._convert_rhythm_ratio(d.rhythm_ratio) for d in all_data]
        节奏上限 = [cls._rhythm_upper(d.total_notes) for d in all_data]

        # Stage B: 复合占比换算的全局极值
        min_复合占比换算 = min(复合占比换算)
        max_复合占比换算 = max(复合占比换算)

        # Stage C: 复合换算（用本地极值）与 节奏换算（纯函数）
        占比换算跨度 = max_复合占比换算 - min_复合占比换算
        复合换算 = [
            min(
                (复合占比换算[i] - min_复合占比换算) / 占比换算跨度 * 15.5, 复合上限[i]
            )
            if 占比换算跨度 > 0
            else 0.0
            for i in range(n)
        ]
        节奏换算 = [cls._convert_rhythm(节奏占比换算[i], 节奏上限[i]) for i in range(n)]

        # Stage D: 归一化所需全局极值
        min_体力换算, max_体力换算 = min(体力换算), max(体力换算)
        max_手速换算 = max(手速换算)
        max_爆发换算 = max(爆发换算)
        min_复合换算, max_复合换算 = min(复合换算), max(复合换算)
        min_节奏换算, max_节奏换算 = min(节奏换算), max(节奏换算)

        # Stage E: 各维度归一化 [0, 15.5]
        体力 = [cls._normalize(体力换算[i], min_体力换算, max_体力换算) for i in range(n)]
        手速 = [cls._normalize(手速换算[i], 0.0, max_手速换算) for i in range(n)]
        爆发 = [cls._normalize(爆发换算[i], 0.0, max_爆发换算) for i in range(n)]
        复合 = [cls._normalize(复合换算[i], min_复合换算, max_复合换算) for i in range(n)]
        节奏 = [cls._normalize(节奏换算[i], min_节奏换算, max_节奏换算) for i in range(n)]

        # Stage F: 粗糙75定数 与 粗糙主定数
        粗糙75定数 = [
            math.sqrt((体力[i] * 体力[i] + 手速[i] * 手速[i] + 复合[i] * 复合[i]) / 3.0)
            for i in range(n)
        ]
        粗糙主定数 = [
            cls._calc_raw_main_constant(体力[i], 手速[i], 爆发[i], 复合[i], 节奏[i])
            for i in range(n)
        ]

        # Stage G: 75定数/主定数的归一分母
        max_粗糙75定数 = max(粗糙75定数)
        max_粗糙主定数 = max(粗糙主定数)

        # Stage H: 归一主定数（每谱，依赖 max_粗糙主定数）
        主定数跨度 = max_粗糙主定数 - 13.3
        归一主定数 = []
        for 粗糙 in 粗糙主定数:
            if 粗糙 > 13.3 and 主定数跨度 > 0:
                归一主定数.append(13.3 + (15.5 - 13.3) * (粗糙 - 13.3) / 主定数跨度)
            else:
                归一主定数.append(粗糙)

        # Stage I/J: 粗糙99定数 及其全局最大值
        粗糙99定数 = [
            cls._calc_raw_99_constant(
                体力[i], 手速[i], 爆发[i], 复合[i], 节奏[i], 归一主定数[i]
            )
            for i in range(n)
        ]
        max_粗糙99定数 = max(粗糙99定数)

        return {
            "min_体力换算": min_体力换算,
            "max_体力换算": max_体力换算,
            "max_手速换算": max_手速换算,
            "max_爆发换算": max_爆发换算,
            "min_复合占比换算": min_复合占比换算,
            "max_复合占比换算": max_复合占比换算,
            "min_复合换算": min_复合换算,
            "max_复合换算": max_复合换算,
            "min_节奏换算": min_节奏换算,
            "max_节奏换算": max_节奏换算,
            "max_粗糙75定数": max_粗糙75定数,
            "max_粗糙主定数": max_粗糙主定数,
            "max_粗糙99定数": max_粗糙99定数,
        }


# ===========================================================================
# CLI
# ===========================================================================

def main():
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="定数计算管线 — 将 tja_analysis 输出转换为最终定数",
    )
    parser.add_argument("file", nargs="?", help="charts JSON 文件（默认 stdin）")
    parser.add_argument("--json", action="store_true", default=True, help="JSON 输出 (默认)")
    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            charts_data = json.load(f)
    else:
        charts_data = json.load(sys.stdin)

    # 动态校准：从输入数据集自身推导全局参考值
    charts = [Chart.from_dict(c) for c in charts_data]
    datas = [ChartRawData.from_chart(c) for c in charts]
    ref_values = RatingPipeline.calibrate(datas)

    pipeline = RatingPipeline(ref_values)
    results = pipeline.compute_all(charts)

    output = []
    for i, r in enumerate(results):
        entry = r.as_dict()
        if charts and i < len(charts):
            c = charts[i]
            entry["course"] = c.course
            entry["branchType"] = c.branch_type
        output.append(entry)

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
