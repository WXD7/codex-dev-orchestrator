#!/usr/bin/env python3
"""Compatibility shim for LobeHub 2.2.14 and current `codex exec resume`.

LobeHub 2.2.14 emits ``codex exec resume <options> <session> -`` while the
current Codex CLI requires ``codex exec <options> resume <session> -``.  This
file only reorders that fixed invocation.  Event conversion, server ingest,
process supervision and prompting remain owned by LobeHub's released harness.
"""

from __future__ import annotations

import os
import sys
from typing import List, Sequence


VALUE_FLAGS = {
    "-c",
    "--config",
    "-m",
    "--model",
    "-p",
    "--profile",
    "-s",
    "--sandbox",
    "-C",
    "--cd",
    "--add-dir",
    "--output-schema",
    "--color",
    "-o",
    "--output-last-message",
}
FORBIDDEN_VALUES = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "danger-full-access",
}


def reorder_resume_arguments(arguments: Sequence[str]) -> List[str]:
    args = list(arguments)
    if args[:2] != ["exec", "resume"]:
        return args
    options: List[str] = []
    position = 2
    while position < len(args) and args[position].startswith("-"):
        flag = args[position]
        options.append(flag)
        position += 1
        if flag in VALUE_FLAGS:
            if position >= len(args):
                raise ValueError("Missing value for Codex option %s" % flag)
            options.append(args[position])
            position += 1
    reordered = ["exec"] + options + ["resume"] + args[position:]
    flattened = " ".join(reordered)
    for forbidden in FORBIDDEN_VALUES:
        if forbidden in flattened:
            raise ValueError("Unsafe Codex argument was rejected: %s" % forbidden)
    return reordered


def main() -> int:
    real_binary = os.environ.get("ORCH_CODEX_REAL_BINARY", "").strip() or (
        "/Applications/ChatGPT.app/Contents/Resources/codex"
    )
    own_path = os.path.realpath(sys.argv[0])
    if os.path.realpath(real_binary) == own_path:
        raise RuntimeError("Codex resume shim cannot invoke itself")
    arguments = reorder_resume_arguments(sys.argv[1:])
    os.execvpe(real_binary, [real_binary] + arguments, os.environ)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
