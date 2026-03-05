from django.urls import reverse
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from timetable.models import Topic, FreeSlot
from rest_framework import serializers

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
