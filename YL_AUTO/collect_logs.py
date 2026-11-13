# -*- coding: utf-8 -*-
"""연구실 PC 실행 결과 중 '점검용 파일만' 모아서 zip 으로 묶는다 (ODB 등 큰 파일 제외).
사용:  python collect_logs.py config_abaqus.json      ->  <work_dir>/YL_logs_YYYYmmdd_HHMM.zip
이 zip 하나만 보내주면 어느 단계에서 왜 멈췄는지 바로 볼 수 있다.
"""
import json
import sys
import time
import zipfile
from pathlib import Path

KEEP_EXT = {".tcl", ".msg", ".sta", ".dat", ".log", ".txt", ".json", ".md", ".csv", ".png", ".runlog"}
TAIL_EXT = {".msg", ".dat", ".tcl"}          # 큰 파일은 앞/뒤만
MAX_FULL = 2_000_000                 # 2 MB 넘으면 앞 300줄 + 뒤 600줄만


def _head_tail(p: Path) -> str:
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= 900:
        return "\n".join(lines)
    return "\n".join(lines[:300] + [f"... ({len(lines) - 900} lines skipped) ..."] + lines[-600:])


def main():
    cfg_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config_abaqus.json").resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    work = Path(cfg.get("work_dir", "auto_out"))
    work = work if work.is_absolute() else cfg_path.parent / work
    if not work.exists():
        print(f"[collect] work_dir 없음: {work}")
        return 1
    out = work / f"YL_logs_{time.strftime('%Y%m%d_%H%M')}.zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        _w, _ws, seen = z.write, z.writestr, set()

        def _write(src, arc):
            if arc not in seen:
                seen.add(arc)
                _w(src, arc)

        def _writestr(arc, data):
            if arc not in seen:
                seen.add(arc)
                _ws(arc, data)
        z.write, z.writestr = _write, _writestr
        z.write(cfg_path, cfg_path.name)
        # 폴더 상태 (모델을 어디에 넣었는지 확인용)
        base = cfg_path.parent
        lines = []
        for sub in ("models", "materials", "test/models"):
            d = base / sub
            lines.append(f"[{d}]")
            if d.exists():
                for q in sorted(d.iterdir()):
                    lines.append(f"  {q.name}  {q.stat().st_size} bytes")
            else:
                lines.append("  (폴더 없음)")
        z.writestr("folder_listing.txt", "\n".join(lines))
        for p in sorted(work.rglob("*")):
            if not p.is_file() or p == out or p.name.startswith("YL_logs_") or p.resolve() == cfg_path:
                continue
            ext = p.suffix.lower()
            arc = str(p.relative_to(work))
            if ext == ".inp":
                # 메시 INP 는 크니까 Job INP 만 (메시는 앞부분만)
                if p.name.endswith("_mesh.inp") or p.name.endswith("_hm.inp"):
                    z.writestr(arc + ".head.txt", "\n".join(
                        p.read_text(encoding="utf-8", errors="replace").splitlines()[:80]))
                else:
                    z.write(p, arc)
                n += 1
            elif ext in KEEP_EXT:
                if ext in TAIL_EXT and p.stat().st_size > MAX_FULL:
                    z.writestr(arc + ".headtail.txt", _head_tail(p))
                else:
                    z.write(p, arc)
                n += 1
    print(f"[collect] {n} files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
