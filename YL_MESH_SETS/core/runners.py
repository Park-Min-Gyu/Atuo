# -*- coding: utf-8 -*-
"""runners.py (메시+세트 전용 축소판) : 외부 프로그램 실행 도우미"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


def _p(path) -> str:
    """Tcl 에 넘길 경로는 슬래시로 통일"""
    return os.path.abspath(str(path)).replace("\\", "/")


def _run(cmd, cwd, log_file, timeout_s, shell=False):
    t0 = time.time()
    with open(log_file, "w", encoding="utf-8", errors="ignore") as lf:
        lf.write(f"# cmd: {cmd}\n# cwd: {cwd}\n")
        lf.flush()
        try:
            p = subprocess.run(cmd, cwd=str(cwd), stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               timeout=timeout_s, shell=shell)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        except FileNotFoundError as e:
            rc = f"not found: {e}"
    return rc, time.time() - t0


def _tail(path: Path, n: int) -> list:
    try:
        return [f"{Path(path).name}: {l}" for l in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]]
    except OSError:
        return []
