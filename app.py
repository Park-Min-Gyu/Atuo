"""문서 정리 AI - 브라우저에서 쓰는 화면.

실행:  python app.py   →  http://localhost:5000
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from flask import Flask, abort, jsonify, render_template, request, send_file

from hwpx_writer import write_hwpx
from local_planner import make_plan_offline
from planner import PlanError, make_plan
from reader import ReadError, read_table_file
from xlsx_writer import write_xlsx

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
_downloads: dict[str, tuple[Path, str]] = {}


def _has_api_key() -> bool:
    """키가 있으면 AI 정리, 없으면 키 없이 동작하는 기본 정리를 쓴다."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", title).strip()
    return re.sub(r"\s+", " ", name)[:60] or "문서"


def _register(path: Path, download_name: str) -> str:
    token = uuid.uuid4().hex
    _downloads[token] = (path, download_name)
    return f"/download/{token}"


@app.get("/")
def index():
    return render_template("index.html", ai=_has_api_key())


@app.post("/generate")
def generate():
    notes = request.form.get("notes", "")
    want_hwpx = request.form.get("hwpx") == "on"
    want_xlsx = request.form.get("xlsx") == "on"
    upload = request.files.get("file")

    table_text = ""
    if upload and upload.filename:
        try:
            table_text = read_table_file(upload.filename, upload.read())
        except ReadError as e:
            return jsonify(error=str(e)), 400
    if not notes.strip() and not table_text:
        return jsonify(error="메모를 적거나 엑셀 파일을 올려 주세요."), 400
    if not (want_hwpx or want_xlsx):
        return jsonify(error="만들 파일 종류를 하나 이상 골라 주세요."), 400

    notices: list[str] = []
    if _has_api_key():
        try:
            plan = make_plan(notes, table_text, want_excel=want_xlsx)
        except PlanError as e:
            return jsonify(error=str(e)), 502
    else:
        plan, notices = make_plan_offline(notes, table_text, want_excel=want_xlsx)

    job = uuid.uuid4().hex[:8]
    base = _safe_filename(plan.title)
    files = []
    if want_hwpx:
        path = OUTPUT_DIR / f"{job}.hwpx"
        write_hwpx(plan, str(path))
        files.append({"label": "한글 문서 (.hwpx)", "name": f"{base}.hwpx",
                      "url": _register(path, f"{base}.hwpx")})
    if want_xlsx:
        if plan.sheets:
            path = OUTPUT_DIR / f"{job}.xlsx"
            write_xlsx(plan, str(path))
            files.append({"label": "엑셀 (.xlsx)", "name": f"{base}.xlsx",
                          "url": _register(path, f"{base}.xlsx")})
        else:
            notices.append("표로 만들 만한 내용이 없어 엑셀은 만들지 않았습니다.")

    return jsonify(
        title=plan.title,
        doc_type=plan.doc_type,
        summary=plan.summary,
        missing_info=plan.missing_info,
        notices=notices,
        files=files,
    )


@app.get("/download/<token>")
def download(token: str):
    entry = _downloads.get(token)
    if entry is None or not entry[0].exists():
        abort(404)
    path, name = entry
    response = send_file(path, as_attachment=True, download_name=name)
    # 한글 파일명을 못 읽는 브라우저를 위한 영문 대체 이름
    response.headers["Content-Disposition"] = (
        f'attachment; filename="document{path.suffix}"; filename*=UTF-8\'\'{quote(name)}'
    )
    return response


if __name__ == "__main__":
    print("브라우저에서 http://localhost:5000 을 열어 주세요.")
    app.run(host="127.0.0.1", port=5000)
