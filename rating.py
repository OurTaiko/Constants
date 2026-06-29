#!/usr/bin/env python3
"""
定数计算管线 — 实现 rating.xlsx「拉表」的运算逻辑
====================================================

第二阶段模块：将 tja_analysis 产出的原始算法值转换为最终 8 个输出字段。
完整工作流请使用 workflow.py（自动串联第一阶段 + 第二阶段）。

用法:
    from tja_analysis import TJAChartAnalyzer
    from rating import RatingPipeline

    analyzer = TJAChartAnalyzer()
    charts = analyzer.analyze_and_process(tja_content)

    pipeline = RatingPipeline()
    results = pipeline.compute_all(charts)
    for r in results:
        print(r.sub_constant_1, r.main_constant, r.sub_constant_2)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 参考值 — 从 rating.xlsx 全量数据集中提取的 MIN/MAX
# 用于将各维度的换算值归一化到 [0, 15.5] 区间
# ---------------------------------------------------------------------------
REF_VALUES: Dict[str, float] = {
    "min_L": 0.0,
    "max_L": 15.5,
    "max_M": 15.5,
    "max_N": 15.5,
    "min_O": 0.027989,
    "max_O": 15.623302,
    "min_Q": 0.0,
    "max_Q": 15.5,
    "min_T": 0.034796,
    "max_T": 15.547332,
    "max_Z": 15.407059,
    "max_AX": 15.167469,
    "max_BO": 15.004055,
}


# ===========================================================================
# 数据结构
# ===========================================================================


@dataclass
class ChartRawData:
    """单个谱面的原始数据，来自 workflow.py 的算法输出。

    对应 rating.xlsx 中的 D-K 列（totalNotes + 7 个算法原始值）:
        totalNotes, stamina, speed, burst, complex, complexRatio, rhythm, rhythmRatio
    """

    course: str = ""
    difficulty: str = ""
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
        difficulty: str,
        branch_type: str,
        ratings: dict,
    ) -> "ChartRawData":
        """从 workflow.py 的 calculate_difficulty_ratings 输出构造。"""
        return cls(
            course=course,
            difficulty=difficulty,
            branch_type=branch_type,
            total_notes=ratings.get("totalNotes", 0),
            stamina_raw=ratings.get("stamina", 0.0),
            speed_raw=ratings.get("speed", 0.0),
            burst_raw=ratings.get("burst", 0.0),
            complex_raw=ratings.get("complex", 0.0),
            complex_ratio=ratings.get("complexRatio", 0.0),
            rhythm_raw=ratings.get("rhythm", 0.0),
            rhythm_ratio=ratings.get("rhythmRatio", 0.0),
        )


@dataclass
class ChartConstantResult:
    """最终 8 个输出字段，对应 rating.xlsx 的 DQ-DX 列。"""

    sub_constant_1: float = 0.0  # 75定数
    main_constant: float = 0.0  # 主定数
    sub_constant_2: float = 0.0  # 99定数
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
    """实现 rating.xlsx「拉表」的完整定数推导管线。

    输入:  ChartRawData（原始算法值 + 总 note 数）
    输出:  ChartConstantResult（8 个最终字段）
    """

    def __init__(self, ref_values: Optional[Dict[str, float]] = None):
        self.ref: Dict[str, float] = ref_values or REF_VALUES

    # ------------------------------------------------------------------
    # Step 1: 原始值 → 换算值 (L-T 列)
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_stamina(raw: float) -> float:
        """L: 体力换算 — sigmoid 映射到 [0, 15.5]"""
        val = 16.3783 / (1.0 + math.exp(-0.6764 * (raw - 7.2836))) - 0.6012
        return max(min(val, 15.5), 0.0)

    @staticmethod
    def _convert_speed(raw: float) -> float:
        """M: 手速换算 — 分段函数"""
        if raw < 5.0:
            return 3.0 * (raw / 5.0) ** (35.0 / 3.0)
        if raw < 6.0:
            return 7.0 * raw - 32.0
        if raw < 10.0:
            return 11.0 / 8.0 * raw + 1.75
        return 15.5

    @staticmethod
    def _convert_burst(raw: float) -> float:
        """N: 爆发换算 — 分段函数"""
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
        """O: 复合占比换算 — tanh 映射"""
        return 18200.736 * math.tanh(4.491 * ratio + 3.86) - 18184.558

    @staticmethod
    def _complex_upper(total_notes: int) -> float:
        """P: 复合上限 — 基于总 note 数的 sigmoid 上限"""
        return 17.7743 / (1.0 + math.exp(-0.0083 * total_notes + 2.8484)) - 0.9613

    def _convert_complex(self, ratio_converted: float, upper: float) -> float:
        """Q: 复合换算 — 归一化后取 min 与上限"""
        r = self.ref
        normalized = (ratio_converted - r["min_O"]) / (r["max_O"] - r["min_O"]) * 15.5
        return min(normalized, upper)

    @staticmethod
    def _convert_rhythm_ratio(ratio: float) -> float:
        """R: 节奏占比换算 — sigmoid 映射"""
        return 20.1353 / (1.0 + math.exp(-18.0625 * (ratio - 0.0692))) - 4.4496

    @staticmethod
    def _rhythm_upper(total_notes: int) -> float:
        """S: 节奏上限 — 基于总 note 数的 sigmoid 上限"""
        return 17.4097 / (1.0 + math.exp(-0.007 * total_notes + 2.7059)) - 1.0787

    @staticmethod
    def _convert_rhythm(ratio_converted: float, upper: float) -> float:
        """T: 节奏换算 — min(ratio, upper)"""
        return min(ratio_converted, upper)

    # ------------------------------------------------------------------
    # Step 2: 归一化到 [0, 15.5] (U-Y 列)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(val: float, min_val: float, max_val: float) -> float:
        if max_val == min_val:
            return 0.0
        return (val - min_val) / (max_val - min_val) * 15.5

    # ------------------------------------------------------------------
    # Step 3: 75定数 / sub_constant_1 (AA 列)
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_75_constant(U: float, V: float, X: float, max_Z: float) -> float:
        Z = math.sqrt((U * U + V * V + X * X) / 3.0)
        return 15.5 * Z / max_Z

    # ------------------------------------------------------------------
    # Step 4: 主定数 / main_constant (AY 列)
    # ------------------------------------------------------------------

    def _calc_main_constant(
        self, U: float, V: float, W: float, X: float, Y: float
    ) -> float:
        r = self.ref

        # AB: 粗略值
        min_uvwxy = min(U, V, W, X, Y)
        sum_sq = U * U + V * V + W * W + X * X + Y * Y
        AB = math.sqrt((sum_sq - 0.9 * min_uvwxy * min_uvwxy) / 4.1)

        # AH-AM: 条件因子
        AH = 0.5 * math.tanh(1.0 * (AB - 10.0)) + 0.5
        AI = 0.5 * math.tanh(3.0 * (U - AB + 0.5)) + 0.5
        AJ = 0.5 * math.tanh(3.0 * (U - 14.5)) + 0.5
        AK = 0.5 * math.tanh(3.0 * (W - AB + 0.5)) + 0.5
        AL = 0.5 * math.tanh(3.0 * (X - AB + 0.5)) + 0.5
        AM = 0.5 * math.tanh(3.0 * (Y - AB + 0.5)) + 0.5

        # AN-AR: 条件判断
        AN = AH * AI * (1.0 - AJ)
        AO = AH
        AP = 1.0 - AK
        AQ = AH * AL
        AR = AH * AM

        # AC-AG: 主定数权重
        if min_uvwxy == U:
            AC = 0.1
        else:
            AC = 0.7 * (1.0 - AN) + 0.3

        if min_uvwxy == V:
            AD = 0.9 * AO + 0.1
        else:
            AD = 1.0

        if min_uvwxy == W:
            AE = 0.1
        else:
            AE = 0.9 * AP + 0.1

        if min_uvwxy == X:
            AF = 0.1
        else:
            AF = 0.9 * (1.0 - AQ) + 0.1

        if min_uvwxy == Y:
            AG = 0.1
        else:
            AG = 0.9 * (1.0 - AR) + 0.1

        # AX: 加权 RMS
        squared = [U * U, V * V, W * W, X * X, Y * Y]
        weights = [AC, AD, AE, AF, AG]
        sum_product = sum(s * w for s, w in zip(squared, weights))
        sum_weights = sum(weights)
        AX = (
            math.sqrt(sum_product / sum_weights) if sum_weights > 0.0 else 0.0
        )

        # AY: 13.3 软上限
        if AX > 13.3:
            return 13.3 + (15.5 - 13.3) * (AX - 13.3) / (r["max_AX"] - 13.3)
        return AX

    # ------------------------------------------------------------------
    # Step 5: 99定数 / sub_constant_2 (BR 列)
    # ------------------------------------------------------------------

    def _calc_99_constant(
        self, U: float, V: float, W: float, X: float, Y: float, AY: float
    ) -> float:
        r = self.ref

        # CN: 主定数范围
        if AY > 14.5:
            CN = 1
        elif AY > 13.5:
            CN = 2
        elif AY > 12.5:
            CN = 3
        elif AY > 11.5:
            CN = 4
        elif AY > 9.5:
            CN = 5
        else:
            CN = 6

        # 先算出主定数权重 AC-AF（简化：仅计算 99 定数需要的最小集合）
        # --- 这些值在 _calc_main_constant 中已算过，此处独立重算以避免耦合 ---
        sum_sq_all = U * U + V * V + W * W + X * X + Y * Y
        min_uvwxy = min(U, V, W, X, Y)
        AB = math.sqrt((sum_sq_all - 0.9 * min_uvwxy * min_uvwxy) / 4.1)

        AH = 0.5 * math.tanh(1.0 * (AB - 10.0)) + 0.5
        AI = 0.5 * math.tanh(3.0 * (U - AB + 0.5)) + 0.5
        AJ = 0.5 * math.tanh(3.0 * (U - 14.5)) + 0.5
        AK = 0.5 * math.tanh(3.0 * (W - AB + 0.5)) + 0.5
        AL = 0.5 * math.tanh(3.0 * (X - AB + 0.5)) + 0.5
        AM = 0.5 * math.tanh(3.0 * (Y - AB + 0.5)) + 0.5

        AN = AH * AI * (1.0 - AJ)
        AO = AH
        AP = 1.0 - AK
        AQ = AH * AL
        AR = AH * AM

        if min_uvwxy == U:
            AC = 0.1
        else:
            AC = 0.7 * (1.0 - AN) + 0.3
        if min_uvwxy == V:
            AD = 0.9 * AO + 0.1
        else:
            AD = 1.0
        if min_uvwxy == W:
            AE = 0.1
        else:
            AE = 0.9 * AP + 0.1
        if min_uvwxy == X:
            AF = 0.1
        else:
            AF = 0.9 * (1.0 - AQ) + 0.1
        if min_uvwxy == Y:
            AG = 0.1
        else:
            AG = 0.9 * (1.0 - AR) + 0.1

        # CO-CT: 99定数条件因子
        CO = 0.5 * math.tanh(3.0 * (U - 14.0)) + 0.5
        CP = 0.5 * math.tanh(3.0 * (U - 13.5)) + 0.5
        CQ_val = 0.5 * math.tanh(3.0 * (V - 11.0)) + 0.5
        CR = 0.5 * math.tanh(3.0 * (W - 15.0)) + 0.5
        CS = 0.5 * math.tanh(3.0 * (W - 8.5)) + 0.5
        CT = 0.5 * math.tanh(3.0 * (Y - AY)) + 0.5

        # BE-BI: 99定数权重
        if CN == 3:
            BE = 1.0 * CO + AC * (1.0 - CO)
        elif CN == 4:
            BE = 1.0 * CP + AC * (1.0 - CP)
        elif CN in (5, 6):
            BE = 0.1
        else:
            BE = AC

        if CN == 5:
            BF = 0.5 * CQ_val + 0.5
        elif CN == 6:
            BF = 0.9 * CQ_val + 0.1
        else:
            BF = AD

        if CN == 1:
            BG = 1.0
        elif CN == 2:
            BG = 0.5
        elif CN == 3:
            BG = 0.5 * CR + AE * (1.0 - CR)
        elif CN == 4:
            BG = 0.5
        elif CN == 5:
            BG = 0.3 * CS + 0.5
        elif CN == 6:
            BG = 0.9 * CS + 0.1
        else:
            BG = AE

        if CN in (1, 2):
            BH = AF
        else:
            BH = 0.1

        if CN == 1:
            BI = 0.3
        elif CN == 2:
            BI = 0.5
        elif CN == 3:
            BI = 0.5
        elif CN == 4:
            BI = 0.8
        elif CN == 5:
            BI = 0.5 * CT + 0.3
        elif CN == 6:
            BI = 1.0
        else:
            BI = AG

        # BO: 加权 RMS (99定数)
        squared = [U * U, V * V, W * W, X * X, Y * Y]
        weights = [BE, BF, BG, BH, BI]
        sum_product = sum(s * w for s, w in zip(squared, weights))
        sum_weights = sum(weights)
        BO_val = (
            math.sqrt(sum_product / sum_weights) if sum_weights > 0.0 else 0.0
        )

        # BP: 13.3 软上限
        if BO_val > 13.3:
            BP = 13.3 + (15.5 - 13.3) * (BO_val - 13.3) / (r["max_BO"] - 13.3)
        else:
            BP = BO_val

        # BQ: 手速限制
        BQ_val = 1.0 / 8.0 * V * V + 10.0

        # BR: 取 min
        return min(BP, BQ_val)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def compute(self, data: ChartRawData) -> ChartConstantResult:
        """从原始算法值计算 8 个最终输出字段。"""
        r = self.ref

        # Step 1: 原始值 → 换算值
        L = self._convert_stamina(data.stamina_raw)
        M = self._convert_speed(data.speed_raw)
        N = self._convert_burst(data.burst_raw)
        O = self._convert_complex_ratio(data.complex_ratio)
        P = self._complex_upper(data.total_notes)
        Q = self._convert_complex(O, P)
        R = self._convert_rhythm_ratio(data.rhythm_ratio)
        S = self._rhythm_upper(data.total_notes)
        T = self._convert_rhythm(R, S)

        # Step 2: 归一化 [0, 15.5]
        U = self._normalize(L, r["min_L"], r["max_L"])
        V = self._normalize(M, 0.0, r["max_M"])
        W = self._normalize(N, 0.0, r["max_N"])
        X = self._normalize(Q, r["min_Q"], r["max_Q"])
        Y = self._normalize(T, r["min_T"], r["max_T"])

        # Step 3: 75定数 (sub_constant_1)
        sub1 = self._calc_75_constant(U, V, X, r["max_Z"])

        # Step 4: 主定数 (main_constant)
        main_c = self._calc_main_constant(U, V, W, X, Y)

        # Step 5: 99定数 (sub_constant_2)
        sub2 = self._calc_99_constant(U, V, W, X, Y, main_c)

        return ChartConstantResult(
            sub_constant_1=sub1,
            main_constant=main_c,
            sub_constant_2=sub2,
            stamina=U,
            handspeed=V,
            burst=W,
            complex=X,
            rhythm=Y,
            source=data,
        )

    def compute_from_chart(self, chart: dict) -> ChartConstantResult:
        """从 workflow.py process_analysis 输出的 chart dict 直接计算。"""
        r = chart.get("ratings", {})
        data = ChartRawData.from_workflow_ratings(
            course=chart.get("course", ""),
            difficulty=chart.get("difficulty", ""),
            branch_type=chart.get("branchType", "unbranched"),
            ratings=r,
        )
        return self.compute(data)

    def compute_all(self, charts: List[dict]) -> List[ChartConstantResult]:
        """批量计算多个谱面。"""
        return [self.compute_from_chart(c) for c in charts]


# ===========================================================================
# CLI
# ===========================================================================

def main():
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="定数计算管线 — 将 workflow.py 输出转换为最终定数",
    )
    parser.add_argument("file", nargs="?", help="workflow.py 的 JSON 输出文件（默认 stdin）")
    parser.add_argument("--json", action="store_true", default=True, help="JSON 输出 (默认)")
    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            charts_data = json.load(f)
    else:
        charts_data = json.load(sys.stdin)

    pipeline = RatingPipeline()
    results = pipeline.compute_all(charts_data)

    output = []
    for i, r in enumerate(results):
        entry = r.as_dict()
        if charts_data and i < len(charts_data):
            c = charts_data[i]
            entry["course"] = c.get("course", "")
            entry["branchType"] = c.get("branchType", "")
        output.append(entry)

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
