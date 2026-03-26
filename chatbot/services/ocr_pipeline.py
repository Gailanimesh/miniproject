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

    # Updated to gemini-2.5-flash (gemini-1.5-flash is deprecated)
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
        "Analyze this timetable image/PDF and extract the examination schedule. "
        "Output a raw JSON dictionary. Strictly adhere to this structure: "
        '{"has_multiple_branches": boolean, '
        '"detected_branches": ["string"], '
        '"exam": "string", '
        '"subjects": [{"name": "string", "date": "YYYY-MM-DD", "difficulty": "medium"}], '
        '"branch_subjects": {"branch_name": [{"name": "string", "date": "YYYY-MM-DD", "difficulty": "medium"}]} }. '
        "\n\nGuidelines:\n"
        "1. Identify all subjects, their full names, and their scheduled dates.\n"
        "2. Dates MUST be in YYYY-MM-DD format. If year is missing, assume current year.\n"
        "3. If multiple branches/streams exist (e.g., CS, EE, Civil):\n"
        f"   - Check if user's branch is mentioned in this request: '{user_prompt}'.\n"
        "   - If matched, populate 'subjects' with ONLY that branch and set 'has_multiple_branches' to true.\n"
        "   - If NOT matched, leave 'subjects' empty, set 'has_multiple_branches' to true, and populate 'branch_subjects' for EVERY branch found.\n"
        "4. If only one branch exists, populate 'subjects' and leave 'branch_subjects' empty.\n"
        "5. IMPORTANT: Output ONLY the raw JSON. No markdown blocks, no triple backticks."
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
    if extracted.startswith("```"):
        # Remove any leading backticks and language identifiers
        extracted = re.sub(r'^```(?:json)?\s*', '', extracted)
    if extracted.endswith("```"):
        extracted = extracted.rstrip("`").strip()
        
    try:
        parsed_json = json.loads(extracted.strip())
        return parsed_json
    except json.JSONDecodeError:
        return {"exam": None, "subjects": [], "error": f"Gemini returned invalid JSON: {extracted[:100]}..."}

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

