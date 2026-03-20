"""
OCR Pipeline Tests
==================
Tests cover:
1. Pure unit tests for parse_exam_timetable() — fast, no DB, no I/O
2. PDF extraction tests using the fixture PDF (pypdf, no Tesseract needed)
3. DB integration tests — ExamSubject and Topic rows created correctly
4. API integration tests — POST exam_image (PDF) to /api/chatbot/converse/

Run with:
    python manage.py test chatbot.tests_ocr --settings=backend.test_settings -v 2
"""

import io
import os

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.services.ocr_pipeline import (
    _is_pdf,
    _normalize_date,
    extract_text_from_pdf,
    parse_exam_timetable,
)
from chatbot.views import _upsert_exam_subjects
from timetable.models import ExamSubject, Topic

User = get_user_model()

# Path to the fixture PDF created by chatbot/fixtures/create_test_pdf.py
FIXTURE_PDF = os.path.join(
    os.path.dirname(__file__), "fixtures", "exam_timetable.pdf"
)

# Raw text that mirrors what the fixture PDF contains
FIXTURE_TEXT = """Final Semester Exam Timetable
Mathematics - 25/03/2026
Physics - 28/03/2026
Chemistry - 01/04/2026
Computer Science - 05/04/2026
English - 08/04/2026
"""

EXPECTED_SUBJECTS = [
    {"name": "Mathematics", "date": "2026-03-25"},
    {"name": "Physics", "date": "2026-03-28"},
    {"name": "Chemistry", "date": "2026-04-01"},
    {"name": "Computer Science", "date": "2026-04-05"},
    {"name": "English", "date": "2026-04-08"},
]


# ---------------------------------------------------------------------------
# 1. Unit Tests — date normalisation & text parser
# ---------------------------------------------------------------------------

class DateNormalisationTests(TestCase):
    """Test the internal date format normaliser."""

    def test_iso_date_unchanged(self):
        self.assertEqual(_normalize_date("2026-03-25"), "2026-03-25")

    def test_dd_slash_mm_slash_yyyy(self):
        self.assertEqual(_normalize_date("25/03/2026"), "2026-03-25")

    def test_dd_dash_mm_dash_yyyy(self):
        self.assertEqual(_normalize_date("25-03-2026"), "2026-03-25")

    def test_dd_dot_mm_dot_yyyy(self):
        self.assertEqual(_normalize_date("25.03.2026"), "2026-03-25")

    def test_invalid_date_returns_none(self):
        self.assertIsNone(_normalize_date("not-a-date"))


class ParseExamTimetableTests(TestCase):
    """Unit tests for parse_exam_timetable() — no DB, no files."""

    def test_parses_correct_subject_count(self):
        result = parse_exam_timetable(FIXTURE_TEXT)
        self.assertEqual(len(result["subjects"]), 5)

    def test_exam_header_detected(self):
        result = parse_exam_timetable(FIXTURE_TEXT)
        self.assertIsNotNone(result["exam"])
        self.assertIn("Semester", result["exam"])

    def test_subject_names_and_dates(self):
        result = parse_exam_timetable(FIXTURE_TEXT)
        for expected in EXPECTED_SUBJECTS:
            match = next(
                (s for s in result["subjects"] if s["name"] == expected["name"]),
                None,
            )
            self.assertIsNotNone(match, f"Subject '{expected['name']}' not found")
            self.assertEqual(match["date"], expected["date"])

    def test_empty_text_returns_empty(self):
        result = parse_exam_timetable("")
        self.assertIsNone(result["exam"])
        self.assertEqual(result["subjects"], [])

    def test_no_dates_returns_empty_subjects(self):
        result = parse_exam_timetable("This text has no dates in it at all.")
        self.assertEqual(result["subjects"], [])

    def test_duplicate_subject_date_deduplicated(self):
        text = "Mathematics - 25/03/2026\nMathematics - 25/03/2026\n"
        result = parse_exam_timetable(text)
        names = [s["name"] for s in result["subjects"]]
        self.assertEqual(names.count("Mathematics"), 1)

    def test_llm_override_used_when_provided(self):
        """If an llm callback is passed and returns a dict, use it."""
        fake_result = {"exam": "Mock Exam", "subjects": [{"name": "Mock", "date": "2026-01-01"}]}
        result = parse_exam_timetable(FIXTURE_TEXT, llm=lambda t: fake_result)
        self.assertEqual(result["exam"], "Mock Exam")
        self.assertEqual(len(result["subjects"]), 1)


