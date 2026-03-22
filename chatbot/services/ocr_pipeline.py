import importlib
import io
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


import base64
import os
import requests
import json

def extract_text_from_file_with_gemini(file_source, mime_type="image/jpeg", user_prompt=""):
    """
    Extract structured JSON data securely via Google Gemini API.
    Dynamically identifies branches and isolates subjects based on user prompt.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"exam": None, "subjects": [], "error": "GEMINI_API_KEY is not configured."}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    try:
        if hasattr(file_source, "seek"):
            file_source.seek(0)
            file_data = file_source.read()
        else:
            with open(file_source, "rb") as f:
                file_data = f.read()
    except Exception as e:
        return {"exam": None, "subjects": [], "error": f"File read error: {e}"}

    b64_data = base64.b64encode(file_data).decode("utf-8")

    sys_text = (
        "Analyze this timetable image and output a raw JSON dictionary without Markdown blocks. "
        "Strictly adhere to this structure: "
        '{"has_multiple_branches": boolean, '
        '"detected_branches": ["string"], '
        '"exam": "string", '
        '"subjects": [{"name": "string", "date": "YYYY-MM-DD", "difficulty": "medium"}], '
        '"branch_subjects": {"branch_name": [{"name": "string", "date": "YYYY-MM-DD", "difficulty": "medium"}]} }. '
        "If there are multiple branches/streams in the timetable (like CE, CS A, EE, etc.): "
        f"1. Check if the user specified their branch here: '{user_prompt}'. "
        "2. If the user explicitly specified a branch, ignore the others and populate the main 'subjects' array ONLY with their branch's subjects. "
        "3. If the user did NOT specify a branch, leave the main 'subjects' array EMPTY [] but populate 'branch_subjects' with a map of every branch name to its specific array of subjects. Set 'has_multiple_branches' to true. "
        "If it is a single-branch timetable, just populate the main 'subjects' array normally."
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": sys_text},
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": b64_data
                    }
                }
            ]
        }]
    }
    
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        resp_json = response.json()
    except Exception as e:
        return {"exam": None, "subjects": [], "error": f"Gemini API error: {e}"}

    parts = resp_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])
    extracted = parts[0].get("text", "")
    
    # Clean up standard markdown markers if the model ignored our rule
    extracted = extracted.strip()
    if extracted.startswith("```json"):
        extracted = extracted[7:]
    if extracted.endswith("```"):
        extracted = extracted[:-3]
        
    try:
        parsed_json = json.loads(extracted.strip())
        return parsed_json
    except json.JSONDecodeError:
        return {"exam": None, "subjects": [], "error": "Gemini returned invalid JSON."}

def _is_pdf(file_source):
    """Check if the file is a PDF by reading its magic bytes."""
    try:
        if hasattr(file_source, "read"):
            header = file_source.read(5)
            file_source.seek(0)
            return header == b"%PDF-"
        return str(file_source).lower().endswith(".pdf")
    except Exception:
        return False

def extract_text_from_image(image_source, user_prompt=""):
    """
    Accepts a file object or a file path and returns structured JSON timetable via Gemini 2.5-flash.
    Automatically handles PDFs directly through Gemini natively.
    """
    if _is_pdf(image_source):
        return extract_text_from_file_with_gemini(image_source, mime_type="application/pdf", user_prompt=user_prompt)
    return extract_text_from_file_with_gemini(image_source, mime_type="image/jpeg", user_prompt=user_prompt)


def parse_exam_timetable(text, llm=None):
    """
    Legacy parser. Redundant when using strictly structured JSON from Gemini.
    Returns the parsed dictionary if it's already a dict, otherwise empty default.
    """
    if isinstance(text, dict):
        return text
    
    return {"exam": None, "subjects": []}

