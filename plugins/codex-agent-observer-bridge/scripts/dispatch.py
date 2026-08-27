#!/usr/bin/env python3
"""Dispatch a Codex Hook event to the installed Kandev observer bridge.

The companion plugin fixes project-scope discovery without weakening Codex's
hook trust boundary.  It forwards a bounded stdin payload to the active,
validated Kandev observer receiver, using a minimal environment and never
persisting prompts, messages, tool input, tool output, or credentials itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Dict, Optional, Tuple


MAX_INPUT_BYTES = 1 << 20
MAX_RECEIVER_BYTES = 1 << 20
PLUGIN_ID = "ai-delivery-agent-observer"
HEALTH_PROTOCOL = "codex-hook-bridge-health/v1"
BINDING_PROTOCOL = "codex-hook-workspace-binding/v1"
RECEIVER_PROTOCOL = "codex-hooks-observer/v2"
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
MANIFEST_ID_RE = re.compile(r'(?m)^id:\s*"?([A-Za-z0-9_-]+)"?\s*$')
MANIFEST_VERSION_RE = re.compile(r'(?m)^version:\s*"?([0-9]+\.[0-9]+\.[0-9]+)"?\s*$')
ACTIVATION_FIELD_RE = re.compile(r"(?m)^([a-z_]+):\s*(.*?)\s*$")
SAFE_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class DispatchOutcome:
    success: bool
    reason_code: str
    workspace_id: str


def default_install_root() -> Path:
    return Path.home() / ".kandev" / "plugins" / PLUGIN_ID


def default_activation_path() -> Path:
    return Path.home() / ".kandev" / "plugins" / (PLUGIN_ID + ".yml")


def default_health_path() -> Path:
    return default_install_root() / "data" / "codex-hook-bridge-health.json"


def default_binding_path() -> Path:
    return default_install_root() / "data" / "codex-hook-binding.json"


def _version(value: str) -> Optional[Tuple[int, int, int]]:
    match = VERSION_RE.fullmatch(value)
    return None if match is None else tuple(int(part) for part in match.groups())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _declared_hash(checksums: Path, relative_path: str) -> str:
    if checksums.is_symlink() or not checksums.is_file() or checksums.stat().st_size > 64 * 1024:
        return ""
    for line in checksums.read_text(encoding="utf-8").splitlines():
        pieces = line.strip().split("  ", 1)
        if len(pieces) == 2 and re.fullmatch(r"[0-9a-f]{64}", pieces[0]) and pieces[1] == relative_path:
            return pieces[0]
    return ""


def _activation_fields(path: Path) -> Dict[str, str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        return {}
    result: Dict[str, str] = {}
    for key, raw_value in ACTIVATION_FIELD_RE.findall(path.read_text(encoding="utf-8")):
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result


def _receiver_protocol(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return ""
    return value.get("protocol_version", "") if isinstance(value, dict) else ""


def find_receiver(root: Path, activation_path: Optional[Path] = None) -> Optional[Path]:
    """Select only Kandev's active, checksum-verified, protocol-compatible receiver."""

    if root.is_symlink() or not root.is_dir():
        return None
    fields = _activation_fields(activation_path or default_activation_path())
    version = fields.get("version", "")
    if (
        fields.get("id") != PLUGIN_ID
        or fields.get("status") != "active"
        or _version(version) is None
    ):
        return None
    version_dir = root / version
    if version_dir.is_symlink() or not version_dir.is_dir():
        return None
    install_path = Path(fields.get("install_path", ""))
    if not install_path.is_absolute() or install_path != version_dir:
        return None
    manifest = version_dir / "manifest.yaml"
    receiver = version_dir / "bridge" / "codex_hook_receiver.py"
    protocol = version_dir / "bridge" / "codex_hook_protocol.json"
    checksums = version_dir / "checksums.txt"
    if (
        manifest.is_symlink()
        or not manifest.is_file()
        or manifest.stat().st_size > 64 * 1024
        or receiver.is_symlink()
        or not receiver.is_file()
        or receiver.stat().st_size > MAX_RECEIVER_BYTES
    ):
        return None
    text = manifest.read_text(encoding="utf-8")
    manifest_id = MANIFEST_ID_RE.search(text)
    manifest_version = MANIFEST_VERSION_RE.search(text)
    if (
        manifest_id is None
        or manifest_id.group(1) != PLUGIN_ID
        or manifest_version is None
        or manifest_version.group(1) != version
        or _receiver_protocol(protocol) != RECEIVER_PROTOCOL
    ):
        return None
    receiver_hash = _declared_hash(checksums, "bridge/codex_hook_receiver.py")
    protocol_hash = _declared_hash(checksums, "bridge/codex_hook_protocol.json")
    if (
        not receiver_hash
        or _sha256(receiver) != receiver_hash
        or not protocol_hash
        or _sha256(protocol) != protocol_hash
    ):
        return None
    return receiver


def minimal_environment() -> Dict[str, str]:
    return {
        "HOME": str(Path.home()),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _workspace_id_from_environment() -> str:
    value = os.environ.get("KANDEV_AGENT_OBSERVER_WORKSPACE_ID", "").strip()
    return value if SAFE_SCOPE.fullmatch(value) else ""


def _workspace_id_from_binding(path: Path) -> str:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return ""
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return ""
    if not isinstance(value, dict) or value.get("protocol_version") != BINDING_PROTOCOL:
        return ""
    workspace_id = value.get("kandev_workspace_id", "")
    return workspace_id if isinstance(workspace_id, str) and SAFE_SCOPE.fullmatch(workspace_id) else ""


def workspace_id_from_configuration(binding_path: Optional[Path] = None) -> str:
    return _workspace_id_from_environment() or _workspace_id_from_binding(
        binding_path or default_binding_path()
    )


def dispatch(
    raw: bytes,
    install_root: Optional[Path] = None,
    activation_path: Optional[Path] = None,
    workspace_id: Optional[str] = None,
    binding_path: Optional[Path] = None,
) -> DispatchOutcome:
    scope = (
        workspace_id_from_configuration(binding_path)
        if workspace_id is None
        else workspace_id.strip()
    )
    if not SAFE_SCOPE.fullmatch(scope):
        return DispatchOutcome(False, "workspace_unbound", "")
    if len(raw) > MAX_INPUT_BYTES:
        return DispatchOutcome(False, "input_too_large", scope)
    receiver = find_receiver(install_root or default_install_root(), activation_path)
    if receiver is None:
        return DispatchOutcome(False, "receiver_unavailable", scope)
    completed = subprocess.run(
        ["/usr/bin/python3", str(receiver), "--kandev-workspace-id", scope],
        input=raw,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=minimal_environment(),
        timeout=2.5,
        check=False,
    )
    if completed.returncode != 0:
        return DispatchOutcome(False, "receiver_failed", scope)
    return DispatchOutcome(True, "", scope)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_health(outcome: DispatchOutcome, path: Optional[Path] = None) -> None:
    target = (path or default_health_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    value = {
        "protocol_version": HEALTH_PROTOCOL,
        "observed_at": _utc_now(),
        "state": "ready" if outcome.success else "delivery_failed",
        "reason_code": outcome.reason_code,
        "kandev_workspace_id": outcome.workspace_id,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=".codex-hook-health-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(target))
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    try:
        outcome = dispatch(raw)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        outcome = DispatchOutcome(False, "dispatcher_error", workspace_id_from_configuration())
    try:
        write_health(outcome)
    except (OSError, UnicodeError, ValueError):
        pass
    # SubagentStop requires JSON stdout.  Never inject receiver output or errors
    # into the model-visible hook channel.
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
