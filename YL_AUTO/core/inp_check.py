# -*- coding: utf-8 -*-
"""
inp_check.py  [v27.4]
  Abaqus INP 를 Abaqus 와 HyperMesh 가 둘 다 읽을 수 있는지 줄 단위로 검사한다.

  오류 (Abaqus 도 못 돌리거나 결과가 틀림)
    - nan / inf / 숫자 자리에 숫자가 아닌 값
    - 절점/요소 번호 중복, 요소 절점 수가 요소 형식과 다름, 한 요소 안에 같은 절점 반복
    - 요소, 세트(*Nset/*Elset), *Boundary 가 없는 절점/세트를 참조
    - 줄 길이 256 자 초과, ASCII 가 아닌 문자, 탭, NUL
  HyperMesh 호환 (Abaqus 는 돌지만 HyperMesh 2019 가 'Fatal parsing error' 를 냄)
    - 지수 표기 숫자 (8e-06, -2.3e-05, 1e-08, 1.2E-05 ...)

  *Include 는 따라간다. 줄 번호는 파일마다 1 부터 센다 (HyperMesh 메시지의 줄 번호와 같다).
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path

RE_INT = re.compile(r"^[+-]?\d+$")
RE_FIX = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)$")
RE_EXP = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)[eEdD][+-]?\d+$")
RE_BAD = re.compile(r"^[+-]?(nan|inf|infinity|\d*\.?\d*#(qnan|ind|inf|snan)\d*)$", re.I)
RE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-\.]*$")

# 데이터 줄이 숫자로만 된 키워드 (정수/실수)
NUMERIC_KW = {"STATIC", "HYPERELASTIC", "ELASTIC", "DENSITY", "CONTROLS", "AMPLITUDE", "DYNAMIC", "VISCO",
              "EXPANSION", "CONDUCTIVITY", "SPECIFIC HEAT", "INITIAL CONDITIONS", "CLOAD", "DLOAD"}
FREE_TEXT_KW = {"HEADING", "PREPRINT", "PART", "END PART", "ASSEMBLY", "END ASSEMBLY", "INSTANCE", "END INSTANCE"}
MAX_LIST = 40          # 종류별로 보여 줄 줄 수


def classify(tok: str) -> str:
    t = tok.strip()
    if t == "":
        return "empty"
    if RE_INT.match(t):
        return "int"
    if RE_FIX.match(t):
        return "fix"
    if RE_EXP.match(t):
        return "exp"
    if RE_BAD.match(t):
        return "bad"
    return "name"


def nnode_of(etype: str):
    """요소 형식 -> 절점 수 (모르는 형식이면 None)"""
    t = etype.upper()
    m = re.match(r"^(?:DC3D|C3D|CAX|CPE|CPS|CPEG|S|SC|M3D|R3D|SFM3D)(\d+)", t)
    return int(m.group(1)) if m else None


def _kw_params(line: str):
    parts = [p.strip() for p in line[1:].split(",")]
    kw = re.sub(r"\s+", " ", parts[0].upper())
    prm = {}
    for p in parts[1:]:
        if not p:
            continue
        if "=" in p:
            k, v = p.split("=", 1)
            prm[re.sub(r"\s+", " ", k.strip().upper())] = v.strip()
        else:
            prm[re.sub(r"\s+", " ", p.strip().upper())] = ""
    return kw, prm


class _State:
    def __init__(self):
        self.errors, self.hm = [], []          # (파일, 줄, 메시지, 내용)
        self.n_err, self.n_hm = 0, 0
        self.kinds = {}
        self.nodes, self.elems = {}, {}         # id -> (파일, 줄)
        self.nsets, self.elsets = {}, {}        # 이름(대문자) -> [ids], [names]
        self.elem_refs = []                     # (파일, 줄, eid, [nids])
        self.set_refs = []                      # (파일, 줄, 'N'|'E', set이름, [ids], [names])
        self.bc_refs = []                       # (파일, 줄, node id or name)
        self.param_refs = []                    # (파일, 줄, 'N'|'E'|'NODE', 이름)
        self.files, self.n_lines, self.n_steps = [], 0, 0

    def err(self, f, ln, msg, text, kind):
        self.n_err += 1
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        if sum(1 for e in self.errors if e[4] == kind) < MAX_LIST:
            self.errors.append((f, ln, msg, text[:160], kind))

    def hmw(self, f, ln, msg, text, kind="지수 표기"):
        self.n_hm += 1
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        if len(self.hm) < MAX_LIST:
            self.hm.append((f, ln, msg, text[:160], kind))


def _check_tokens(st, fname, ln, raw, toks, want):
    """want : 'int' / 'real' / 'any' (숫자처럼 생긴 것만 검사)"""
    for t in toks:
        c = classify(t)
        if c == "exp":
            st.hmw(fname, ln, f"지수 표기 숫자 '{t.strip()}'", raw)
        elif c == "bad":
            st.err(fname, ln, f"숫자가 아님 (nan/inf) '{t.strip()}'", raw, "nan/inf")
        elif want == "int" and c not in ("int", "empty"):
            st.err(fname, ln, f"정수 자리에 '{t.strip()}'", raw, "숫자 형식")
        elif want == "real" and c == "name":
            st.err(fname, ln, f"숫자 자리에 '{t.strip()}'", raw, "숫자 형식")


def _parse_file(st: _State, path: Path, follow_include: bool, depth=0):
    fname = str(path)
    st.files.append(fname)
    data = path.read_bytes()
    if b"\x00" in data:
        st.err(fname, 0, "파일에 NUL 문자가 있음 (파일이 깨졌을 수 있음)", "", "파일 손상")
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    st.n_lines += len(lines)
    kw, prm = None, {}
    elem_n, pending, pend_ln, elset_name = None, [], 0, ""
    nset_name, set_kind, generate = "", "N", False
    for ln, raw in enumerate(lines, 1):
        if len(raw) > 256:
            st.err(fname, ln, f"줄 길이 {len(raw)} 자 (Abaqus 최대 256)", raw, "줄 길이")
        if "\t" in raw:
            st.err(fname, ln, "탭 문자", raw, "탭/비ASCII")
        if any(ord(ch) > 126 for ch in raw):
            st.err(fname, ln, "ASCII 가 아닌 문자", raw, "탭/비ASCII")
        s = raw.strip()
        if not s or s.startswith("**"):
            continue
        if s.startswith("*"):
            if kw == "ELEMENT" and pending:
                st.err(fname, pend_ln, f"요소 줄이 끝나지 않음 (값 {len(pending)}개)", lines[pend_ln - 1], "요소")
                pending = []
            kw, prm = _kw_params(s)
            if kw == "INCLUDE":
                inc = prm.get("INPUT", "").strip().strip('"')
                ip = Path(inc) if os.path.isabs(inc) else path.parent / inc
                if not ip.exists():
                    st.err(fname, ln, f"*Include 파일이 없음: {inc}", raw, "Include")
                elif follow_include and depth < 5:
                    _parse_file(st, ip, follow_include, depth + 1)
                kw = None
            elif kw == "ELEMENT":
                elem_n = nnode_of(prm.get("TYPE", ""))
                elset_name = prm.get("ELSET", "").upper()
                if elset_name:
                    st.elsets.setdefault(elset_name, ([], []))
            elif kw == "NODE":
                nset_name = prm.get("NSET", "").upper()
                if nset_name:
                    st.nsets.setdefault(nset_name, ([], []))
            elif kw in ("NSET", "ELSET"):
                set_kind = "N" if kw == "NSET" else "E"
                nset_name = prm.get(kw, "").upper()
                generate = "GENERATE" in prm
                (st.nsets if set_kind == "N" else st.elsets).setdefault(nset_name, ([], []))
            elif kw == "STEP":
                st.n_steps += 1
            # 키워드 매개변수가 가리키는 세트/절점
            for key, kind in (("ELSET", "E"), ("NSET", "N"), ("TIE NSET", "N"), ("REF NODE", "NODE")):
                if key in prm and kw not in ("ELEMENT", "NODE", "NSET", "ELSET"):
                    st.param_refs.append((fname, ln, kind, prm[key].upper()))
            continue
        if kw is None or kw in FREE_TEXT_KW:
            continue
        toks = s.split(",")
        if kw == "NODE":
            vals = [t for t in toks if t.strip() != ""]
            if len(vals) < 2:
                st.err(fname, ln, "절점 줄에 좌표가 없음", raw, "절점")
                continue
            _check_tokens(st, fname, ln, raw, vals[:1], "int")
            _check_tokens(st, fname, ln, raw, vals[1:], "real")
            if classify(vals[0]) == "int":
                nid = int(vals[0])
                if nid in st.nodes:
                    st.err(fname, ln, f"절점 번호 {nid} 중복 (처음: {Path(st.nodes[nid][0]).name} 줄 {st.nodes[nid][1]})",
                           raw, "번호 중복")
                else:
                    st.nodes[nid] = (fname, ln)
                if nset_name:
                    st.nsets[nset_name][0].append(nid)
        elif kw == "ELEMENT":
            _check_tokens(st, fname, ln, raw, toks, "int")
            vals = [int(t) for t in toks if classify(t) == "int"]
            if not pending:
                pend_ln = ln
            pending.extend(vals)
            cont = s.endswith(",")
            need = (elem_n + 1) if elem_n else None
            if need is None:
                if not cont and pending:
                    eid, cn = pending[0], pending[1:]
                    st.elem_refs.append((fname, pend_ln, eid, cn))
                    st.elems.setdefault(eid, (fname, pend_ln))
                    pending = []
                continue
            if len(pending) < need and cont:
                continue
            if len(pending) != need:
                st.err(fname, pend_ln, f"요소 값 개수 {len(pending)} (형식 {prm.get('TYPE')} 은 번호 + 절점 {elem_n}개)",
                       lines[pend_ln - 1], "요소")
                pending = []
                continue
            eid, cn = pending[0], pending[1:]
            if eid in st.elems:
                st.err(fname, pend_ln, f"요소 번호 {eid} 중복", lines[pend_ln - 1], "번호 중복")
            st.elems[eid] = (fname, pend_ln)
            if len(set(cn)) != len(cn):
                st.err(fname, pend_ln, f"요소 {eid} 안에 같은 절점이 반복됨", lines[pend_ln - 1], "요소")
            st.elem_refs.append((fname, pend_ln, eid, cn))
            if elset_name:
                st.elsets[elset_name][0].append(eid)
            pending = []
        elif kw in ("NSET", "ELSET"):
            vals = [t.strip() for t in toks if t.strip() != ""]
            _check_tokens(st, fname, ln, raw, vals, "any")
            ids = [int(v) for v in vals if classify(v) == "int"]
            names = [v.upper() for v in vals if classify(v) == "name"]
            if generate and len(ids) >= 2:
                a, b = ids[0], ids[1]
                stp = ids[2] if len(ids) > 2 and ids[2] else 1
                ids = list(range(a, b + 1, stp))
            tgt = (st.nsets if set_kind == "N" else st.elsets)[nset_name]
            tgt[0].extend(ids)
            tgt[1].extend(names)
            st.set_refs.append((fname, ln, set_kind, nset_name, ids, names))
        elif kw == "BOUNDARY":
            vals = [t.strip() for t in toks]
            _check_tokens(st, fname, ln, raw, vals[:1], "any")
            _check_tokens(st, fname, ln, raw, vals[1:3], "any")
            _check_tokens(st, fname, ln, raw, vals[3:4], "real")
            c0 = classify(vals[0])
            st.bc_refs.append((fname, ln, int(vals[0]) if c0 == "int" else vals[0].upper()))
        elif kw in NUMERIC_KW:
            _check_tokens(st, fname, ln, raw, [t for t in toks if t.strip() != ""], "real")
        else:
            _check_tokens(st, fname, ln, raw, toks, "any")
    if kw == "ELEMENT" and pending:
        st.err(fname, pend_ln, f"요소 줄이 끝나지 않음 (값 {len(pending)}개)", lines[pend_ln - 1], "요소")


def _check_refs(st: _State):
    nodes = st.nodes
    for f, ln, eid, cn in st.elem_refs:
        miss = [n for n in cn if n not in nodes]
        if miss:
            st.err(f, ln, f"요소 {eid} 가 없는 절점 {miss[:4]} 를 참조", "", "없는 절점 참조")
    for f, ln, kind, name, ids, names in st.set_refs:
        pool = nodes if kind == "N" else st.elems
        miss = [i for i in ids if i not in pool]
        if miss:
            st.err(f, ln, f"{'*Nset' if kind == 'N' else '*Elset'} {name} 이 없는 {'절점' if kind == 'N' else '요소'} "
                   f"{miss[:4]} 를 참조", "", "없는 절점 참조")
        sets = st.nsets if kind == "N" else st.elsets
        for nm in names:
            if nm not in sets:
                st.err(f, ln, f"세트 {name} 안의 이름 {nm} 이 정의되지 않음", "", "없는 세트 참조")
    for f, ln, ref in st.bc_refs:
        if isinstance(ref, int):
            if ref not in nodes:
                st.err(f, ln, f"*Boundary 가 없는 절점 {ref} 를 참조", "", "없는 절점 참조")
        elif ref not in st.nsets and ref not in ("ENCASTRE", "PINNED", "XSYMM", "YSYMM", "ZSYMM",
                                                   "XASYMM", "YASYMM", "ZASYMM"):
            st.err(f, ln, f"*Boundary 의 세트 {ref} 가 정의되지 않음", "", "없는 세트 참조")
    for f, ln, kind, name in st.param_refs:
        if kind == "NODE":
            if RE_INT.match(name):
                if int(name) not in nodes:
                    st.err(f, ln, f"ref node {name} 가 없는 절점", "", "없는 절점 참조")
            elif name not in st.nsets:
                st.err(f, ln, f"ref node 세트 {name} 가 정의되지 않음", "", "없는 세트 참조")
        elif kind == "N" and name not in st.nsets:
            st.err(f, ln, f"절점 세트 {name} 가 정의되지 않음", "", "없는 세트 참조")
        elif kind == "E" and name not in st.elsets:
            st.err(f, ln, f"요소 세트 {name} 가 정의되지 않음", "", "없는 세트 참조")


def check_inp(path, follow_include: bool = True, refs: bool = True) -> dict:
    """INP 1 개(+ *Include) 검사 -> 결과 dict.
    ok    : Abaqus 가 읽을 수 있음 (오류 0)
    hm_ok : HyperMesh 도 읽을 수 있음 (오류 0, 지수 표기 0)"""
    st = _State()
    _parse_file(st, Path(path), follow_include)
    if refs and follow_include:
        _check_refs(st)
    return {"file": str(path), "files": st.files, "n_lines": st.n_lines, "ok": st.n_err == 0,
            "hm_ok": st.n_err == 0 and st.n_hm == 0, "n_errors": st.n_err, "n_hm": st.n_hm,
            "kinds": st.kinds, "errors": st.errors, "hm_issues": st.hm,
            "n_nodes": len(st.nodes), "n_elems": len(st.elems), "n_steps": st.n_steps,
            "nsets": sorted(st.nsets), "elsets": sorted(st.elsets)}


def format_report(rep: dict, max_items: int = 15) -> str:
    out = [f"[INP 검사] {rep['file']}",
           f"  파일 {len(rep['files'])}개, {rep['n_lines']} 줄, 절점 {rep['n_nodes']}, 요소 {rep['n_elems']}, 스텝 {rep['n_steps']}"]
    if rep["hm_ok"]:
        out.append("  결과: 문제 없음 (Abaqus, HyperMesh 모두 읽을 수 있는 형식)")
        return "\n".join(out)
    out.append(f"  결과: 오류 {rep['n_errors']}개, HyperMesh 가 못 읽는 지수 표기 숫자 {rep['n_hm']}줄"
               + ("" if rep["ok"] else "  -> Abaqus 도 실패할 수 있음"))
    if rep["kinds"]:
        out.append("  종류별: " + ", ".join(f"{k} {v}" for k, v in rep["kinds"].items()))
    for f, ln, msg, text, kind in (rep["errors"] + rep["hm_issues"])[:max_items]:
        out.append(f"   - {Path(f).name} 줄 {ln}: {msg}" + (f"  | {text.strip()}" if text else ""))
    more = rep["n_errors"] + rep["n_hm"] - min(max_items, len(rep["errors"]) + len(rep["hm_issues"]))
    if more > 0:
        out.append(f"   ... 그 밖에 {more}건")
    return "\n".join(out)


def show_lines(path, linenos, context: int = 1) -> str:
    """지정한 줄 번호의 내용 + 그 줄이 속한 키워드 블록 (HyperMesh 'Fatal parsing error on line N' 확인용)"""
    p = Path(path)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    kw_at, cur, cur_ln = {}, "(키워드 없음)", 0
    want = sorted({int(n) for n in linenos})
    wi = 0
    for ln, raw in enumerate(lines, 1):
        s = raw.strip()
        if s.startswith("*") and not s.startswith("**"):
            cur, cur_ln = s[:70], ln
        while wi < len(want) and want[wi] == ln:
            kw_at[ln] = (cur, cur_ln)
            wi += 1
    out = [f"[줄 내용] {p}  (전체 {len(lines)} 줄)"]
    for n in want:
        if n < 1 or n > len(lines):
            out.append(f"  줄 {n}: 파일 범위 밖")
            continue
        k, kl = kw_at.get(n, ("?", 0))
        bad = [t.strip() for t in lines[n - 1].split(",") if classify(t) in ("exp", "bad")]
        out.append(f"  줄 {n}  (블록: {k}  @줄 {kl})" + (f"  <- 문제 숫자 {bad}" if bad else ""))
        for m in range(max(1, n - context), min(len(lines), n + context) + 1):
            out.append(f"   {'>>' if m == n else '  '} {m:7d}: {lines[m - 1][:150]}")
    return "\n".join(out)


def _fix_token(t: str) -> str:
    c = classify(t)
    if c != "exp":
        return t
    v = float(t.strip().replace("D", "E").replace("d", "e"))
    if not math.isfinite(v):
        return t
    if v == 0.0:
        s = "0."
    else:
        nd = min(max(12 - 1 - math.floor(math.log10(abs(v))), 0), 40)
        s = f"{v:.{nd}f}"
        s = s.rstrip("0") if "." in s else s + "."
        if s.startswith("-") and float(s) == 0.0:
            s = "0."
    lead = t[:len(t) - len(t.lstrip())]
    return lead + s


def fix_numbers(path) -> int:
    """지수 표기 숫자를 고정소수점(유효숫자 12개)으로 바꿔 다시 저장. 값은 그대로. 반환: 바꾼 줄 수.
    *Heading 같은 자유 문장 줄과 주석은 건드리지 않는다."""
    p = Path(path)
    raw = p.read_bytes()
    nl = "\r\n" if b"\r\n" in raw[:20000] else "\n"
    lines = raw.decode("utf-8", errors="replace").splitlines()
    kw, changed = None, 0
    for i, s0 in enumerate(lines):
        s = s0.strip()
        if not s or s.startswith("**"):
            continue
        if s.startswith("*"):
            kw = _kw_params(s)[0]
            continue
        if kw in FREE_TEXT_KW or kw is None:
            continue
        toks = s0.split(",")
        if any(classify(t) == "exp" for t in toks):
            lines[i] = ",".join(_fix_token(t) for t in toks)
            changed += 1
    if changed:
        tmp = p.with_name(p.name + ".tmp_fix")
        tmp.write_bytes((nl.join(lines) + nl).encode("ascii", errors="replace"))
        os.replace(tmp, p)
    return changed


def input_hash(job_inp: Path) -> str:
    """[v22] 해석 입력(Job INP + *Include 한 메시 INP)의 지문. 같으면 이전 해석 결과를 그대로 쓴다."""
    h = hashlib.sha1()
    try:
        txt = Path(job_inp).read_bytes()
    except OSError:
        return ""
    h.update(txt)
    m = re.search(rb"\*Include,\s*input=([^\r\n]+)", txt, re.I)
    if m:
        try:
            h.update((Path(job_inp).parent / m.group(1).decode(errors="ignore").strip().strip('"')).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:16]


def equivalent(old_text: str, new_text: str, tol: float = 1e-6) -> bool:
    """두 INP 글이 숫자 표기만 다르고 내용(값)은 같은지 (|차이| <= tol * max(1, |값|))."""
    a, b = old_text.splitlines(), new_text.splitlines()
    if len(a) != len(b):
        return False
    for la, lb in zip(a, b):
        if la == lb:
            continue
        ta, tb = la.split(","), lb.split(",")
        if len(ta) != len(tb):
            return False
        for x, y in zip(ta, tb):
            x, y = x.strip(), y.strip()
            if x == y:
                continue
            try:
                fx, fy = float(x), float(y)
            except ValueError:
                return False
            if not (math.isfinite(fx) and math.isfinite(fy)) or abs(fx - fy) > tol * max(1.0, abs(fx)):
                return False
    return True
