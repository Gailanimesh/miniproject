from django.urls import reverse
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model

User = get_user_model()

class FullApiFlowTests(APITestCase):
    def setUp(self):
        self.register_url = reverse('auth-register')
        self.token_url = reverse('token_obtain_pair')
        self.refresh_url = reverse('token_refresh')
        self.me_url = reverse('auth-me')
        self.logout_url = reverse('auth-logout')
        self.timetable_url = reverse('timetable-chatbot')
        self.entries_url = reverse('timetable-entries')
        self.chatbot_url = reverse('chatbot-converse')

        self.user_data = {
            "email": "testuser@example.com",
            "password": "Test1234",
            "first_name": "Test",
            "last_name": "User"
        }

    def test_full_flow(self):
        # Register
        resp = self.client.post(self.register_url, self.user_data, format='json')
        self.assertIn(resp.status_code, (200, 201))

        # Login
        resp = self.client.post(self.token_url, {
            "email": self.user_data["email"],
            "password": self.user_data["password"]
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        access = resp.data["access"]
        refresh = resp.data["refresh"]

        # Get user info
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get(self.me_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("email"), self.user_data["email"])

        # Timetable: Save topics and free slots
        data = {
            "topics": [{"name": "Math", "estimated_minutes": 60, "priority": 1}],
            "free_slots": [{"start": "2026-03-07T10:00:00Z", "end": "2026-03-07T12:00:00Z"}]
        }
        resp = self.client.post(self.timetable_url, data, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(len(resp.data) > 0)

        # List timetable entries
        resp = self.client.get(self.entries_url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(isinstance(resp.data, list))

        # Chatbot converse
        resp = self.client.post(self.chatbot_url, {"message": "hi"}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn("response", resp.data)

        # Refresh token
        resp = self.client.post(self.refresh_url, {"refresh": refresh}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        refresh = resp.data.get("refresh", refresh)

        # Logout
        resp = self.client.post(self.logout_url, {"refresh": refresh}, format='json')
        self.assertIn(resp.status_code, (200, 204))