# -*- coding: utf-8 -*-
"""[v27.4] INP 검사 / HyperMesh 오류 줄 확인 / 예전 INP 고치기

  python check_inp.py
      -> config_abaqus.json 의 work_dir(보통 auto_out) 아래 모든 INP 를 검사
  python check_inp.py <폴더 또는 INP 파일>
      -> 그 폴더(하위 폴더 포함) 또는 그 파일만 검사
  python check_inp.py <INP 파일> 7639 7641 7730
      -> 그 줄의 내용과 어느 키워드 블록인지 보여 줌 (HyperMesh 'Fatal parsing error on line N' 의 N)
  python check_inp.py [폴더] --fix
      -> 지수 표기 숫자(8e-06, 1e-08 ...)를 고정소수점으로 바꿔 다시 저장.
         값은 바뀌지 않으므로 이미 끝난 해석 결과는 그대로 재사용된다 (해석 상태 파일의 지문도 같이 갱신).

결과는 화면과 <폴더>/inp_check_YYYYmmdd_HHMM.txt 에 쓴다 (collect_logs.py 가 같이 모음).
"""
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from core import inp_check  # noqa: E402

INC_RE = re.compile(rb"^\*Include,\s*input=([^\r\n]+)", re.I | re.M)


def _default_target() -> Path:
    cfg_path = HERE / "config_abaqus.json"
    work = "auto_out"
    try:
        work = json.loads(cfg_path.read_text(encoding="utf-8")).get("work_dir", work)
    except (OSError, ValueError):
        pass
    p = Path(work)
    return p if p.is_absolute() else HERE / p


def _inp_files(target: Path):
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*.inp") if p.is_file())


def _includes(p: Path):
    try:
        head = p.read_bytes()[:20000]
    except OSError:
        return []
    return [(p.parent / m.decode(errors="ignore").strip().strip('"')).resolve() for m in INC_RE.findall(head)]


def main(argv):
    fix = "--fix" in argv
    args = [a for a in argv if a != "--fix"]
    target = Path(args[0]) if args else _default_target()
    linenos = [int(a) for a in args[1:] if a.isdigit()]
    if not target.exists():
        print(f"[check_inp] 없음: {target}")
        return 2
    out = []

    def say(s=""):
        print(s)
        out.append(s)

    if linenos:
        if not target.is_file():
            print("[check_inp] 줄 번호를 줄 때는 INP 파일 하나를 지정하세요")
            return 2
        say(inp_check.show_lines(target, linenos))
        say()
        inc = _includes(target)
        for ip in inc:
            say(f"(이 파일이 *Include 하는 {ip.name} 의 같은 줄 번호)")
            say(inp_check.show_lines(ip, linenos))
            say()

    files = _inp_files(target)
    included = {ip for f in files for ip in _includes(f)}
    roots = [f for f in files if f.resolve() not in included] or files
    say(f"[check_inp] {target} : INP {len(files)}개 (그중 다른 INP 가 *Include 하는 메시 {len(files) - len(roots)}개는 같이 검사)")

    # --fix 전에 해석 상태 파일의 지문(Job INP + 메시 INP)을 기록해 둔다
    before = {}
    if fix:
        for f in roots:
            stf = f.with_name(f.stem + "_status.json")
            if stf.exists():
                before[f] = inp_check.input_hash(f)

    n_bad_hm, n_bad = 0, 0
    fixed_files = 0
    for f in roots:
        t0 = time.time()
        rep = inp_check.check_inp(f)
        if not rep["hm_ok"]:
            n_bad_hm += 1
        if not rep["ok"]:
            n_bad += 1
        say(inp_check.format_report(rep) + f"  ({time.time() - t0:.1f}s)")
        if fix and rep["n_hm"]:
            for fp in dict.fromkeys(rep["files"]):
                n = inp_check.fix_numbers(fp)
                if n:
                    fixed_files += 1
                    say(f"   [고침] {Path(fp).name}: 지수 표기 숫자가 있던 {n}줄을 고정소수점으로 바꿈")
            rep2 = inp_check.check_inp(f)
            say(f"   [다시 검사] 지수 표기 {rep2['n_hm']}줄, 오류 {rep2['n_errors']}개")
    if fix and before:
        n_upd = 0
        for f, h0 in before.items():
            h1 = inp_check.input_hash(f)
            stf = f.with_name(f.stem + "_status.json")
            try:
                st = json.loads(stf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if h1 != h0 and st.get("input_hash") == h0:
                st["input_hash"] = h1
                stf.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
                n_upd += 1
        if n_upd:
            say(f"[check_inp] 해석 상태 파일 {n_upd}개의 입력 지문을 갱신함 -> 이미 끝난 해석은 다시 돌리지 않음")
    say()
    if n_bad == 0 and n_bad_hm == 0:
        say("[check_inp] 모든 INP 가 Abaqus / HyperMesh 에서 읽을 수 있는 형식입니다.")
    else:
        say(f"[check_inp] Abaqus 도 못 읽는 오류가 있는 INP {n_bad}개, HyperMesh 가 못 읽는 숫자 표기가 있는 INP {n_bad_hm}개"
            + (" -> 고쳤음" if fix and fixed_files else " -> python check_inp.py --fix 로 고칠 수 있음 (지수 표기만)"))
    rep_dir = target if target.is_dir() else target.parent
    rp = rep_dir / f"inp_check_{time.strftime('%Y%m%d_%H%M')}.txt"
    try:
        rp.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"[check_inp] 결과 저장: {rp}")
    except OSError:
        pass
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
