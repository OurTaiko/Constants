import contextlib
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import batch_workflow as batch
import single_workflow as single
from computing_parameters import DEFAULT_PARAMETERS, SAMPLE_PARAMETERS, load_computing_parameters
from rating import ChartRawData, RatingPipeline
from tja_analysis import Chart, ChartRatings


class CachedParametersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        previous = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, previous)
        self.charts = [
            Chart(course="hard", ratings=ChartRatings(
                stamina=4, speed=5, burst=6, complex_ratio=.01,
                rhythm_ratio=.03, total_notes=300)),
            Chart(course="oni", branch_type="master", ratings=ChartRatings(
                stamina=10, speed=9, burst=12, complex_ratio=.3,
                rhythm_ratio=.2, total_notes=1200)),
        ]
        self.tja = Path("谱面 sample.tja")
        self.tja.write_text("TITLE:测试\n#START\n1111,\n#END\n", encoding="utf-8-sig")
        self.ref = RatingPipeline.calibrate([ChartRawData.from_chart(c) for c in self.charts])

    def run_batch(self, *extra, failed=False):
        result = {"id": "1", "path": str(self.tja), "charts": self.charts, "source": "api"}
        if failed:
            result = {"id": "1", "path": str(self.tja), "error": "test failure"}
        with (
            patch.object(sys, "argv", ["batch", "--base-dir", ".", "--workers", "1", *extra]),
            patch.object(batch, "load_dotenv"),
            patch.object(batch, "fetch_ese_mapping", return_value={"1": str(self.tja)}),
            patch.object(batch, "process_song", return_value=result),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return batch.main()

    def run_single(self, path=None, *extra, charts=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["single", str(path or self.tja), *extra]),
            patch.object(single, "load_dotenv"),
            patch.object(single.TJAChartAnalyzer, "analyze_and_process",
                         return_value=self.charts if charts is None else charts) as analyze,
            patch.object(RatingPipeline, "calibrate", side_effect=AssertionError("must not calibrate")),
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr),
        ):
            code = single.main()
        return code, stdout.getvalue(), stderr.getvalue(), analyze

    def test_batch_cache_and_single_results_match_without_writes(self):
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(load_computing_parameters(Path(DEFAULT_PARAMETERS)), self.ref)
        expected = json.loads(Path("raw_constants.json").read_text(encoding="utf-8"))
        before = {p: p.read_bytes() for p in Path.cwd().rglob("*") if p.is_file()}
        for path in (self.tja, self.tja.resolve()):
            with self.subTest(path=path):
                code, stdout, stderr, analyze = self.run_single(path)
                self.assertEqual((code, stderr), (0, ""))
                analyze.assert_called_once_with(self.tja.read_bytes().decode("utf-8-sig"))
                output = json.loads(stdout)
                self.assertEqual(output["charts"], expected["songs"]["1"]["charts"])
                self.assertEqual(output["path"], str(self.tja.resolve()))
        after = {p: p.read_bytes() for p in Path.cwd().rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_sample_preserves_full_cache_and_can_be_selected(self):
        Path(DEFAULT_PARAMETERS).write_text("sentinel", encoding="utf-8")
        self.assertEqual(self.run_batch("--limit", "1"), 0)
        self.assertEqual(Path(DEFAULT_PARAMETERS).read_text(), "sentinel")
        self.assertEqual(load_computing_parameters(Path(SAMPLE_PARAMETERS)), self.ref)
        self.assertEqual(self.run_single(None, "--parameters", SAMPLE_PARAMETERS)[0], 0)

    def test_no_cache_still_exports_parameters(self):
        self.assertEqual(self.run_batch("--no-cache", "--parameters-output", "out/params.json"), 0)
        self.assertEqual(load_computing_parameters(Path("out/params.json")), self.ref)
        self.assertFalse(Path(".cache").exists())

    def test_failed_batch_preserves_parameters(self):
        Path(DEFAULT_PARAMETERS).write_text("sentinel", encoding="utf-8")
        self.assertEqual(self.run_batch(failed=True), 1)
        self.assertEqual(Path(DEFAULT_PARAMETERS).read_text(), "sentinel")

    def test_invalid_parameters_fail_before_api(self):
        invalid = [None, "broken JSON", "[]", "{}"]
        for value in (True, "1", float("nan"), float("inf")):
            invalid.append(json.dumps({**self.ref, "max_手速换算": value}))
        invalid.append(json.dumps({**self.ref, "min_体力换算": 100}))
        for content in invalid:
            with self.subTest(content=content):
                if content is not None:
                    Path(DEFAULT_PARAMETERS).write_text(content, encoding="utf-8")
                code, stdout, stderr, analyze = self.run_single()
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "")
                self.assertIn("计算失败", stderr)
                analyze.assert_not_called()

    def test_missing_tja_and_empty_charts(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_single("missing.tja")
        self.assertEqual(raised.exception.code, 2)
        batch.write_json_atomic(Path(DEFAULT_PARAMETERS), self.ref)
        code, stdout, stderr, _ = self.run_single(charts=[])
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("无可用谱面分支", stderr)

    def test_output_path_collision_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_batch("--parameters-output", "raw_constants.json")
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(Path("raw_constants.json").exists())

    def test_equal_complex_extrema_are_supported(self):
        chart = self.charts[0]
        ref = RatingPipeline.calibrate([ChartRawData.from_chart(chart)])
        result = RatingPipeline(ref).compute_from_chart(chart)
        self.assertEqual(result.complex, 0)
        self.assertTrue(all(math.isfinite(v) for v in result.as_dict().values()))


if __name__ == "__main__":
    unittest.main()
