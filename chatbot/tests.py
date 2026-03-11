from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Conversation, Message
from timetable.models import FreeSlot, TimetableEntry, Topic
from users.models import UserProfile

User = get_user_model()


class ChatbotConversationTests(APITestCase):
    def setUp(self):
        self.url = reverse("chatbot-converse")
        self.conversations_url = reverse("chatbot-conversations")
        self.user = User.objects.create_user(
            email="planner@example.com",
            password="Test1234",
        )
        self.client.force_authenticate(self.user)

    def test_onboarding_update(self):
        payload = {
            "tool": "onboarding",
            "onboarding": {
                "goal_type": "GATE",
                "knowledge_level": "beginner",
                "daily_free_hours": 3,
            },
        }

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["tool"], "onboarding")
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.goal_type, "GATE")
        self.assertEqual(profile.knowledge_level, "beginner")
        self.assertEqual(profile.daily_free_hours, 3)

    def test_generate_timetable_tool(self):
        UserProfile.objects.create(
            user=self.user,
            goal_type="Semester Exam",
            knowledge_level="intermediate",
            daily_free_hours=2,
        )
        Topic.objects.create(
            user=self.user,
            name="Mathematics",
            estimated_minutes=120,
            priority=2,
        )

        start = timezone.now() + timedelta(hours=1)
        FreeSlot.objects.create(
            user=self.user,
            start=start,
            end=start + timedelta(hours=2),
        )

        response = self.client.post(
            self.url,
            {"tool": "generate_timetable", "generate_timetable": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["tool"], "generate_timetable")
        self.assertTrue(len(response.data["entries"]) > 0)
        self.assertIn("timetable", response.data)
        self.assertIn("generation", response.data)
        self.assertIn("algorithm", response.data["timetable"])

    def test_rag_chat_fallback(self):
        response = self.client.post(
            self.url,
            {"message": "How do I stay consistent with study?"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["tool"], "rag_chat")
        self.assertIn("response", response.data)
        self.assertIn("conversation_id", response.data)

        conversation_id = response.data["conversation_id"]
        self.assertTrue(
            Conversation.objects.filter(id=conversation_id, user=self.user).exists()
        )
        self.assertEqual(
            Message.objects.filter(conversation_id=conversation_id).count(),
            2,
        )

    def test_conversation_history_endpoints(self):
        response = self.client.post(
            self.url,
            {"message": "I need a focus plan for math."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        conversation_id = response.data["conversation_id"]
        list_response = self.client.get(self.conversations_url)
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(list_response.data) >= 1)

        messages_url = reverse(
            "chatbot-conversation-messages",
            kwargs={"conversation_id": conversation_id},
        )
        messages_response = self.client.get(messages_url)
        self.assertEqual(messages_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(messages_response.data), 2)
        self.assertEqual(messages_response.data[0]["sender"], "user")
        self.assertEqual(messages_response.data[1]["sender"], "bot")

    def test_adaptive_reschedule_tool(self):
        UserProfile.objects.create(
            user=self.user,
            goal_type="Semester Exam",
            knowledge_level="intermediate",
            daily_free_hours=3,
        )
        topic = Topic.objects.create(
            user=self.user,
            name="Data Structures",
            estimated_minutes=120,
            priority=1,
        )

        # Two future slots ensure rescheduling has enough space.
        now = timezone.now()
        FreeSlot.objects.create(
            user=self.user,
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=3),
        )
        FreeSlot.objects.create(
            user=self.user,
            start=now + timedelta(hours=5),
            end=now + timedelta(hours=7),
        )

        gen_response = self.client.post(
            self.url,
            {"tool": "generate_timetable", "generate_timetable": True},
            format="json",
        )
        self.assertEqual(gen_response.status_code, status.HTTP_200_OK)
        target_entry_id = gen_response.data["entries"][0]["id"]

        response = self.client.post(
            self.url,
            {
                "tool": "adaptive_reschedule",
                "adaptive_reschedule": {
                    "entry_id": target_entry_id,
                    "reason": "I was busy and had no time yesterday.",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["tool"], "adaptive_reschedule")
        self.assertEqual(response.data["strategy"]["max_chunk_minutes"], 30)
        self.assertTrue(len(response.data["entries"]) > 0)
        self.assertIn("feedback_analysis", response.data)
        self.assertIn("timetable", response.data)
        self.assertIn("generation", response.data)
        self.assertIn("topic_adjustments", response.data)
        self.assertIn("algorithm", response.data["timetable"])
        self.assertIn("ml_training", response.data["generation"])
        self.assertIn("trained", response.data["generation"]["ml_training"])
        self.assertIn("source", response.data["generation"]["ml_training"]["training"])

        topic.refresh_from_db()
        self.assertGreaterEqual(topic.priority, 2)

        # Ensure regenerated entries were split to <= 30-minute chunks.
        for entry in TimetableEntry.objects.filter(user=self.user, start__gte=now):
            duration = int((entry.end - entry.start).total_seconds() // 60)
            self.assertLessEqual(duration, 30)

    def test_adaptive_reschedule_rejects_invalid_entry_id(self):
        response = self.client.post(
            self.url,
            {
                "tool": "adaptive_reschedule",
                "adaptive_reschedule": {
                    "entry_id": "abc",
                    "reason": "I was busy yesterday.",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["tool"], "adaptive_reschedule")
        self.assertIn("entry_id must be an integer", response.data["error"])

    def test_adaptive_reschedule_auto_mode_without_payload(self):
        UserProfile.objects.create(
            user=self.user,
            goal_type="Semester Exam",
            knowledge_level="intermediate",
            daily_free_hours=2,
        )
        Topic.objects.create(
            user=self.user,
            name="Operating Systems",
            estimated_minutes=90,
            priority=1,
        )

        now = timezone.now()
        FreeSlot.objects.create(
            user=self.user,
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=3),
        )

        gen_response = self.client.post(
            self.url,
            {"tool": "generate_timetable", "generate_timetable": True},
            format="json",
        )
        self.assertEqual(gen_response.status_code, status.HTTP_200_OK)

        response = self.client.post(
            self.url,
            {"tool": "adaptive_reschedule"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["tool"], "adaptive_reschedule")
        self.assertIn("generation", response.data)
        self.assertIn("timetable", response.data)
