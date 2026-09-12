"""Batch 校准参数的文件格式和读取校验。"""

import json
import math
from pathlib import Path


DEFAULT_PARAMETERS = "cached_computing_parameters.json"
SAMPLE_PARAMETERS = "cached_computing_parameters.sample.json"
REQUIRED_KEYS = (
    "min_体力换算", "max_体力换算", "max_手速换算", "max_爆发换算",
    "min_复合占比换算", "max_复合占比换算", "min_复合换算", "max_复合换算",
    "min_节奏换算", "max_节奏换算", "max_粗糙75定数", "max_粗糙主定数",
    "max_粗糙99定数",
)


def load_computing_parameters(path: Path) -> dict[str, float]:
    """读取完整的 13 个全局极值；缺失或损坏时不使用默认值代替。"""
    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"参数缓存不存在：{path}；请先运行 src/batch_workflow.py") from exc
    if not isinstance(values, dict):
        raise ValueError("参数缓存必须是包含 13 个 min/max 的 JSON 对象")
    for key in REQUIRED_KEYS:
        value = values.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            raise ValueError(f"参数缓存缺少有效数值：{key}；请重新运行 batch")
    for key in REQUIRED_KEYS:
        if key.startswith("min_") and values[key] > values["max_" + key[4:]]:
            raise ValueError(f"参数缓存的 min 大于 max：{key}；请重新运行 batch")
    return {key: values[key] for key in REQUIRED_KEYS}
