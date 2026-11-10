"""로컬 저장소: 폴더·SQLite 초기화, 파일 저장과 경로 검사, 텍스트 추출."""
import io
import json
import os
import re
import shutil
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import config

ALLOWED_EXTS = {".pdf", ".docx", ".txt"}

CASE_FIELDS = ("business_name", "expense_item", "amount", "review_result", "ai_question", "user_answer")


class ExtractionError(Exception):
    """문서에서 텍스트를 추출할 수 없을 때 (메시지는 사용자에게 그대로 표시)."""


# ---------------------------------------------------------------- 경로 / 초기화
def data_dir() -> Path:
    return Path(config.DATA_DIR)


def guidelines_dir() -> Path:
    return data_dir() / "guidelines"


def documents_dir() -> Path:
    return data_dir() / "documents"


def db_path() -> Path:
    return data_dir() / "cases.db"


@contextmanager
def _db():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_storage() -> None:
    """필요한 폴더와 DB 테이블이 없으면 만든다(있으면 그대로 둔다)."""
    guidelines_dir().mkdir(parents=True, exist_ok=True)
    documents_dir().mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY,
                business_name TEXT,
                expense_item TEXT,
                amount INTEGER,
                review_result TEXT,
                ai_question TEXT,
                user_answer TEXT,
                file_paths TEXT,
                created_at TEXT
            )"""
        )


# ---------------------------------------------------------------- 파일 이름 / 검사
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str) -> str:
    """원본 파일명을 최대한 유지하되 Windows에서 쓸 수 없는 문자와 폴더 경로를 제거한다."""
    name = unicodedata.normalize("NFC", (name or "").replace("\\", "/").split("/")[-1])
    name = _INVALID_CHARS.sub("_", name).strip(" .")
    stem, ext = os.path.splitext(name)
    stem = stem.strip(" .")
    if not stem or stem.upper() in _RESERVED:
        stem = f"file_{stem}" if stem else "file"
    return stem[:120] + ext.lower()


def unique_path(folder: Path, filename: str) -> Path:
    """같은 이름의 파일이 있으면 '이름 (2).pdf' 형식으로 고유한 경로를 만든다."""
    candidate = folder / filename
    stem, ext = os.path.splitext(filename)
    i = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({i}){ext}"
        i += 1
    return candidate


def validate_upload(name: str, size: int) -> str | None:
    """허용 형식·크기를 검사한다. 문제가 있으면 오류 메시지, 없으면 None."""
    ext = os.path.splitext(name or "")[1].lower()
    if ext not in ALLOWED_EXTS:
        return f"'{name}': 지원하지 않는 형식입니다. PDF, DOCX, TXT 파일만 등록할 수 있습니다."
    if size <= 0:
        return f"'{name}': 빈 파일입니다."
    if size > config.MAX_FILE_MB * 1024 * 1024:
        return f"'{name}': 파일이 너무 큽니다(최대 {config.MAX_FILE_MB}MB)."
    return None


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def resolve_case_file(rel_path: str) -> Path | None:
    """DB에 저장된 상대경로(data/ 기준)를 실제 경로로 바꾼다.
    data/documents/ 밖을 가리키면 None을 돌려준다(허용 폴더 외부 접근 차단)."""
    if not rel_path or not isinstance(rel_path, str):
        return None
    rel = Path(rel_path.replace("\\", "/"))
    if rel.is_absolute() or rel.drive or rel.anchor:
        return None
    full = data_dir() / rel
    return full if _inside(full, documents_dir()) else None


def to_rel_path(full: Path) -> str:
    return full.resolve().relative_to(data_dir().resolve()).as_posix()


# ---------------------------------------------------------------- 텍스트 추출
def _decode_txt(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as e:  # 손상·암호화된 PDF 등
        raise ExtractionError(f"PDF를 읽을 수 없습니다({type(e).__name__}). 암호가 걸렸거나 손상된 파일일 수 있습니다.")
    if not any(pages):
        raise ExtractionError(
            "PDF에서 텍스트를 추출할 수 없습니다. 스캔(이미지)형 PDF로 보이며, 이 프로그램은 OCR을 지원하지 않습니다. "
            "텍스트가 포함된 PDF, DOCX 또는 TXT로 변환해 등록해 주세요."
        )
    return "\n\n".join(f"[{i}페이지]\n{t}" for i, t in enumerate(pages, 1) if t)


def _extract_docx(data: bytes) -> str:
    import docx
    from docx.table import Table

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise ExtractionError(f"DOCX 파일을 읽을 수 없습니다({type(e).__name__}).")
    lines = []
    for block in document.iter_inner_content():  # 문단과 표를 문서 순서대로
        if isinstance(block, Table):
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    text = cell.text.strip()
                    if not cells or cells[-1] != text:  # 병합 셀 중복 제거
                        cells.append(text)
                lines.append(" | ".join(cells))
        else:
            lines.append(block.text)
    return "\n".join(lines)


def extract_text(name: str, data: bytes) -> str:
    """파일 내용(bytes)에서 텍스트를 추출한다. 실패하면 ExtractionError."""
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        text = _extract_pdf(data)
    elif ext == ".docx":
        text = _extract_docx(data)
    elif ext == ".txt":
        text = _decode_txt(data)
    else:
        raise ExtractionError(f"지원하지 않는 형식입니다: {ext}")
    if not text.strip():
        raise ExtractionError("문서에서 텍스트를 찾을 수 없습니다.")
    return text.strip()


def extract_text_from_file(path: Path) -> str:
    return extract_text(path.name, path.read_bytes())


# ---------------------------------------------------------------- 지침
def list_guidelines() -> list[Path]:
    folder = guidelines_dir()
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_EXTS)


def save_guideline(name: str, data: bytes) -> Path:
    path = unique_path(guidelines_dir(), safe_filename(name))
    path.write_bytes(data)
    return path


def delete_guideline(filename: str) -> bool:
    path = guidelines_dir() / safe_filename(filename)
    if path.name != filename or not _inside(path, guidelines_dir()) or not path.is_file():
        return False
    path.unlink()
    return True


# ---------------------------------------------------------------- 사례 DB
def _row_to_case(row: sqlite3.Row) -> dict:
    case = dict(row)
    try:
        paths = json.loads(case.get("file_paths") or "[]")
        case["file_paths"] = [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []
    except json.JSONDecodeError:
        case["file_paths"] = []
    return case


def list_cases() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM cases ORDER BY id").fetchall()
    return [_row_to_case(r) for r in rows]


def get_cases(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    with _db() as conn:
        rows = conn.execute(f"SELECT * FROM cases WHERE id IN ({marks}) ORDER BY id", list(ids)).fetchall()
    return [_row_to_case(r) for r in rows]


def _find_duplicate(conn: sqlite3.Connection, record: dict) -> int | None:
    row = conn.execute(
        "SELECT id FROM cases WHERE business_name IS ? AND expense_item IS ? AND amount IS ? "
        "AND ai_question IS ? AND user_answer IS ?",
        [record.get(k) for k in ("business_name", "expense_item", "amount", "ai_question", "user_answer")],
    ).fetchone()
    return row[0] if row else None


def save_cases(records: list[dict], files: list[tuple[str, bytes]]) -> dict:
    """사례 여러 건과 관련 문서를 한 번에 저장한다.

    - 파일은 data/documents/case_XXX/ 폴더 하나에 한 번만 복사하고, 모든 사례가 같은 경로를 참조한다.
    - 이미 같은 내용의 사례가 있으면 새로 만들지 않는다(중복 저장 방지).
    - 중간에 오류가 나면 DB 기록과 복사한 파일을 모두 되돌린다.
    반환: {"saved_ids": [...], "duplicate_ids": [...], "file_paths": [...]}
    """
    init_storage()
    conn = sqlite3.connect(db_path())
    folder: Path | None = None
    try:
        saved_ids, duplicate_ids, new_records = [], [], []
        for rec in records:
            dup = _find_duplicate(conn, rec)
            if dup is not None:
                duplicate_ids.append(dup)
            else:
                new_records.append(rec)
        if not new_records:
            return {"saved_ids": [], "duplicate_ids": duplicate_ids, "file_paths": []}

        now = datetime.now().isoformat(timespec="seconds")
        for rec in new_records:
            cur = conn.execute(
                "INSERT INTO cases (business_name, expense_item, amount, review_result, ai_question, "
                "user_answer, file_paths, created_at) VALUES (?, ?, ?, ?, ?, ?, '[]', ?)",
                [rec.get(k) for k in CASE_FIELDS] + [now],
            )
            saved_ids.append(cur.lastrowid)

        # 사례 폴더 생성 (이미 있으면 _2, _3 ... 을 붙인다)
        base = f"case_{saved_ids[0]:03d}"
        folder = documents_dir() / base
        n = 2
        while True:
            try:
                folder.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                folder = documents_dir() / f"{base}_{n}"
                n += 1

        rel_paths = []
        for name, data in files:
            target = unique_path(folder, safe_filename(name))
            target.write_bytes(data)
            rel_paths.append(to_rel_path(target))

        paths_json = json.dumps(rel_paths, ensure_ascii=False)
        conn.executemany("UPDATE cases SET file_paths = ? WHERE id = ?", [(paths_json, i) for i in saved_ids])
        conn.commit()
        return {"saved_ids": saved_ids, "duplicate_ids": duplicate_ids, "file_paths": rel_paths}
    except Exception:
        conn.rollback()
        if folder is not None and folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        conn.close()


def delete_case(case_id: int) -> None:
    """사례를 삭제한다. 다른 사례가 같이 쓰는 파일은 남겨 둔다."""
    cases = list_cases()
    target = next((c for c in cases if c["id"] == case_id), None)
    if target is None:
        return
    still_used = {p for c in cases if c["id"] != case_id for p in c["file_paths"]}
    with _db() as conn:
        conn.execute("DELETE FROM cases WHERE id = ?", [case_id])
    for rel in target["file_paths"]:
        if rel in still_used:
            continue
        path = resolve_case_file(rel)
        if path and path.is_file():
            path.unlink()
            if path.parent != documents_dir() and not any(path.parent.iterdir()):
                path.parent.rmdir()


def read_case_documents(case: dict) -> list[dict]:
    """사례에 저장된 파일 경로를 따라 실제 파일을 열고 텍스트를 추출한다.
    반환: [{"path", "name", "text"(없으면 None), "warning"(없으면 None)}]"""
    docs = []
    for rel in case.get("file_paths", []):
        info = {"path": rel, "name": Path(rel).name, "text": None, "warning": None}
        path = resolve_case_file(rel)
        if path is None:
            info["warning"] = f"허용된 저장 폴더 밖의 경로라서 열지 않았습니다: {rel}"
        elif not path.is_file():
            info["warning"] = f"과거 사례의 첨부파일을 찾을 수 없습니다: {rel}"
        else:
            try:
                info["text"] = extract_text_from_file(path)
            except ExtractionError as e:
                info["warning"] = f"{rel}: {e}"
            except OSError as e:
                info["warning"] = f"과거 사례의 첨부파일을 열 수 없습니다: {rel} ({e})"
        docs.append(info)
    return docs
