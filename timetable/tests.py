from datetime import timedelta

from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from timetable.models import CompletionCheck, FreeSlot, TimetableEntry, Topic, UserNotification
from timetable.notification_service import process_notification_pipeline

User = get_user_model()

class TimetableValidationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="Test1234")
        self.client.force_authenticate(user=self.user)
        self.url = reverse('timetable-chatbot')

    def test_duplicate_topic(self):
        data = {"topics": [{"name": "Math", "estimated_minutes": 60, "priority": 1}]}
        resp1 = self.client.post(self.url, data, format='json')
        resp2 = self.client.post(self.url, data, format='json')
        self.assertNotEqual(resp1.status_code, 400)
        self.assertEqual(resp2.status_code, 400)
        self.assertIn("Topic with this name already exists.", str(resp2.data))

    def test_overlapping_free_slot(self):
        slot1 = {"start": "2026-03-07T10:00:00Z", "end": "2026-03-07T12:00:00Z"}
        slot2 = {"start": "2026-03-07T11:00:00Z", "end": "2026-03-07T13:00:00Z"}
        data1 = {"free_slots": [slot1]}
        data2 = {"free_slots": [slot2]}
        resp1 = self.client.post(self.url, data1, format='json')
        resp2 = self.client.post(self.url, data2, format='json')
        self.assertNotEqual(resp1.status_code, 400)
        self.assertEqual(resp2.status_code, 400)
        self.assertIn("Free slot overlaps with an existing slot.", str(resp2.data))

    def test_invalid_slot_duration(self):
        slot = {"start": "2026-03-07T14:00:00Z", "end": "2026-03-07T13:00:00Z"}
        data = {"free_slots": [slot]}
        resp = self.client.post(self.url, data, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn("End time must be after start time.", str(resp.data))


class NotificationAndCompletionFlowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="notify@example.com", password="Test1234")
        self.client.force_authenticate(user=self.user)
        self.topic = Topic.objects.create(
            user=self.user,
            name="Operating Systems",
            estimated_minutes=120,
            priority=2,
        )

    def test_notification_pipeline_creates_pre_and_completion_notifications(self):
        now = timezone.now()
        future_entry = TimetableEntry.objects.create(
            user=self.user,
            topic=self.topic,
            start=now + timedelta(minutes=8),
            end=now + timedelta(minutes=38),
        )
        past_entry = TimetableEntry.objects.create(
            user=self.user,
            topic=self.topic,
            start=now - timedelta(minutes=40),
            end=now - timedelta(minutes=10),
        )

        summary = process_notification_pipeline(now=now)

        self.assertGreaterEqual(summary["pre_reminders"], 1)
        self.assertGreaterEqual(summary["completion_checks"], 1)
        self.assertTrue(
            UserNotification.objects.filter(
                entry=future_entry,
                notification_type=UserNotification.TYPE_PRE_REMINDER,
            ).exists()
        )
        self.assertTrue(CompletionCheck.objects.filter(entry=past_entry).exists())

    def test_completion_response_marks_entry_done(self):
        now = timezone.now()
        entry = TimetableEntry.objects.create(
            user=self.user,
            topic=self.topic,
            start=now - timedelta(minutes=50),
            end=now - timedelta(minutes=20),
        )

        url = reverse("timetable-entry-completion-response", kwargs={"pk": entry.id})
        response = self.client.post(
            url,
            {
                "completed": True,
                "response_text": "I completed this topic",
                "quiz_answer": "Processes can run concurrently",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "completed")
        entry.refresh_from_db()
        self.topic.refresh_from_db()
        self.assertTrue(entry.done)
        self.assertGreater(self.topic.completed_minutes, 0)

    def test_completion_response_false_triggers_reschedule(self):
        now = timezone.now()
        entry = TimetableEntry.objects.create(
            user=self.user,
            topic=self.topic,
            start=now - timedelta(minutes=60),
            end=now - timedelta(minutes=30),
        )
        FreeSlot.objects.create(
            user=self.user,
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=2),
        )

        url = reverse("timetable-entry-completion-response", kwargs={"pk": entry.id})
        response = self.client.post(
            url,
            {
                "completed": False,
                "response_text": "I could not finish because I was busy",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "rescheduled")
        self.assertIn("strategy", response.data)
        check = CompletionCheck.objects.get(entry=entry)
        self.assertTrue(check.auto_rescheduled)

    def test_notifications_list_and_mark_read(self):
        now = timezone.now()
        entry = TimetableEntry.objects.create(
            user=self.user,
            topic=self.topic,
            start=now + timedelta(minutes=10),
            end=now + timedelta(minutes=40),
        )
        notification = UserNotification.objects.create(
            user=self.user,
            entry=entry,
            notification_type=UserNotification.TYPE_PRE_REMINDER,
            title="Reminder",
            message="Study soon",
            sent_at=now,
        )

        list_url = reverse("timetable-notifications")
        list_resp = self.client.get(list_url)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(len(list_resp.data) >= 1)

        read_url = reverse("timetable-notification-read", kwargs={"pk": notification.id})
        read_resp = self.client.patch(read_url, {"is_actioned": True}, format="json")
        self.assertEqual(read_resp.status_code, status.HTTP_200_OK)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertTrue(notification.is_actioned)
