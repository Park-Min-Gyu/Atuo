#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atuo - 내 컴퓨터 자동 정리 도구 (Windows / macOS / Linux, 파이썬 표준 라이브러리만 사용)

  python atuo.py            메뉴로 실행
  python atuo.py scan       무엇을 지울 수 있는지 보기만 함 (아무것도 지우지 않음)
  python atuo.py clean      하나씩 물어보면서 정리
  python atuo.py apps       안 쓰는 앱 정리
  python atuo.py memory     메모리(RAM)를 많이 쓰는 프로그램 보기
  python atuo.py restore    보관함에 있는 파일 되살리기
  python atuo.py empty      보관함 비우기 (이때 실제로 용량이 확보됨)

안전 원칙
  - 다시 만들어지는 것(임시파일, 캐시, 빌드 산출물)만 바로 삭제한다.
  - 사용자의 파일(큰 파일, 오래된 다운로드, 중복 파일, 앱)은 반드시 물어보고,
    삭제 대신 보관함(~/.atuo/trash)으로 옮긴다. 30일 동안 restore 로 되살릴 수 있다.
  - 시스템 폴더, 홈 폴더 자체, .ssh 같은 중요한 경로는 절대 건드리지 않는다.
"""
import argparse
import codecs
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

VERSION = "1.0.0"
SYSTEM = platform.system()  # Windows / Darwin / Linux
HOME = Path.home()
DATA_DIR = HOME / ".atuo"
TRASH_DIR = DATA_DIR / "trash"
INDEX_FILE = TRASH_DIR / "index.json"
LOG_FILE = DATA_DIR / "history.log"
KEEP_DAYS = 30          # 보관함 보관 기간
DAY = 86400
MB = 1024 * 1024

# ───────────────────────── 화면 출력 ─────────────────────────

def _init_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if SYSTEM == "Windows":
        os.system("")  # ANSI 색상 켜기

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

def c(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else str(text)

def bold(t): return c(t, "1")
def dim(t): return c(t, "2")
def green(t): return c(t, "32")
def yellow(t): return c(t, "33")
def red(t): return c(t, "31")
def cyan(t): return c(t, "36")

def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024

def ago(ts):
    if not ts:
        return "알 수 없음"
    d = int((time.time() - ts) / DAY)
    if d < 1:
        return "오늘"
    if d < 30:
        return f"{d}일 전"
    if d < 365:
        return f"{d // 30}개월 전"
    return f"{d // 365}년 전"

def short(path, width=70):
    s = str(path)
    home = str(HOME)
    if s.startswith(home):
        s = "~" + s[len(home):]
    return s if len(s) <= width else "…" + s[-(width - 1):]

def header(title):
    print()
    print(bold(cyan(f"■ {title}")))

def ask(prompt, choices="yn", default="n"):
    """한 글자 답을 받는다. 입력을 받을 수 없으면 기본값(안전한 쪽)을 돌려준다."""
    hint = "/".join(ch.upper() if ch == default else ch for ch in choices)
    while True:
        try:
            ans = input(f"{prompt} [{hint}] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "q" if "q" in choices else default
        if not ans:
            return default
        if ans[0] in choices:
            return ans[0]

# ───────────────────────── 안전장치 ─────────────────────────

def _protected_roots():
    roots = {HOME, HOME.parent, Path(HOME.anchor), DATA_DIR}
    for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos",
                 "Movies", "Library", "AppData", "OneDrive", "바탕 화면", "문서"):
        roots.add(HOME / name)
    if SYSTEM == "Windows":
        for env in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            if os.environ.get(env):
                roots.add(Path(os.environ[env]))
    else:
        for p in ("/", "/bin", "/sbin", "/usr", "/etc", "/var", "/lib", "/opt", "/boot",
                  "/System", "/Library", "/Applications", "/Users", "/home", "/root"):
            roots.add(Path(p))
    return {os.path.normcase(os.path.abspath(str(r))) for r in roots}

PROTECTED = _protected_roots()
SENSITIVE_PARTS = {".ssh", ".gnupg", ".aws", ".kube", ".git"}

def is_protected(path):
    """True 이면 이 경로 자체는 절대 지우거나 옮기지 않는다."""
    p = os.path.normcase(os.path.abspath(str(path)))
    if p in PROTECTED:
        return True
    home = os.path.normcase(str(HOME))
    if home.startswith(p.rstrip(os.sep) + os.sep):  # 홈 폴더의 상위 폴더
        return True
    return any(part in SENSITIVE_PARTS for part in Path(p).parts)

def log(action, path, size=0):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{action}\t{size}\t{path}\n")
    except OSError:
        pass

# ───────────────────────── 파일 다루기 ─────────────────────────

def dir_size(path):
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total

def path_size(path):
    try:
        if os.path.islink(path):
            return 0
        if os.path.isdir(path):
            return dir_size(path)
        return os.stat(path).st_size
    except OSError:
        return 0

def delete_now(path):
    """되살릴 수 없게 삭제. 실제로 확보한 바이트 수를 돌려준다 (실패하면 0)."""
    if is_protected(path):
        return 0
    size = path_size(path)
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
            left = dir_size(path) if os.path.exists(path) else 0
            freed = size - left
        else:
            os.remove(path)
            freed = size
    except OSError:
        return 0
    if freed > 0:
        log("삭제", path, freed)
    return max(freed, 0)

# ───────────────────────── 보관함 (되살리기 가능) ─────────────────────────

def _load_index():
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

def _save_index(items):
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, INDEX_FILE)

def quarantine(path, reason):
    """파일/폴더를 보관함으로 옮긴다. 성공하면 크기를 돌려준다."""
    path = os.path.abspath(str(path))
    if is_protected(path) or not os.path.lexists(path):
        return 0
    size = path_size(path)
    item_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    dest_dir = TRASH_DIR / item_id
    try:
        dest_dir.mkdir(parents=True)
        shutil.move(path, str(dest_dir / os.path.basename(path)))
    except (OSError, shutil.Error) as e:
        print(red(f"   옮기지 못했습니다: {e}"))
        shutil.rmtree(dest_dir, ignore_errors=True)
        return 0
    items = _load_index()
    items.append({"id": item_id, "orig": path, "size": size,
                  "when": time.time(), "reason": reason})
    _save_index(items)
    log("보관함으로", path, size)
    return size

def purge_expired():
    items = _load_index()
    keep, freed = [], 0
    for it in items:
        if time.time() - it["when"] > KEEP_DAYS * DAY:
            shutil.rmtree(TRASH_DIR / it["id"], ignore_errors=True)
            freed += it["size"]
            log("보관기간 만료 삭제", it["orig"], it["size"])
        else:
            keep.append(it)
    if len(keep) != len(items):
        _save_index(keep)
    return freed

def cmd_restore(_opts=None):
    items = _load_index()
    header("보관함")
    if not items:
        print("  보관함이 비어 있습니다.")
        return
    for i, it in enumerate(items, 1):
        left = KEEP_DAYS - int((time.time() - it["when"]) / DAY)
        print(f"  {i:>3}. {human(it['size']):>9}  {short(it['orig'])}  "
              + dim(f"({it['reason']}, {left}일 뒤 자동 삭제)"))
    try:
        raw = input("\n되살릴 번호 (여러 개는 쉼표, 전부는 all, 취소는 Enter): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if not raw:
        return
    picks = set(range(len(items))) if raw == "all" else {
        int(x) - 1 for x in raw.replace(" ", "").split(",") if x.isdigit()}
    remain = []
    for i, it in enumerate(items):
        if i not in picks:
            remain.append(it)
            continue
        src_dir = TRASH_DIR / it["id"]
        try:
            src = next(src_dir.iterdir())
            dest = it["orig"]
            if os.path.lexists(dest):
                base, ext = os.path.splitext(dest)
                dest = f"{base} (복구됨){ext}"
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(str(src), dest)
            shutil.rmtree(src_dir, ignore_errors=True)
            log("복구", dest, it["size"])
            print(green(f"  되살림: {short(dest)}"))
        except (OSError, StopIteration, shutil.Error) as e:
            print(red(f"  실패: {short(it['orig'])} ({e})"))
            remain.append(it)
    _save_index(remain)

def cmd_empty(_opts=None):
    items = _load_index()
    total = sum(it["size"] for it in items)
    header("보관함 비우기")
    if not items:
        print("  보관함이 비어 있습니다.")
        return
    print(f"  {len(items)}개 항목, {bold(human(total))}. 비우면 되살릴 수 없습니다.")
    if ask("  정말 비울까요?") == "y":
        for it in items:
            shutil.rmtree(TRASH_DIR / it["id"], ignore_errors=True)
            log("보관함 비움", it["orig"], it["size"])
        _save_index([])
        print(green(f"  {human(total)} 확보했습니다."))

# ───────────────────────── 1. 임시파일 · 캐시 ─────────────────────────

def junk_targets():
    """(이름, 경로, 최소 경과일) 목록. 경로 안의 '내용물'만 지운다."""
    env = os.environ.get
    t = []
    if SYSTEM == "Windows":
        local = Path(env("LOCALAPPDATA", HOME / "AppData" / "Local"))
        t += [("임시 파일", Path(env("TEMP", local / "Temp")), 1),
              ("Windows 임시 파일", Path(env("SystemRoot", r"C:\Windows")) / "Temp", 1),
              ("오류 덤프", local / "CrashDumps", 0),
              ("pip 캐시", local / "pip" / "Cache", 0),
              ("npm 캐시", local / "npm-cache" / "_cacache", 0)]
        for name, base in (("Chrome", local / "Google" / "Chrome" / "User Data"),
                           ("Edge", local / "Microsoft" / "Edge" / "User Data")):
            if base.is_dir():
                for prof in base.iterdir():
                    for sub in ("Cache", "Code Cache", "GPUCache"):
                        t.append((f"{name} 캐시", prof / sub, 0))
        ff = local / "Mozilla" / "Firefox" / "Profiles"
        if ff.is_dir():
            t += [("Firefox 캐시", p / "cache2", 0) for p in ff.iterdir()]
    elif SYSTEM == "Darwin":
        lib = HOME / "Library"
        t += [("앱 캐시", lib / "Caches", 0),
              ("로그 파일", lib / "Logs", 7),
              ("Xcode 빌드 캐시", lib / "Developer" / "Xcode" / "DerivedData", 0),
              ("iOS 시뮬레이터 캐시", lib / "Developer" / "CoreSimulator" / "Caches", 0),
              ("휴지통", HOME / ".Trash", 0),
              ("npm 캐시", HOME / ".npm" / "_cacache", 0)]
    else:
        cache = Path(env("XDG_CACHE_HOME", HOME / ".cache"))
        t += [("앱 캐시", cache, 0),
              ("휴지통", HOME / ".local" / "share" / "Trash" / "files", 0),
              ("휴지통 정보", HOME / ".local" / "share" / "Trash" / "info", 0),
              ("npm 캐시", HOME / ".npm" / "_cacache", 0)]
    t.append(("Gradle 캐시 (다음 빌드 때 다시 받음)", HOME / ".gradle" / "caches", 0))
    return t

def junk_children(path, min_days):
    out = []
    cutoff = time.time() - min_days * DAY
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if min_days and e.stat(follow_symlinks=False).st_mtime > cutoff:
                        continue
                    out.append(e.path)
                except OSError:
                    pass
    except OSError:
        pass
    return out

def scan_junk():
    grouped = defaultdict(lambda: {"size": 0, "paths": []})
    seen = set()
    for label, path, days in junk_targets():
        key = os.path.normcase(str(path))
        if key in seen or not os.path.isdir(path):
            continue
        seen.add(key)
        for child in junk_children(path, days):
            grouped[label]["size"] += path_size(child)
            grouped[label]["paths"].append(child)
    items = [{"label": k, **v} for k, v in grouped.items() if v["size"] > 0]
    if SYSTEM == "Windows":
        size = sum(dir_size(f"{d}:\\$Recycle.Bin") for d in "CDEFGH"
                   if os.path.isdir(f"{d}:\\$Recycle.Bin"))
        if size > MB:
            items.append({"label": "휴지통", "size": size, "paths": [], "recycle": True})
    return sorted(items, key=lambda x: -x["size"])

def clean_junk_item(item):
    if item.get("recycle"):
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
                           capture_output=True)
        return item["size"] if r.returncode == 0 else 0
    return sum(delete_now(p) for p in item["paths"])

# ───────────────────────── 2~5. 홈 폴더 훑기 ─────────────────────────

# 빌드 산출물: 폴더 이름 -> 같은 프로젝트 폴더에 있어야 하는 표시 파일 (None 이면 조건 없음)
ARTIFACTS = {
    "node_modules": ("package.json",),
    ".next": ("package.json",), ".nuxt": ("package.json",),
    ".parcel-cache": ("package.json",), ".turbo": ("package.json",),
    "target": ("Cargo.toml", "pom.xml"),
    "build": ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"),
    ".gradle": ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"),
    "Pods": ("Podfile",),
    "__pycache__": None, ".pytest_cache": None, ".mypy_cache": None,
    ".ruff_cache": None, ".tox": None,
}
VENV_NAMES = {".venv", "venv", "env"}
SKIP_DIRS = {"appdata", "library", "application data", "windows", "program files",
             "program files (x86)", "programdata", "$recycle.bin", "system volume information",
             "node_modules", "site-packages", "snap"}
BUNDLE_EXT = (".app", ".photoslibrary", ".framework", ".xcodeproj", ".bundle")
KIND = {
    "동영상": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv"},
    "디스크 이미지": {".iso", ".img", ".dmg", ".vhd", ".vhdx", ".vmdk", ".vdi", ".qcow2"},
    "압축 파일": {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".xz", ".bz2"},
    "설치 파일": {".exe", ".msi", ".pkg", ".deb", ".rpm", ".apk", ".appimage"},
    "백업/덤프": {".bak", ".dmp", ".sql", ".dump", ".old", ".log"},
}

def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    for name, exts in KIND.items():
        if ext in exts:
            return name
    return "파일"

def walk(roots, min_large, want_dupes):
    large, artifacts, dupe_pool = [], [], defaultdict(list)
    trash = os.path.normcase(str(DATA_DIR))
    stack = [str(r) for r in roots]
    count = 0
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        names = {e.name for e in entries}
        subdirs, newest = [], 0
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                st = e.stat(follow_symlinks=False)
                if e.is_dir(follow_symlinks=False):
                    subdirs.append(e)
                    continue
                newest = max(newest, st.st_mtime)
                count += 1
                if count % 5000 == 0 and sys.stdout.isatty():
                    print(f"\r  파일 {count:,}개 확인 중…", end="", flush=True)
                if st.st_size >= min_large:
                    large.append({"path": e.path, "size": st.st_size,
                                  "used": max(st.st_mtime, st.st_atime)})
                if want_dupes and st.st_size >= MB:
                    dupe_pool[st.st_size].append((e.path, st.st_mtime))
            except OSError:
                pass
        for e in subdirs:
            name = e.name
            markers = ARTIFACTS.get(name, ())
            is_art = name in ARTIFACTS and (markers is None or any(m in names for m in markers))
            if not is_art and name in VENV_NAMES:
                is_art = os.path.isfile(os.path.join(e.path, "pyvenv.cfg"))
            if is_art:
                artifacts.append({"path": e.path, "project": d, "kind": name,
                                  "touched": newest or os.stat(d).st_mtime})
                continue
            low = name.lower()
            if (name.startswith(".") or low in SKIP_DIRS or low.endswith(BUNDLE_EXT)
                    or os.path.normcase(e.path) == trash):
                continue
            stack.append(e.path)
    if sys.stdout.isatty():
        print("\r" + " " * 40 + "\r", end="")
    for a in artifacts:
        a["size"] = dir_size(a["path"])
    return large, [a for a in artifacts if a["size"] > 0], dupe_pool

def _hash(path, limit=None):
    h = hashlib.blake2b(digest_size=16)
    try:
        st = os.stat(path)
        with open(path, "rb") as f:
            if limit:
                h.update(f.read(limit))
            else:
                for chunk in iter(lambda: f.read(MB), b""):
                    h.update(chunk)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # 검사 때문에 '마지막 사용일'이 바뀌지 않게
    except OSError:
        return None
    return h.hexdigest()

def find_dupes(pool):
    groups = []
    for size, files in pool.items():
        if len(files) < 2:
            continue
        quick = defaultdict(list)
        for p, m in files:
            quick[_hash(p, 65536)].append((p, m))
        for k, fs in quick.items():
            if k is None or len(fs) < 2:
                continue
            full = defaultdict(list)
            for p, m in fs:
                full[_hash(p)].append((p, m))
            for k2, same in full.items():
                if k2 is not None and len(same) > 1:
                    same.sort(key=lambda x: (x[1], len(x[0])))  # 가장 오래된 것을 원본으로
                    groups.append({"size": size, "keep": same[0][0],
                                   "extra": [p for p, _ in same[1:]]})
    return sorted(groups, key=lambda g: -g["size"] * len(g["extra"]))

def scan_downloads(days, min_large):
    out = []
    folder = HOME / "Downloads"
    cutoff = time.time() - days * DAY
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_symlink() or e.name.startswith("."):
                        continue
                    st = e.stat(follow_symlinks=False)
                    used = max(st.st_mtime, st.st_atime)
                    if used > cutoff:
                        continue
                    size = dir_size(e.path) if e.is_dir(follow_symlinks=False) else st.st_size
                    if e.is_file(follow_symlinks=False) and size >= min_large:
                        continue  # 큰 파일 목록에서 따로 물어봄
                    out.append({"path": e.path, "size": size, "used": used})
                except OSError:
                    pass
    except OSError:
        pass
    return sorted(out, key=lambda x: -x["size"])

def scan_all(opts):
    print(dim("  검사 중입니다. 파일이 많으면 몇 분 걸릴 수 있습니다…"))
    roots = [Path(p).expanduser().resolve() for p in opts.path] if opts.path else [HOME]
    large, artifacts, pool = walk(roots, opts.min_size * MB, not opts.no_dupes)
    stale_cut = time.time() - opts.days * DAY
    return {
        "junk": scan_junk(),
        "stale": sorted([a for a in artifacts if a["touched"] < stale_cut], key=lambda a: -a["size"]),
        "fresh": [a for a in artifacts if a["touched"] >= stale_cut],
        "large": sorted(large, key=lambda x: -x["size"]),
        "downloads": scan_downloads(opts.download_days, opts.min_size * MB),
        "dupes": find_dupes(pool) if not opts.no_dupes else [],
    }

def report(r, opts, limit=10):
    def more(n):
        if n > limit:
            print(dim(f"     … 외 {n - limit}개"))
    totals = {}

    header("1. 임시파일 · 캐시 (지워도 다시 만들어짐)")
    totals["junk"] = sum(i["size"] for i in r["junk"])
    for i in r["junk"][:limit]:
        print(f"  {human(i['size']):>9}  {i['label']}")
    more(len(r["junk"]))
    print(f"  합계 {bold(human(totals['junk']))}")

    header(f"2. {opts.days}일 넘게 손대지 않은 코드 프로젝트의 빌드 산출물")
    print(dim("     소스 코드는 그대로 두고 node_modules·build 같은 다시 만들 수 있는 폴더만 대상"))
    totals["stale"] = sum(a["size"] for a in r["stale"])
    for a in r["stale"][:limit]:
        print(f"  {human(a['size']):>9}  {short(a['path'], 60)}  " + dim(f"(마지막 작업 {ago(a['touched'])})"))
    more(len(r["stale"]))
    print(f"  합계 {bold(human(totals['stale']))}"
          + dim(f"   (최근 작업 중인 프로젝트 {len(r['fresh'])}개 폴더는 건드리지 않음)"))

    header(f"3. 큰 파일 ({opts.min_size}MB 이상) - 하나씩 물어봄")
    totals["large"] = sum(x["size"] for x in r["large"])
    for x in r["large"][:limit]:
        print(f"  {human(x['size']):>9}  {short(x['path'], 55)}  " + dim(f"({kind_of(x['path'])}, {ago(x['used'])})"))
    more(len(r["large"]))
    print(f"  합계 {bold(human(totals['large']))}")

    header(f"4. {opts.download_days}일 넘게 안 연 다운로드 파일")
    totals["downloads"] = sum(x["size"] for x in r["downloads"])
    for x in r["downloads"][:limit]:
        print(f"  {human(x['size']):>9}  {short(x['path'], 55)}  " + dim(f"({ago(x['used'])})"))
    more(len(r["downloads"]))
    print(f"  합계 {bold(human(totals['downloads']))}")

    header("5. 내용이 똑같은 중복 파일")
    totals["dupes"] = sum(g["size"] * len(g["extra"]) for g in r["dupes"])
    for g in r["dupes"][:limit]:
        print(f"  {human(g['size']):>9} x {len(g['extra']) + 1}개  {short(g['keep'], 55)}")
    more(len(r["dupes"]))
    print(f"  합계 {bold(human(totals['dupes']))}")

    safe = totals["junk"] + totals["stale"]
    header("요약")
    print(f"  물어보지 않고 지워도 안전한 것 : {bold(green(human(safe)))}")
    print(f"  확인이 필요한 것              : {bold(yellow(human(sum(totals.values()) - safe)))}"
          + dim("  (서로 겹칠 수 있음)"))
    try:
        du = shutil.disk_usage(HOME)
        print(f"  디스크: 전체 {human(du.total)} 중 {human(du.free)} 남음 ({du.free * 100 // du.total}%)")
    except OSError:
        pass

def cmd_scan(opts):
    report(scan_all(opts), opts)
    print("\n  아직 아무것도 지우지 않았습니다. 정리하려면: " + bold("python atuo.py clean"))

# ───────────────────────── 정리 ─────────────────────────

def cmd_clean(opts):
    r = scan_all(opts)
    report(r, opts)
    freed, held = 0, 0
    auto = opts.yes

    header("정리 시작")
    # 1. 임시파일/캐시
    if r["junk"]:
        total = sum(i["size"] for i in r["junk"])
        if auto or ask(f"  임시파일·캐시 {human(total)} 를 삭제할까요? (브라우저는 닫고 하세요)", default="y") == "y":
            got = sum(clean_junk_item(i) for i in r["junk"])
            freed += got
            print(green(f"   {human(got)} 삭제") + dim("  (사용 중인 파일은 건너뜀)"))

    # 2. 오래된 프로젝트의 빌드 산출물
    if r["stale"] and not auto:
        total = sum(a["size"] for a in r["stale"])
        a = ask(f"  오래된 빌드 산출물 {len(r['stale'])}개 {human(total)}: 전부(a) / 하나씩(s) / 건너뛰기(n)",
                "asn", "n")
        for art in r["stale"] if a != "n" else []:
            if a == "s":
                k = ask(f"   {human(art['size']):>9} {short(art['path'], 60)} 삭제?", "ynq", "n")
                if k == "q":
                    break
                if k != "y":
                    continue
            freed += delete_now(art["path"])
        if a != "n":
            print(green("   완료") + dim("  (필요하면 npm install / 빌드로 다시 만들어집니다)"))

    if auto:
        print(dim("  --yes 모드: 확인이 필요한 항목(큰 파일, 다운로드, 중복)은 건너뜁니다."))
    else:
        # 3. 큰 파일
        gone = set()
        if r["large"]:
            print(bold("\n  큰 파일 - y: 보관함으로 / n: 그대로 둠 / q: 그만 보기"))
        for x in r["large"]:
            k = ask(f"   {human(x['size']):>9} {short(x['path'], 60)} "
                    + dim(f"[{kind_of(x['path'])}, {ago(x['used'])}]") + " 치울까요?", "ynq", "n")
            if k == "q":
                break
            if k == "y":
                got = quarantine(x["path"], "큰 파일")
                held += got
                if got:
                    gone.add(x["path"])

        # 4. 오래된 다운로드
        if r["downloads"]:
            total = sum(x["size"] for x in r["downloads"])
            a = ask(f"\n  오래된 다운로드 {len(r['downloads'])}개 {human(total)}: "
                    "전부 보관함으로(a) / 하나씩(s) / 건너뛰기(n)", "asn", "n")
            for x in r["downloads"] if a != "n" else []:
                if a == "s":
                    k = ask(f"   {human(x['size']):>9} {short(x['path'], 60)} "
                            + dim(f"[{ago(x['used'])}]") + " 치울까요?", "ynq", "n")
                    if k == "q":
                        break
                    if k != "y":
                        continue
                held += quarantine(x["path"], "오래된 다운로드")
                gone.add(x["path"])

        # 5. 중복 파일
        if r["dupes"]:
            print(bold("\n  중복 파일 - y: 원본 하나만 남기고 나머지는 보관함으로"))
        for g in r["dupes"]:
            paths = [g["keep"]] + g["extra"]
            if any(p in gone or not os.path.exists(p) for p in paths):
                continue
            print(f"   {human(g['size'])} 짜리 같은 파일 {len(paths)}개")
            print(green(f"     남김: {short(g['keep'])}"))
            for p in g["extra"]:
                print(yellow(f"     치움: {short(p)}"))
            k = ask("   이렇게 할까요?", "ynq", "n")
            if k == "q":
                break
            if k == "y":
                held += sum(quarantine(p, "중복 파일") for p in g["extra"])

    header("결과")
    print(f"  바로 확보한 용량        : {bold(green(human(freed)))}")
    if held:
        print(f"  보관함으로 옮긴 용량    : {bold(yellow(human(held)))}")
        print(dim(f"  보관함의 파일은 {KEEP_DAYS}일 뒤 자동 삭제됩니다. 잘못 치웠다면: python atuo.py restore"))
        if ask("  보관함을 지금 비워서 용량을 바로 확보할까요? (되살릴 수 없게 됩니다)") == "y":
            cmd_empty()
    print(dim(f"  작업 기록: {LOG_FILE}"))

# ───────────────────────── 앱 정리 ─────────────────────────

def _run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return ""

SYSTEM_PUBLISHERS = ("microsoft", "intel", "nvidia", "advanced micro devices", "amd",
                     "realtek", "qualcomm", "apple inc")

def _win_last_runs():
    """탐색기 실행 기록(UserAssist)에서 프로그램별 마지막 실행 시각을 읽는다."""
    import winreg
    runs = {}
    base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i) + r"\Count") as k:
                        for j in range(winreg.QueryInfoKey(k)[1]):
                            name, data, _ = winreg.EnumValue(k, j)
                            if not isinstance(data, bytes) or len(data) < 68:
                                continue
                            ft = int.from_bytes(data[60:68], "little")
                            if ft:
                                runs[codecs.decode(name, "rot_13").lower()] = ft / 1e7 - 11644473600
                except OSError:
                    pass
    except OSError:
        pass
    return runs

def _apps_windows():
    import winreg
    runs = _win_last_runs()
    apps = {}
    sub = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    views = [(winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
             (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
             (winreg.HKEY_CURRENT_USER, 0)]
    for hive, view in views:
        try:
            root = winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                        def val(n, default=None):
                            try:
                                return winreg.QueryValueEx(k, n)[0]
                            except OSError:
                                return default
                        name, cmd = val("DisplayName"), val("UninstallString")
                        if not name or not cmd or val("SystemComponent") == 1 or val("ParentKeyName"):
                            continue
                        pub = str(val("Publisher", "") or "")
                        if any(p in pub.lower() for p in SYSTEM_PUBLISHERS):
                            continue  # 드라이버·런타임 등 시스템에 필요한 것은 목록에서 제외
                        loc = str(val("InstallLocation", "") or "").rstrip("\\")
                        size = int(val("EstimatedSize", 0) or 0) * 1024
                        if not size and loc and os.path.isdir(loc) and not is_protected(loc):
                            size = dir_size(loc)
                        keys = [os.path.basename(loc).lower()] if loc else []
                        keys.append(name.lower().split(" version")[0])
                        used = max((t for p, t in runs.items()
                                    if any(len(x) > 3 and x in p for x in keys)), default=0)
                        apps[name] = {"name": name, "size": size, "used": used, "uninstall": cmd}
                except OSError:
                    pass
    return list(apps.values())

def _apps_mac():
    apps = []
    for folder in (Path("/Applications"), HOME / "Applications"):
        if not folder.is_dir():
            continue
        for app in folder.glob("*.app"):
            if app.is_symlink():
                continue
            used = 0
            raw = _run(["mdls", "-raw", "-name", "kMDItemLastUsedDate", str(app)]).strip()
            try:
                used = time.mktime(time.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")) - time.timezone
            except ValueError:
                pass
            apps.append({"name": app.stem, "size": dir_size(app), "used": used, "path": str(app)})
    return apps

def _apps_linux():
    manual = set(_run(["apt-mark", "showmanual"]).split())
    apps = []
    for line in _run(["dpkg-query", "-W", "-f=${Installed-Size}\t${Package}\n"]).splitlines():
        size, _, name = line.partition("\t")
        if name in manual and size.isdigit():
            apps.append({"name": name, "size": int(size) * 1024, "used": 0,
                         "hint": f"sudo apt remove {name}"})
    return apps

def cmd_apps(opts):
    header("설치된 앱")
    print(dim("  앱 목록을 읽는 중…"))
    try:
        apps = {"Windows": _apps_windows, "Darwin": _apps_mac}.get(SYSTEM, _apps_linux)()
    except Exception as e:  # 레지스트리 접근 등 환경 문제
        print(red(f"  앱 목록을 읽지 못했습니다: {e}"))
        return
    if not apps:
        print("  정리할 수 있는 앱을 찾지 못했습니다.")
        return
    cutoff = time.time() - opts.app_days * DAY
    old = sorted([a for a in apps if a["used"] and a["used"] < cutoff], key=lambda a: -a["size"])
    unknown = sorted([a for a in apps if not a["used"]], key=lambda a: -a["size"])[:20]
    recent = len(apps) - len(old) - len([a for a in apps if not a["used"]])
    print(f"  전체 {len(apps)}개 · 최근에 쓴 앱 {recent}개는 건드리지 않습니다.")

    def offer(group, title):
        if not group:
            return
        print(bold(f"\n  {title}"))
        for a in group:
            k = ask(f"   {human(a['size']):>9}  {a['name'][:45]}  " + dim(f"[마지막 사용 {ago(a['used'])}]")
                    + " 제거할까요?", "ynq", "n")
            if k == "q":
                return
            if k != "y":
                continue
            if "path" in a:        # macOS: 보관함으로 옮김 → restore 로 되살릴 수 있음
                if quarantine(a["path"], "앱"):
                    print(green("     보관함으로 옮겼습니다."))
            elif "uninstall" in a:  # Windows: 그 앱의 공식 제거 프로그램을 띄움
                print(dim("     제거 프로그램을 실행합니다. 창의 안내를 따라 주세요."))
                try:
                    subprocess.run(a["uninstall"], shell=True)
                    log("앱 제거 실행", a["name"], a["size"])
                except OSError as e:
                    print(red(f"     실행 실패: {e}"))
            else:
                print(f"     터미널에서 실행하세요:  {bold(a['hint'])}")

    offer(old, f"{opts.app_days}일 넘게 쓰지 않은 앱 (크기순)")
    if unknown and ask(f"\n  사용 기록을 알 수 없는 앱 중 큰 것 {len(unknown)}개도 볼까요?") == "y":
        offer(unknown, "사용 기록을 알 수 없는 앱 (크기순) - 모르는 앱은 그냥 두세요")

# ───────────────────────── 메모리 ─────────────────────────

def cmd_memory(_opts=None):
    usage = defaultdict(lambda: [0, 0])
    if SYSTEM == "Windows":
        import csv
        for row in csv.reader(_run(["tasklist", "/fo", "csv", "/nh"]).splitlines()):
            if len(row) >= 5:
                digits = "".join(ch for ch in row[4] if ch.isdigit())
                if digits:
                    usage[row[0]][0] += int(digits) * 1024
                    usage[row[0]][1] += 1
    else:
        for line in _run(["ps", "-axo", "rss=,comm="]).splitlines():
            rss, _, name = line.strip().partition(" ")
            if rss.isdigit() and name:
                name = os.path.basename(name.strip())
                usage[name][0] += int(rss) * 1024
                usage[name][1] += 1
    header("메모리(RAM)를 많이 쓰는 프로그램 TOP 15")
    for name, (mem, n) in sorted(usage.items(), key=lambda x: -x[1][0])[:15]:
        print(f"  {human(mem):>9}  {name}" + (dim(f"  (프로세스 {n}개)") if n > 1 else ""))
    if SYSTEM == "Windows":
        import winreg
        header("컴퓨터를 켤 때 자동으로 시작되는 프로그램")
        key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for i in range(winreg.QueryInfoKey(k)[1]):
                        print(f"  - {winreg.EnumValue(k, i)[0]}")
            except OSError:
                pass
        print(dim("  끄려면: Ctrl+Shift+Esc → '시작 앱' 탭에서 '사용 안 함'"))
    print(dim("\n  이 화면은 보기만 합니다. 필요 없는 프로그램은 직접 종료하세요."))

# ───────────────────────── 진입점 ─────────────────────────

def menu(opts):
    actions = {"1": cmd_scan, "2": cmd_clean, "3": cmd_apps,
               "4": cmd_memory, "5": cmd_restore, "6": cmd_empty}
    while True:
        print(bold(cyan(f"\n  Atuo {VERSION} - 내 컴퓨터 정리")))
        print("   1. 검사만 하기 (아무것도 지우지 않음)")
        print("   2. 정리하기 (하나씩 물어봄)")
        print("   3. 안 쓰는 앱 정리")
        print("   4. 메모리 많이 쓰는 프로그램 보기")
        print("   5. 보관함에서 되살리기")
        print("   6. 보관함 비우기")
        print("   0. 끝내기")
        try:
            pick = input("  번호: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if pick == "0":
            return
        if pick in actions:
            actions[pick](opts)

def main(argv=None):
    _init_console()
    p = argparse.ArgumentParser(prog="atuo", description="내 컴퓨터 자동 정리 도구")
    p.add_argument("command", nargs="?", default="menu",
                   choices=["menu", "scan", "clean", "apps", "memory", "restore", "empty"])
    p.add_argument("--path", action="append", help="검사할 폴더 (기본: 홈 폴더, 여러 번 지정 가능)")
    p.add_argument("--min-size", type=int, default=500, metavar="MB", help="'큰 파일' 기준 (기본 500MB)")
    p.add_argument("--days", type=int, default=30, help="이 일수 넘게 손대지 않은 프로젝트만 정리 (기본 30)")
    p.add_argument("--download-days", type=int, default=90, help="오래된 다운로드 기준 일수 (기본 90)")
    p.add_argument("--app-days", type=int, default=180, help="안 쓰는 앱 기준 일수 (기본 180)")
    p.add_argument("--no-dupes", action="store_true", help="중복 파일 검사 생략 (더 빠름)")
    p.add_argument("--yes", action="store_true", help="임시파일·캐시만 묻지 않고 삭제 (나머지는 건너뜀)")
    p.add_argument("--version", action="version", version=VERSION)
    opts = p.parse_args(argv)

    expired = purge_expired()
    if expired:
        print(dim(f"  보관 기간({KEEP_DAYS}일)이 지난 파일 {human(expired)} 을 삭제했습니다."))
    {"menu": menu, "scan": cmd_scan, "clean": cmd_clean, "apps": cmd_apps,
     "memory": cmd_memory, "restore": cmd_restore, "empty": cmd_empty}[opts.command](opts)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  중단했습니다.")
