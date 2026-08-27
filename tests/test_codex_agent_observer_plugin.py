import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "codex-agent-observer-bridge"
MODULE_PATH = PLUGIN / "scripts" / "dispatch.py"
SPEC = importlib.util.spec_from_file_location("codex_agent_observer_dispatch", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dispatch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dispatch
SPEC.loader.exec_module(dispatch)


class CodexAgentObserverPluginTests(unittest.TestCase):
    def install_version(self, root: Path, version: str, receiver: bytes = b"print('{}')\n") -> Path:
        version_dir = root / version
        bridge = version_dir / "bridge"
        bridge.mkdir(parents=True)
        receiver_path = bridge / "codex_hook_receiver.py"
        receiver_path.write_bytes(receiver)
        protocol = bridge / "codex_hook_protocol.json"
        protocol.write_text(
            json.dumps({"protocol_version": dispatch.RECEIVER_PROTOCOL}) + "\n",
            encoding="utf-8",
        )
        receiver_digest = hashlib.sha256(receiver).hexdigest()
        protocol_digest = hashlib.sha256(protocol.read_bytes()).hexdigest()
        (version_dir / "manifest.yaml").write_text(
            'id: "ai-delivery-agent-observer"\nversion: "%s"\n' % version,
            encoding="utf-8",
        )
        (version_dir / "checksums.txt").write_text(
            receiver_digest
            + "  bridge/codex_hook_receiver.py\n"
            + protocol_digest
            + "  bridge/codex_hook_protocol.json\n",
            encoding="utf-8",
        )
        return receiver_path

    def activate(self, root: Path, version: str) -> Path:
        path = root.parent / "ai-delivery-agent-observer.yml"
        path.write_text(
            "id: ai-delivery-agent-observer\n"
            f"version: {version}\n"
            "status: active\n"
            f"install_path: {root / version}\n",
            encoding="utf-8",
        )
        return path

    def test_plugin_manifest_and_default_hook_path_are_valid_json(self):
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "codex-agent-observer-bridge")
        self.assertIn("defaultPrompt", manifest["interface"])
        self.assertNotIn("hooks", manifest)
        self.assertEqual(
            set(hooks["hooks"]),
            {
                "SessionStart",
                "SessionEnd",
                "SubagentStart",
                "SubagentStop",
                "PreToolUse",
                "PostToolUse",
                "PermissionRequest",
            },
        )
        encoded = json.dumps(hooks, ensure_ascii=False)
        self.assertIn("$PLUGIN_ROOT/scripts/dispatch.py", encoded)
        self.assertNotIn("/Users/", encoded)
        self.assertNotIn("kandev-workspace-id", encoded)
        for groups in hooks["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertEqual(
                        hook["command"],
                        '/usr/bin/python3 "$PLUGIN_ROOT/scripts/dispatch.py"',
                    )
        self.assertEqual(hooks["hooks"]["PreToolUse"][0]["matcher"], "*")
        self.assertEqual(hooks["hooks"]["PostToolUse"][0]["matcher"], "*")
        self.assertEqual(hooks["hooks"]["PermissionRequest"][0]["matcher"], "*")

    def test_only_active_checksum_verified_receiver_is_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.install_version(root, "0.3.0")
            newest = self.install_version(root, "0.3.1")
            tampered = self.install_version(root, "0.4.0")
            tampered.write_text("tampered", encoding="utf-8")
            activation = self.activate(root, "0.3.1")
            self.assertEqual(dispatch.find_receiver(root, activation), newest)

    def test_wrong_manifest_symlink_and_bad_checksum_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self.install_version(root, "0.3.1")
            activation = self.activate(root, "0.3.1")
            (candidate.parents[1] / "manifest.yaml").write_text(
                'id: "other-plugin"\nversion: "0.3.1"\n', encoding="utf-8"
            )
            self.assertIsNone(dispatch.find_receiver(root, activation))

            candidate.parents[1].rename(root / "wrong")
            candidate = self.install_version(root, "0.3.1")
            activation = self.activate(root, "0.3.1")
            (candidate.parents[1] / "checksums.txt").write_text(
                "0" * 64 + "  bridge/codex_hook_receiver.py\n", encoding="utf-8"
            )
            self.assertIsNone(dispatch.find_receiver(root, activation))

    def test_dispatch_uses_verified_receiver_and_does_not_inherit_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "capture.json"
            receiver = (
                "import json, os, sys\n"
                "from pathlib import Path\n"
                f"Path({str(capture)!r}).write_text(json.dumps({{"
                "'raw': sys.stdin.read(), 'environment': sorted(os.environ), 'argv': sys.argv[1:]"
                "}), encoding='utf-8')\n"
            ).encode("utf-8")
            self.install_version(root, "0.3.1", receiver)
            activation = self.activate(root, "0.3.1")
            raw = b'{"hook_event_name":"SubagentStart","agent_id":"probe"}'
            with patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "must-not-propagate", "OTHER_SECRET": "also-private"},
            ):
                outcome = dispatch.dispatch(raw, root, activation, "workspace-1")
                self.assertTrue(outcome.success)
            observed = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(observed["raw"], raw.decode("utf-8"))
            self.assertNotIn("DEEPSEEK_API_KEY", observed["environment"])
            self.assertNotIn("OTHER_SECRET", observed["environment"])
            self.assertEqual(observed["argv"], ["--kandev-workspace-id", "workspace-1"])

    def test_oversized_event_is_rejected_before_receiver_lookup(self):
        outcome = dispatch.dispatch(
            b"x" * (dispatch.MAX_INPUT_BYTES + 1),
            Path("/missing"),
            workspace_id="workspace-1",
        )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.reason_code, "input_too_large")

    def test_workspace_binding_is_required(self):
        outcome = dispatch.dispatch(b"{}", Path("/missing"), workspace_id="")
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.reason_code, "workspace_unbound")

    def test_workspace_binding_file_is_validated_and_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            binding = Path(temporary) / "binding.json"
            binding.write_text(
                json.dumps(
                    {
                        "protocol_version": dispatch.BINDING_PROTOCOL,
                        "kandev_workspace_id": "workspace-1",
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    dispatch.workspace_id_from_configuration(binding),
                    "workspace-1",
                )
            binding.write_text(
                json.dumps(
                    {
                        "protocol_version": dispatch.BINDING_PROTOCOL,
                        "kandev_workspace_id": "../../other-workspace",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(dispatch.workspace_id_from_configuration(binding), "")

    def test_health_file_is_private_and_contains_only_fixed_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "health.json"
            dispatch.write_health(
                dispatch.DispatchOutcome(False, "receiver_failed", "workspace-1"), path
            )
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["state"], "delivery_failed")
            self.assertEqual(value["reason_code"], "receiver_failed")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                set(value),
                {
                    "protocol_version",
                    "observed_at",
                    "state",
                    "reason_code",
                    "kandev_workspace_id",
                },
            )

    def test_minimal_environment_does_not_forward_credentials(self):
        environment = dispatch.minimal_environment()
        self.assertEqual(set(environment), {"HOME", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE"})
        self.assertNotIn("DEEPSEEK_API_KEY", environment)


if __name__ == "__main__":
    unittest.main()
