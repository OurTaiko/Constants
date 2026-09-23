from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rating import ChartRawData, RatingPipeline


class RatingCapsTests(unittest.TestCase):
    def setUp(self):
        self.data = [
            ChartRawData(stamina_raw=4, speed_raw=5, burst_raw=6,
                         complex_ratio=.01, rhythm_ratio=.03, total_notes=300),
            ChartRawData(stamina_raw=8, speed_raw=8, burst_raw=9,
                         complex_ratio=.1, rhythm_ratio=.1, total_notes=700),
            ChartRawData(stamina_raw=10, speed_raw=9, burst_raw=12,
                         complex_ratio=.3, rhythm_ratio=.2, total_notes=1200),
        ]

    def test_capped_dataset_maxima_normalize_to_15_5(self):
        ref = RatingPipeline.calibrate(self.data)
        for key, expected in (
            ("max_粗糙75定数", 15.4),
            ("max_粗糙主定数", 15.15),
            ("max_粗糙99定数", 15.0),
        ):
            with self.subTest(key=key):
                self.assertAlmostEqual(ref[key], expected)

        pipeline = RatingPipeline(ref)
        results = [pipeline.compute(data) for data in self.data]
        for field in ("sub_constant_1", "main_constant", "sub_constant_2"):
            with self.subTest(field=field):
                self.assertAlmostEqual(max(getattr(r, field) for r in results), 15.5)

    def test_99_calibration_uses_same_normalized_main_as_compute(self):
        with patch.object(
            RatingPipeline, "_calc_raw_99_constant",
            wraps=RatingPipeline._calc_raw_99_constant,
        ) as raw_99:
            ref = RatingPipeline.calibrate(self.data)
            calibration_calls = list(raw_99.call_args_list)
            raw_99.reset_mock()
            pipeline = RatingPipeline(ref)
            for data in self.data:
                pipeline.compute(data)
            self.assertEqual(raw_99.call_args_list, calibration_calls)


if __name__ == "__main__":
    unittest.main()
