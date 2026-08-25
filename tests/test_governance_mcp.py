from __future__ import annotations

import io
import json
import unittest

from orchestrator import governance_mcp
from tests.test_governance import ready_source


class GovernanceMCPTests(unittest.TestCase):
    def test_tool_surface_is_pure_and_has_no_execution_or_approval(self):
        names = {item["name"] for item in governance_mcp.TOOLS}
        self.assertEqual(
            names,
            {
                "compile_work_contract",
                "propose_contract_resolution",
                "compile_bad_case_registry",
                "calibrate_inspector",
                "plan_delivery",
                "build_inspector_contexts",
                "build_delivery_handoff",
                "adjudicate_delivery",
                "get_integration_blueprint",
            },
        )
        self.assertFalse(any("approve" in name or "run" in name for name in names))

    def test_learning_tools_only_compile_evidence_and_never_grant_authority(self):
        registry = governance_mcp.call_tool(
            "compile_bad_case_registry", {"registry_id": "empty", "cases": []}
        )
        profile = governance_mcp.call_tool(
            "calibrate_inspector", {"lane_id": "security", "evaluations": []}
        )

        self.assertEqual(registry["metrics"]["confirmed"], 0)
        self.assertEqual(profile["mode"], "shadow")
        self.assertFalse(profile["promotion"]["automatic"])

    def test_compile_and_route_return_structured_content(self):
        compiled = governance_mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "compile_work_contract", "arguments": ready_source()},
            }
        )
        content = compiled["result"]["structuredContent"]
        self.assertEqual(content["status"], "ready")

        routed = governance_mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "plan_delivery", "arguments": {"contract": content}},
            }
        )
        self.assertEqual(
            routed["result"]["structuredContent"]["contract_hash"],
            content["contract_hash"],
        )

    def test_invalid_tool_input_is_a_tool_error_not_a_server_crash(self):
        response = governance_mcp.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "compile_work_contract", "arguments": {}},
            }
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("goal is required", response["result"]["content"][0]["text"])

    def test_stdio_server_recovers_after_malformed_input(self):
        reader = io.StringIO(
            "not json\n"
            + json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"})
            + "\n"
        )
        writer = io.StringIO()
        governance_mcp.serve(reader, writer)
        frames = [json.loads(line) for line in writer.getvalue().splitlines()]
        self.assertEqual(frames[0]["error"]["code"], -32700)
        self.assertEqual(frames[1]["result"], {})


if __name__ == "__main__":
    unittest.main()
