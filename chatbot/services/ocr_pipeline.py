import importlib
import re
from datetime import datetime

DATE_REGEX = re.compile(
    r"(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4}|\d{2}\.\d{2}\.\d{4})"
)


def _normalize_date(raw_date):
    candidates = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"]
    for date_format in candidates:
        try:
            return datetime.strptime(raw_date, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def extract_text_from_image(image_source):
    """
    Accepts a file object or a file path and returns OCR text.
    Returns an empty string if OCR libraries are unavailable.
    """
    try:
        pytesseract = importlib.import_module("pytesseract")
        pil_image = importlib.import_module("PIL.Image")
    except ModuleNotFoundError:
        return ""

    try:
        if hasattr(image_source, "seek"):
            image_source.seek(0)
        image = pil_image.open(image_source)
        return pytesseract.image_to_string(image) or ""
    except Exception:
        return ""


def parse_exam_timetable(text, llm=None):
    """
    Parse OCR text into structured exam data.
    Optional llm callback can override deterministic parsing when provided.
    """
    if llm:
        try:
            llm_result = llm(text)
            if isinstance(llm_result, dict):
                return llm_result
        except Exception:
            pass

    parsed = {"exam": None, "subjects": []}
    if not text:
        return parsed

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:3]:
        lower_line = line.lower()
        if any(tag in lower_line for tag in ["exam", "semester", "internal", "final"]):
            parsed["exam"] = line
            break

    seen = set()
    for line in lines:
        match = DATE_REGEX.search(line)
        if not match:
            continue

        raw_date = match.group(0)
        normalized_date = _normalize_date(raw_date)
        if not normalized_date:
            continue

        subject_name = line.replace(raw_date, "").strip(" -:\t")
        if not subject_name:
            continue

        key = (subject_name.lower(), normalized_date)
        if key in seen:
            continue
        seen.add(key)

        parsed["subjects"].append(
            {
                "name": subject_name,
                "date": normalized_date,
            }
        )

    return parsed
