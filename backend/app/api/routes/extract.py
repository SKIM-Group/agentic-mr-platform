"""File text extraction endpoint — supports Excel, Word, PDF, CSV, plain text."""

import io
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".docx", ".doc", ".pdf", ".csv", ".txt", ".md"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/extract")
async def extract_text(
    file: UploadFile = File(...),
    _: None = Depends(verify_api_key),
) -> dict:
    """Extract plain text from an uploaded document."""
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    filename = file.filename or ""
    ext = _get_extension(filename).lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    try:
        text = _extract(content, ext, filename)
    except Exception as exc:
        logger.exception("Extraction failed for %s: %s", filename, exc)
        raise HTTPException(status_code=422, detail=f"Could not extract text: {exc}") from exc

    return {
        "filename": filename,
        "characters": len(text),
        "text": text,
    }


def _get_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1]


def _extract(content: bytes, ext: str, filename: str) -> str:
    if ext in (".txt", ".md", ".csv"):
        return content.decode("utf-8", errors="replace")

    if ext in (".xlsx", ".xls"):
        return _extract_excel(content)

    if ext in (".docx", ".doc"):
        return _extract_word(content)

    if ext == ".pdf":
        return _extract_pdf(content)

    return content.decode("utf-8", errors="replace")


def _extract_excel(content: bytes) -> str:
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("openpyxl not installed") from exc

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    lines: list[str] = []
    for sheet in wb.worksheets:
        lines.append(f"=== Sheet: {sheet.title} ===")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(cells):
                lines.append("\t".join(cells))
    return "\n".join(lines)


def _extract_word(content: bytes) -> str:
    try:
        import docx  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("python-docx not installed") from exc

    doc = docx.Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("pypdf not installed") from exc

    reader = PdfReader(io.BytesIO(content))
    pages: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- Page {i} ---\n{text}")
    return "\n\n".join(pages)
