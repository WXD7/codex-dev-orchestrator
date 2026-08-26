from pathlib import Path
import unittest


class ObserverUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(__file__).with_name("bundle.js").read_text(encoding="utf-8")

    def test_realtime_and_history_are_visibly_separated(self):
        for required in (
            "Codex 实时 Hook",
            "Codex 持久化历史",
            "codex_history_agents",
            "历史存在绝不冒充实时运行",
            "只有 Kandev 原生事件和 Codex 实时 Hook",
        ):
            self.assertIn(required, self.source)

    def test_correction_and_folded_wait_are_rendered(self):
        self.assertIn('correction: "上级纠偏"', self.source)
        self.assertIn("同类事件累计 ", self.source)
        self.assertIn("repeat_count > 1", self.source)

    def test_subagent_stop_is_not_presented_as_success(self):
        self.assertIn('stopped: "已停止（结果未知）"', self.source)
        self.assertIn('label: "已停止待核验"', self.source)

    def test_polling_is_four_seconds(self):
        self.assertIn("POLL_INTERVAL_MS = 4000", self.source)


if __name__ == "__main__":
    unittest.main()
