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

    def test_codex_agents_are_grouped_by_root_task_and_show_waiting(self):
        for required in (
            "groupAgentsByRoot",
            "所属 Codex 任务",
            "活跃 Codex 任务",
            'permission: "等待权限审批"',
            'session_start: "主任务开始 / 恢复"',
            'session_end: "主任务结束"',
            'agent.execution_state === "waiting_on_human"',
        ):
            self.assertIn(required, self.source)

    def test_bridge_is_event_driven_and_idle_is_not_presented_as_connected(self):
        self.assertIn('active: "正在接收活跃任务事件"', self.source)
        self.assertIn('idle: "事件桥已就绪，目前没有活跃任务"', self.source)
        self.assertNotIn('connected: "数据通道已连接"', self.source)
        self.assertIn("ao-live-dot-active", self.source)
        self.assertIn("Hook 是事件驱动通道，不是长连接", self.source)
        self.assertIn('delivery_failed: "最近一次 Hook 投递失败"', self.source)

    def test_subagent_stop_is_not_presented_as_success(self):
        self.assertIn('stopped: "已停止（结果未知）"', self.source)
        self.assertIn('label: "已停止待核验"', self.source)

    def test_polling_is_four_seconds(self):
        self.assertIn("POLL_INTERVAL_MS = 4000", self.source)


if __name__ == "__main__":
    unittest.main()