# ---------------------------------------------------------------------------
# 2. PDF Extraction Tests
# ---------------------------------------------------------------------------

class PDFExtractionTests(TestCase):
    """Test PDF detection and text extraction using the fixture file."""

    def _open_fixture(self):
        if not os.path.exists(FIXTURE_PDF):
            self.skipTest(f"Fixture PDF not found: {FIXTURE_PDF}")
        return open(FIXTURE_PDF, "rb")

    def test_is_pdf_detects_real_pdf(self):
        with self._open_fixture() as f:
            self.assertTrue(_is_pdf(f))

    def test_is_pdf_rejects_non_pdf(self):
        fake = io.BytesIO(b"This is just plain text, not a PDF")
        self.assertFalse(_is_pdf(fake))

    def test_extract_text_from_pdf_returns_string(self):
        with self._open_fixture() as f:
            text = extract_text_from_pdf(f)
        self.assertIsInstance(text, str)
        self.assertTrue(len(text) > 0, "Expected non-empty text from fixture PDF")

    def test_extract_text_contains_subjects(self):
        with self._open_fixture() as f:
            text = extract_text_from_pdf(f)
        for subject in ["Mathematics", "Physics", "Chemistry"]:
            self.assertIn(
                subject, text,
                f"Expected subject '{subject}' in extracted PDF text"
            )

    def test_parse_timetable_from_pdf_text(self):
        """End-to-end: extract text from PDF → parse → get subjects."""
        with self._open_fixture() as f:
            text = extract_text_from_pdf(f)
        result = parse_exam_timetable(text)
        self.assertGreaterEqual(
            len(result["subjects"]), 1,
            "Expected at least 1 subject parsed from fixture PDF"
        )

    def test_broken_pdf_returns_empty_string(self):
        broken = io.BytesIO(b"%PDF-1.4 this is garbage content")
        text = extract_text_from_pdf(broken)
        self.assertIsInstance(text, str)  # Should not raise — just return ""


# ---------------------------------------------------------------------------
# 3. DB Integration Tests — ExamSubject + Topic rows created
# ---------------------------------------------------------------------------

