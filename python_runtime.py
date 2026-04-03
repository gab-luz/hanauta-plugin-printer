#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _can_import_cups(python_bin: str, timeout: float = 2.0) -> bool:
    try:
        result = subprocess.run(
            [python_bin, "-c", "import cups"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def select_python_with_cups(preferred_python: str = "") -> str:
    candidates: list[str] = []
    if preferred_python:
        candidates.append(preferred_python)
    candidates.append(sys.executable)

    system_python = "/usr/bin/python3"
    if Path(system_python).exists():
        candidates.append(system_python)

    for name in ("python3", "python"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        path = str(candidate).strip()
        if not path or path in seen:
            continue
        seen.add(path)
        ordered.append(path)

    for candidate in ordered:
        if _can_import_cups(candidate):
            return candidate

    return preferred_python or sys.executable


def maybe_reexec_with_cups(script_path: Path, argv: list[str]) -> None:
    if os.environ.get("HANAUTA_PRINTER_NO_REEXEC", "") == "1":
        return
    current = sys.executable
    if _can_import_cups(current):
        return

    target = select_python_with_cups(current)
    if not target or target == current:
        return

    os.environ["HANAUTA_PRINTER_NO_REEXEC"] = "1"
    os.execv(target, [target, str(script_path), *argv])
