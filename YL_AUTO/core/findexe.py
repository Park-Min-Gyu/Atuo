# -*- coding: utf-8 -*-
"""HyperMesh(hmbatch.exe) / Abaqus(abaqus.bat) 실행 파일 자동 찾기.

찾는 순서
  1) config 에 적은 경로가 실제로 있으면 그대로 사용
  2) config 의 start_menu_dir (시작 메뉴 폴더) 안 바로가기(.lnk)의 대상 경로에서 설치 폴더 역추적
  3) 흔한 기본 설치 위치 (C:/Program Files/Altair/2019, C:/SIMULIA/Commands ...)
  4) PATH (abaqus)
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

HM_DEFAULTS = [
    "C:/Program Files/Altair/2019/hm/bin/win64/hmbatch.exe",
    "C:/Program Files/Altair/2019/hwdesktop/hm/bin/win64/hmbatch.exe",
    "D:/Program Files/Altair/2019/hm/bin/win64/hmbatch.exe",
    "C:/Altair/2019/hm/bin/win64/hmbatch.exe",
    "D:/Altair/2019/hm/bin/win64/hmbatch.exe",
]
ABQ_DEFAULTS = [
    "C:/SIMULIA/Commands/abaqus.bat",
    "C:/SIMULIA/Commands/abq2021.bat",
    "D:/SIMULIA/Commands/abaqus.bat",
    "D:/SIMULIA/Commands/abq2021.bat",
]


def _lnk_targets(folder: str) -> list[str]:
    """시작 메뉴 폴더 안 .lnk 의 TargetPath + Arguments (Windows PowerShell 사용)."""
    if not folder or os.name != "nt" or not Path(folder).exists():
        return []
    ps = (
        "$s=New-Object -ComObject WScript.Shell;"
        f"Get-ChildItem -LiteralPath '{folder}' -Recurse -Filter *.lnk | ForEach-Object {{"
        "$l=$s.CreateShortcut($_.FullName); Write-Output ($l.TargetPath + ' ' + $l.Arguments)}"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           timeout=60)
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def _paths_in(text: str) -> list[Path]:
    return [Path(m) for m in re.findall(r"[A-Za-z]:[\\/][^\"'<>|*?\r\n]*?\.(?:exe|bat|cmd)", text, re.I)]


def find_hmbatch(hm_cfg: dict) -> tuple[str | None, str]:
    exe = (hm_cfg or {}).get("exe", "auto")
    if exe and exe != "auto" and Path(exe).exists():
        return exe, "config"
    cands: list[Path] = []
    for t in _lnk_targets((hm_cfg or {}).get("start_menu_dir", "")):
        for p in _paths_in(t):
            parts = [x.lower() for x in p.parts]
            for key in ("hm", "hw", "hwdesktop", "hwsolvers", "hst", "hwx"):
                if key in parts:
                    root = Path(*p.parts[:parts.index(key)])
                    cands += [root / "hm/bin/win64/hmbatch.exe", root / "hwdesktop/hm/bin/win64/hmbatch.exe"]
            cands.append(p.parent / "hmbatch.exe")
    cands += [Path(x) for x in HM_DEFAULTS]
    for pat in ("C:/Program Files/Altair/*/hm/bin/win64/hmbatch.exe", "D:/Program Files/Altair/*/hm/bin/win64/hmbatch.exe"):
        cands += [Path(x) for x in sorted(glob.glob(pat))]
    for c in cands:
        if c.exists():
            return str(c).replace("\\", "/"), "auto"
    return None, "not found"


def find_abaqus(abq_cfg: dict) -> tuple[str | None, str]:
    cmd = (abq_cfg or {}).get("cmd", "auto")
    if cmd and cmd != "auto":
        if Path(cmd).exists() or shutil.which(cmd):
            return cmd, "config"
    cands: list[Path] = []
    ver = str((abq_cfg or {}).get("version", "2021"))
    for t in _lnk_targets((abq_cfg or {}).get("start_menu_dir", "")):
        for p in _paths_in(t):
            d = p.parent
            cands += [d / "abaqus.bat", d / f"abq{ver}.bat"]
            # ...\SIMULIA\EstProducts\2021\win_b64\code\bin\... -> ...\SIMULIA\Commands
            parts = [x.lower() for x in p.parts]
            if "simulia" in parts:
                root = Path(*p.parts[:parts.index("simulia") + 1])
                cands += [root / "Commands/abaqus.bat", root / f"Commands/abq{ver}.bat"]
    cands += [Path(x) for x in ABQ_DEFAULTS]
    for c in cands:
        if c.exists():
            return str(c).replace("\\", "/"), "auto"
    w = shutil.which("abaqus") or shutil.which(f"abq{ver}")
    if w:
        return w, "PATH"
    return None, "not found"
