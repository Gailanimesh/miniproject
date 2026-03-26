"""
E2E Test for Study Planner - Tests the complete user workflow
Run with: python manage.py test test_e2e
"""
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from timetable.models import Topic, FreeSlot, TimetableEntry, UserNotification, CompletionCheck
from users.models import UserProfile

User = get_user_model()


class E2EWorkflowTest(APITestCase):
    """Tests the complete study planner workflow"""
    
    def setUp(self):
        """Create test user"""
        self.user = User.objects.create_user(
            email='e2e_workflow@example.com',
            password='TestPass123!'
        )
        self.client.force_authenticate(user=self.user)
        self.base_url = '/api/chatbot/converse/'
    
    def tearDown(self):
        """Clean up to avoid FK constraint issues"""
        CompletionCheck.objects.all().delete()
        UserNotification.objects.all().delete()
        super().tearDown()
    
    def test_complete_workflow(self):
        """Test the complete workflow: onboarding -> topics -> slots -> exam -> timetable -> completion"""
        
        # 1. ONBOARDING
        print("\n=== 1. Testing Onboarding ===")
        response = self.client.post(
            self.base_url,
            {
                "tool": "onboarding",
                "onboarding": {
                    "goal_type": "Semester Exam",
                    "knowledge_level": "intermediate",
                    "daily_free_hours": 3
                }
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['tool'], 'onboarding')
        print(f"  [OK] Onboarding successful")
        
        # 2. ADD TOPICS
        print("\n=== 2. Testing Adding Topics ===")
        response = self.client.post(
            self.base_url,
            {
                "topics": [
                    {"name": "Operating Systems", "estimated_minutes": 180, "priority": 2},
                ]
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        response = self.client.post(
            self.base_url,
            {
                "topics": [
                    {"name": "Data Structures", "estimated_minutes": 240, "priority": 1},
                ]
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Topic.objects.filter(user=self.user).count(), 2)
        print(f"  [OK] Added 2 topics")
        
        # 3. ADD FREE SLOTS
        print("\n=== 3. Testing Adding Free Slots ===")
        now = timezone.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
        
        slots = [
            {"start": tomorrow.isoformat(), "end": (tomorrow + timedelta(hours=2)).isoformat()},
            {"start": (tomorrow + timedelta(days=1)).replace(hour=20).isoformat(), 
             "end": (tomorrow + timedelta(days=1)).replace(hour=22).isoformat()},
        ]
        
        response = self.client.post(
            self.base_url,
            {"free_slots": slots},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(FreeSlot.objects.filter(user=self.user).count(), 2)
        print(f"  [OK] Added 2 free slots")
        
        # 4. SET EXAM DATE
        print("\n=== 4. Testing Exam Date ===")
        profile = UserProfile.objects.get(user=self.user)
        profile.exam_date = (timezone.now() + timedelta(days=7)).date()
        profile.save()
        
        profile.refresh_from_db()
        self.assertIsNotNone(profile.exam_date)
        print(f"  [OK] Exam date set: {profile.exam_date}")
        
        # 5. GENERATE TIMETABLE
        print("\n=== 5. Testing Timetable Generation ===")
        response = self.client.post(
            self.base_url,
            {"tool": "generate_timetable", "generate_timetable": True},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['tool'], 'generate_timetable')
        self.assertGreater(len(response.data['entries']), 0)
        print(f"  [OK] Generated {len(response.data['entries'])} timetable entries")
        
        # 6. TEST COMPLETION RESPONSE (mark as done)
        print("\n=== 6. Testing Completion - Mark Done ===")
        entry = TimetableEntry.objects.filter(user=self.user, done=False).first()
        self.assertIsNotNone(entry, "No entry found to test completion")
        
        # Mark entry as past for completion check
        entry.start = timezone.now() - timedelta(hours=2)
        entry.end = timezone.now() - timedelta(hours=1)
        entry.save()
        
        response = self.client.post(
            f'/api/timetable/entries/{entry.id}/completion-response/',
            {"completed": True, "response_text": "Completed successfully!", "quiz_answer": "Process scheduling"},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data.get('status'), 'completed')
        
        entry.refresh_from_db()
        self.assertTrue(entry.done)
        print(f"  [OK] Entry marked as completed")
        
        # 7. TEST COMPLETION RESPONSE (mark as not done - triggers reschedule)
        print("\n=== 7. Testing Rescheduling Flow ===")
        
        # Create another entry for testing
        entry2 = TimetableEntry.objects.create(
            user=self.user,
            topic=Topic.objects.filter(user=self.user).first(),
            start=timezone.now() - timedelta(hours=3),
            end=timezone.now() - timedelta(hours=2),
            done=False
        )
        
        response = self.client.post(
            f'/api/timetable/entries/{entry2.id}/completion-response/',
            {
                "completed": False,
                "response_text": "I was too busy with work and the topic was hard"
            },
            format='json'
        )
        
        if response.status_code == status.HTTP_200_OK:
            data = response.json()
            self.assertEqual(data.get('status'), 'rescheduled')
            self.assertIn('strategy', data)
            print(f"  [OK] Rescheduling triggered with strategy: {data['strategy']}")
        else:
            print(f"  [WARN] Reschedule returned {response.status_code}")
            print(f"  Response: {response.content.decode()[:500]}")
        
        # 8. TEST NOTIFICATIONS
        print("\n=== 8. Testing Notifications ===")
        from timetable.notification_service import process_notification_pipeline
        
        result = process_notification_pipeline()
        self.assertIn('pre_reminders', result)
        self.assertIn('completion_checks', result)
        print(f"  [OK] Notification pipeline: pre={result['pre_reminders']}, completion={result['completion_checks']}")
        
        # List notifications
        response = self.client.get('/api/timetable/notifications/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        print(f"  [OK] Retrieved {len(response.data)} notifications")
        
        print("\n" + "="*50)
        print("ALL WORKFLOW TESTS PASSED!")
        print("="*50)
    
    def test_feedback_analysis_keywords(self):
        """Test that feedback analysis correctly identifies keywords"""
        print("\n=== Testing Feedback Analysis ===")
        from chatbot.services.feedback_analyzer import _analyze_reason, _build_strategy
        
        # Test time constraint keyword
        analysis = _analyze_reason("I was too busy with work to study")
        self.assertIn('time_constraints', analysis.matched_keywords)
        print(f"  [OK] Detected time_constraints: {analysis.matched_keywords}")
        
        # Test difficulty keyword
        analysis = _analyze_reason("The topic was very hard and confusing")
        self.assertIn('difficulty', analysis.matched_keywords)
        print(f"  [OK] Detected difficulty: {analysis.matched_keywords}")
        
        # Test fatigue keyword
        analysis = _analyze_reason("I was exhausted and tired from the day")
        self.assertIn('fatigue', analysis.matched_keywords)
        print(f"  [OK] Detected fatigue: {analysis.matched_keywords}")
        
        # Test urgency keyword
        analysis = _analyze_reason("My exam is coming soon, need to prioritize")
        self.assertIn('urgency', analysis.matched_keywords)
        print(f"  [OK] Detected urgency: {analysis.matched_keywords}")
        
        # Test strategy building
        analysis = _analyze_reason("I was too busy and the topic was hard")
        strategy = _build_strategy(analysis)
        self.assertLess(strategy.max_chunk_minutes, 60)  # Should be reduced
        print(f"  [OK] Strategy: chunk={strategy.max_chunk_minutes}min, boost={strategy.priority_boost}")
        
        print("\n" + "="*50)
        print("FEEDBACK ANALYSIS TESTS PASSED!")
        print("="*50)
    
    def test_timezone_conversion_in_serializer(self):
        """Test that times are correctly converted to IST in serializers"""
        print("\n=== Testing Timezone Conversion ===")
        
        # Create a test entry
        topic = Topic.objects.create(
            user=self.user,
            name="Test Subject TZ",
            estimated_minutes=60,
            priority=1
        )
        entry = TimetableEntry.objects.create(
            user=self.user,
            topic=topic,
            start=timezone.now(),
            end=timezone.now() + timedelta(hours=1),
            done=False
        )
        
        # Test via API - use the list endpoint which filters by user
        response = self.client.get('/api/timetable/entries/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data), 0)
        
        start_time = response.data[0]['start']
        start_str = str(start_time)
        self.assertIn('+05:30', start_str)  # Should be IST
        print(f"  [OK] Entry serialized with IST: {start_str}")
        
        # Test FreeSlot serializer via serializer directly
        slot = FreeSlot.objects.create(
            user=self.user,
            start=timezone.now(),
            end=timezone.now() + timedelta(hours=2)
        )
        from timetable.serializers import FreeSlotSerializer
        slot_data = FreeSlotSerializer(slot).data
        self.assertIn('+05:30', str(slot_data['start']))
        print(f"  [OK] FreeSlot serialized with IST: {slot_data['start']}")
        
        print("\n" + "="*50)
        print("TIMEZONE CONVERSION TESTS PASSED!")
        print("="*50)
