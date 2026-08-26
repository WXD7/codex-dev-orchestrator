#!/usr/bin/env python3
"""Test, build, checksum, and package the Kandev Agent Observer plugin."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


PLUGIN_VERSION = "0.3.1"
PLUGIN_ID = "ai-delivery-agent-observer"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kandev-backend", type=Path)
    parser.add_argument("--go-bin", default="go", type=Path)
    parser.add_argument("--go-proxy")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--test-only", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument(
        "--reuse-binary-package",
        type=Path,
        help="Reuse server/plugin-darwin-arm64 from an already audited plugin package for a UI-only hotfix.",
    )
    return parser.parse_args()


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def write_modfile(root: Path, temporary: Path, backend: Path) -> Path:
    modfile = temporary / "observer.mod"
    content = (root / "go.mod").read_text(encoding="utf-8").rstrip()
    content += f"\n\nreplace github.com/kandev/kandev => {backend.resolve()}\n"
    modfile.write_text(content, encoding="utf-8")
    return modfile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_tar(output: Path, stage: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in sorted(stage.rglob("*")):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(stage).as_posix()
                    info = archive.gettarinfo(str(path), arcname=relative)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    backend = args.kandev_backend.resolve() if args.kandev_backend else None
    reuse_package = args.reuse_binary_package.resolve() if args.reuse_binary_package else None
    if reuse_package is None:
        if backend is None or not (backend / "go.mod").is_file() or not (backend / "pkg" / "pluginsdk").is_dir():
            raise SystemExit("--kandev-backend must point to a Kandev backend module")
    elif args.test_only:
        raise SystemExit("--test-only cannot be combined with --reuse-binary-package")
    go_bin = str(args.go_bin.resolve()) if args.go_bin.parent != Path(".") else str(args.go_bin)
    with tempfile.TemporaryDirectory(prefix="kandev-agent-observer-") as tmp_value:
        temporary = Path(tmp_value)
        env = os.environ.copy()
        env.update({"GOWORK": "off", "CGO_ENABLED": "0", "GOOS": "darwin", "GOARCH": "arm64"})
        if args.go_proxy:
            env["GOPROXY"] = args.go_proxy
        env.setdefault("GOCACHE", str(temporary / "go-build-cache"))
        env.setdefault("GOMODCACHE", str(temporary / "go-module-cache"))
        env["PYTHONPYCACHEPREFIX"] = str(temporary / "python-cache")
        if not args.skip_tests:
            run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "bridge/test_codex_app_server_bridge.py",
                    "bridge/test_codex_hook_receiver.py",
                    "ui/test_ui_contract.py",
                    "-v",
                ],
                root,
                env,
            )
        modfile = None
        if reuse_package is None:
            assert backend is not None
            modfile = write_modfile(root, temporary, backend)
            if not args.skip_tests:
                run([go_bin, "test", "-mod=mod", "-modfile", str(modfile), "./server"], root, env)
            if args.test_only:
                return

        stage = temporary / "stage"
        (stage / "server").mkdir(parents=True)
        (stage / "ui").mkdir(parents=True)
        (stage / "bridge").mkdir(parents=True)
        binary = stage / "server" / "plugin-darwin-arm64"
        if reuse_package is not None:
            if not reuse_package.is_file():
                raise SystemExit(f"reuse package does not exist: {reuse_package}")
            with tarfile.open(reuse_package, "r:gz") as archive:
                member = archive.getmember("server/plugin-darwin-arm64")
                source = archive.extractfile(member)
                if source is None:
                    raise SystemExit("reuse package has no server/plugin-darwin-arm64")
                binary.write_bytes(source.read())
        else:
            assert modfile is not None
            run(
                [go_bin, "build", "-mod=mod", "-modfile", str(modfile), "-trimpath", "-ldflags=-s -w", "-o", str(binary), "./server"],
                root,
                env,
            )
        binary.chmod(0o755)
        shutil.copy2(root / "manifest.yaml", stage / "manifest.yaml")
        shutil.copy2(root / "README.md", stage / "README.md")
        shutil.copy2(root / "CHANGELOG.md", stage / "CHANGELOG.md")
        shutil.copy2(root / "ui" / "bundle.js", stage / "ui" / "bundle.js")
        shutil.copy2(root / "ui" / "plugin.css", stage / "ui" / "plugin.css")
        shutil.copy2(root / "bridge" / "codex_app_server_bridge.py", stage / "bridge" / "codex_app_server_bridge.py")
        shutil.copy2(root / "bridge" / "codex_hook_receiver.py", stage / "bridge" / "codex_hook_receiver.py")

        payload_files = sorted(path for path in stage.rglob("*") if path.is_file())
        checksum_lines = [f"{sha256(path)}  {path.relative_to(stage).as_posix()}" for path in payload_files]
        (stage / "checksums.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

        output = args.output or root / "dist" / f"{PLUGIN_ID}-{PLUGIN_VERSION}-darwin-arm64.tar.gz"
        normalized_tar(output.resolve(), stage)
        print(output.resolve())


if __name__ == "__main__":
    main()
