from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from batch_workflow import process_song
from tja_analysis import Chart


class MappingPathsTests(unittest.TestCase):
    def test_mapping_separators_can_read_the_same_local_chart(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "Songs"
            chart_path = base / "分类" / "Lightning Beams" / "谱面.tja"
            chart_path.parent.mkdir(parents=True)
            content = "TITLE:Lightning Beams\n#START\n1111,\n#END\n"
            chart_path.write_text(content, encoding="utf-8")
            chart = Chart(course="oni")

            for relative in (
                r"分类\Lightning Beams\谱面.tja",
                "分类/Lightning Beams/谱面.tja",
                r"分类\Lightning Beams/谱面.tja",
            ):
                with self.subTest(relative=relative):
                    analyzer = Mock()
                    analyzer.analyze.return_value = {"charts": []}
                    analyzer.process.return_value = [chart]
                    result = process_song(
                        analyzer, "1", relative, str(base),
                        Path(directory) / "cache", use_cache=False,
                        algorithm_version="test",
                    )
                    self.assertNotIn("error", result)
                    self.assertEqual(result["charts"], [chart])
                    analyzer.analyze.assert_called_once_with(content)


if __name__ == "__main__":
    unittest.main()