class UpsertExamSubjectsTests(TestCase):
    """Test that _upsert_exam_subjects() writes to ExamSubject and Topic tables."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="ocr_test@example.com",
            password="Test1234",
        )

    def _parsed_data(self):
        return {"subjects": [dict(s) for s in EXPECTED_SUBJECTS]}

    def test_creates_exam_subjects(self):
        _upsert_exam_subjects(self.user, self._parsed_data())
        count = ExamSubject.objects.filter(user=self.user).count()
        self.assertEqual(count, 5)

    def test_creates_topics_matching_subjects(self):
        _upsert_exam_subjects(self.user, self._parsed_data())
        topic_names = set(Topic.objects.filter(user=self.user).values_list("name", flat=True))
        expected_names = {s["name"] for s in EXPECTED_SUBJECTS}
        self.assertTrue(
            expected_names.issubset(topic_names),
            f"Missing topics: {expected_names - topic_names}"
        )

    def test_exam_subject_fields_stored_correctly(self):
        _upsert_exam_subjects(self.user, self._parsed_data())
        subj = ExamSubject.objects.get(user=self.user, name="Mathematics")
        self.assertEqual(str(subj.exam_date), "2026-03-25")
        self.assertEqual(subj.difficulty, "medium")

    def test_duplicate_call_does_not_create_duplicates(self):
        parsed = self._parsed_data()
        _upsert_exam_subjects(self.user, parsed)
        _upsert_exam_subjects(self.user, parsed)  # second call
        count = ExamSubject.objects.filter(user=self.user).count()
        self.assertEqual(count, 5, "Duplicate upsert should not create extra rows")

    def test_subject_missing_name_is_skipped(self):
        parsed = {"subjects": [{"name": "", "date": "2026-03-25"}]}
        _upsert_exam_subjects(self.user, parsed)
        self.assertEqual(ExamSubject.objects.filter(user=self.user).count(), 0)

    def test_subject_missing_date_is_skipped(self):
        parsed = {"subjects": [{"name": "Mathematics", "date": ""}]}
        _upsert_exam_subjects(self.user, parsed)
        self.assertEqual(ExamSubject.objects.filter(user=self.user).count(), 0)

    def test_subject_invalid_date_is_skipped(self):
        parsed = {"subjects": [{"name": "Mathematics", "date": "not-a-date"}]}
        _upsert_exam_subjects(self.user, parsed)
        self.assertEqual(ExamSubject.objects.filter(user=self.user).count(), 0)


# ---------------------------------------------------------------------------
# 4. API Integration Tests — POST PDF to /api/chatbot/converse/
# ---------------------------------------------------------------------------

class OCRAPITests(APITestCase):
    """Test the full API flow: upload a PDF → parse → create DB rows."""

    def setUp(self):
        self.url = reverse("chatbot-converse")
        self.user = User.objects.create_user(
            email="ocr_api@example.com",
            password="Test1234",
        )
        self.client.force_authenticate(self.user)

    def _fixture_file(self):
        if not os.path.exists(FIXTURE_PDF):
            self.skipTest(f"Fixture PDF not found: {FIXTURE_PDF}")
        return open(FIXTURE_PDF, "rb")

    def test_api_accepts_pdf_and_returns_parsed_data(self):
        """POST fixture PDF → 200 with tool=ocr_exam_parser and parsed subjects."""
        with self._fixture_file() as pdf:
            response = self.client.post(
                self.url,
                {"exam_image": pdf},
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("tool"), "ocr_exam_parser")
        self.assertIn("parsed", response.data)
        self.assertIn("subjects_count", response.data)

    def test_api_creates_exam_subject_rows(self):
        """PDF upload must result in ExamSubject rows in the DB."""
        with self._fixture_file() as pdf:
            self.client.post(self.url, {"exam_image": pdf}, format="multipart")

        count = ExamSubject.objects.filter(user=self.user).count()
        self.assertGreater(count, 0, "Expected ExamSubject rows after PDF upload")

    def test_api_creates_topic_rows(self):
        """PDF upload must also create Topic rows for timetable scheduling."""
        with self._fixture_file() as pdf:
            self.client.post(self.url, {"exam_image": pdf}, format="multipart")

        count = Topic.objects.filter(user=self.user).count()
        self.assertGreater(count, 0, "Expected Topic rows after PDF upload")

    def test_api_subjects_count_matches_db(self):
        """subjects_count in API response must equal DB rows created."""
        with self._fixture_file() as pdf:
            response = self.client.post(
                self.url, {"exam_image": pdf}, format="multipart"
            )

        api_count = response.data.get("subjects_count", 0)
        db_count = ExamSubject.objects.filter(user=self.user).count()
        self.assertEqual(
            api_count, db_count,
            f"API reported {api_count} subjects but DB has {db_count}"
        )

    def test_api_no_file_falls_through_to_rag(self):
        """Without an image/PDF, the endpoint should NOT return ocr_exam_parser."""
        response = self.client.post(
            self.url,
            {"message": "What subjects do I have?"},
            format="json",
        )
        self.assertNotEqual(response.data.get("tool"), "ocr_exam_parser")
