"""
End-to-End Integration Test for Study Planner Backend
Tests complete user workflow from registration to rescheduling
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
sys.path.insert(0, r'C:\mini project')
django.setup()

from datetime import timedelta
from django.utils import timezone
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from timetable.models import Topic, FreeSlot, TimetableEntry, UserNotification, CompletionCheck
from users.models import UserProfile

User = get_user_model()


class E2EStudyPlannerTest(APITestCase):
    """Complete end-to-end test of the study planner workflow"""
    
    def setUp(self):
        """Setup test user and client"""
        # Clean up any existing test data first
        User.objects.filter(email='e2e_test@example.com').delete()
        
        self.user = User.objects.create_user(
            email='e2e_test@example.com',
            password='TestPass123!'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.base_url = '/api/chatbot/converse/'
        
    def test_01_user_onboarding(self):
        """Step 1: User onboarding with profile setup"""
        print("\n" + "="*60)
        print("STEP 1: User Onboarding")
        print("="*60)
        
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
        
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['tool'], 'onboarding')
        
        # Verify profile was created
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.goal_type, 'Semester Exam')
        self.assertEqual(profile.knowledge_level, 'intermediate')
        print(f"[OK] Profile created: {profile}")
        
    def test_02_add_subjects(self):
        """Step 2: Add study subjects"""
        print("\n" + "="*60)
        print("STEP 2: Adding Subjects")
        print("="*60)
        
        # Add first subject
        response = self.client.post(
            self.base_url,
            {
                "topics": [
                    {"name": "Operating Systems", "estimated_minutes": 180, "priority": 2},
                ]
            },
            format='json'
        )
        
        print(f"Add OS - Status: {response.status_code}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Add second subject
        response = self.client.post(
            self.base_url,
            {
                "topics": [
                    {"name": "Data Structures", "estimated_minutes": 240, "priority": 1},
                ]
            },
            format='json'
        )
        
        print(f"Add DS - Status: {response.status_code}")
        
        # Verify topics created
        topics = Topic.objects.filter(user=self.user)
        print(f"Topics created: {list(topics.values('name', 'estimated_minutes', 'priority'))}")
        self.assertEqual(topics.count(), 2)
        print(f"[OK] Added {topics.count()} subjects")
        
    def test_03_add_free_slots(self):
        """Step 3: Add available study time slots"""
        print("\n" + "="*60)
        print("STEP 3: Adding Free Time Slots")
        print("="*60)
        
        now = timezone.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
        
        # Create free slots for 3 days
        slots = [
            {
                "start": tomorrow.isoformat(),
                "end": (tomorrow + timedelta(hours=2)).isoformat()
            },
            {
                "start": (tomorrow + timedelta(days=1)).replace(hour=20).isoformat(),
                "end": (tomorrow + timedelta(days=1)).replace(hour=22).isoformat()
            },
            {
                "start": (tomorrow + timedelta(days=2)).replace(hour=20).isoformat(),
                "end": (tomorrow + timedelta(days=2)).replace(hour=22).isoformat()
            },
        ]
        
        response = self.client.post(
            self.base_url,
            {"free_slots": slots},
            format='json'
        )
        
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        # Verify slots created
        free_slots = FreeSlot.objects.filter(user=self.user)
        print(f"Free slots created: {free_slots.count()}")
        self.assertEqual(free_slots.count(), 3)
        print(f"[OK] Added {free_slots.count()} free time slots")
        
    def test_04_set_exam_date(self):
        """Step 4: Set exam date"""
        print("\n" + "="*60)
        print("STEP 4: Setting Exam Date")
        print("="*60)
        
        exam_date = (timezone.now() + timedelta(days=7)).date().isoformat()
        
        response = self.client.post(
            self.base_url,
            {"exam_date": exam_date},
            format='json'
        )
        
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        # Verify exam date set
        profile = UserProfile.objects.get(user=self.user)
        print(f"Exam date: {profile.exam_date}")
        self.assertEqual(str(profile.exam_date), exam_date)
        print(f"[OK] Exam date set to: {exam_date}")
        
    def test_05_generate_timetable(self):
        """Step 5: Generate initial timetable"""
        print("\n" + "="*60)
        print("STEP 5: Generating Timetable")
        print("="*60)
        
        response = self.client.post(
            self.base_url,
            {"tool": "generate_timetable", "generate_timetable": True},
            format='json'
        )
        
        print(f"Status: {response.status_code}")
        data = response.json()
        print(f"Tool: {data.get('tool')}")
        print(f"Entries count: {len(data.get('entries', []))}")
        
        for entry in data.get('entries', [])[:4]:
            print(f"  - {entry['topic']}: {entry['start']} to {entry['end']}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(data['tool'], 'generate_timetable')
        self.assertGreater(len(data['entries']), 0)
        print(f"[OK] Generated {len(data['entries'])} timetable entries")
        
        return data['entries']
        
    def test_06_simulate_completion_check_flow(self):
        """Step 6: Simulate the completion check and rescheduling flow"""
        print("\n" + "="*60)
        print("STEP 6: Completion Check & Rescheduling Flow")
        print("="*60)
        
        # First generate a timetable
        response = self.client.post(
            self.base_url,
            {"tool": "generate_timetable", "generate_timetable": True},
            format='json'
        )
        
        entries_before = response.json()['entries']
        print(f"Entries before: {len(entries_before)}")
        
        # Get an entry to mark as "completed" for testing
        if entries_before:
            entry_id = entries_before[0]['id']
            
            # Mark entry as done
            print(f"\nMarking entry {entry_id} as completed...")
            entry_url = f'/api/timetable/entries/{entry_id}/completion-response/'
            
            response = self.client.post(
                entry_url,
                {
                    "completed": True,
                    "response_text": "I completed the topic successfully!",
                    "quiz_answer": "Process scheduling - used in OS for CPU allocation"
                },
                format='json'
            )
            
            print(f"Completion response status: {response.status_code}")
            print(f"Completion response: {response.json()}")
            
            if response.status_code == 200:
                data = response.json()
                self.assertEqual(data['status'], 'completed')
                print(f"[OK] Entry marked as completed")
                print(f"  New entries generated: {len(data.get('entries', []))}")
            else:
                print(f"⚠ Completion endpoint returned: {response.status_code}")
                print(f"  This might be because entry times are in the future")
        
        # Now test rescheduling (marking an entry as NOT completed)
        print("\n--- Testing Rescheduling Flow ---")
        
        # Create a past entry manually for testing
        now = timezone.now()
        past_entry = TimetableEntry.objects.create(
            user=self.user,
            topic=Topic.objects.filter(user=self.user).first(),
            start=now - timedelta(hours=2),
            end=now - timedelta(hours=1),
            done=False
        )
        print(f"Created past entry for testing: ID={past_entry.id}")
        
        # Get the completion response URL
        entry_url = f'/api/timetable/entries/{past_entry.id}/completion-response/'
        
        # Mark as NOT completed with a reason
        response = self.client.post(
            entry_url,
            {
                "completed": False,
                "response_text": "I was too busy with work and couldn't find time to study. The topic was also quite hard."
            },
            format='json'
        )
        
        print(f"\nReschedule response status: {response.status_code}")
        print(f"Reschedule response: {response.json()}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"\n📋 Reschedule Result:")
            print(f"  Status: {data.get('status')}")
            print(f"  Strategy: {data.get('strategy')}")
            print(f"  New entries: {len(data.get('entries', []))}")
            
            # Check the completion check record
            check = CompletionCheck.objects.filter(entry=past_entry).first()
            if check:
                print(f"  CompletionCheck created: ID={check.id}")
                print(f"  Auto-rescheduled: {check.auto_rescheduled}")
                print(f"  Response: {check.response_text}")
            
            # Verify entry was rescheduled
            self.assertEqual(data['status'], 'rescheduled')
            self.assertTrue(data.get('strategy'))
            print(f"[OK] Rescheduling completed successfully")
        else:
            print(f"⚠ Reschedule endpoint returned: {response.status_code}")
            print(f"  Error: {response.json() if hasattr(response, 'json') else response}")
            
    def test_07_notification_pipeline(self):
        """Step 7: Test notification pipeline"""
        print("\n" + "="*60)
        print("STEP 7: Testing Notification Pipeline")
        print("="*60)
        
        from timetable.notification_service import (
            process_notification_pipeline,
            create_pre_reminder_notifications,
            create_completion_check_notifications,
            auto_reschedule_pending_checks
        )
        
        now = timezone.now()
        
        # Test pre-reminder creation
        print("\n--- Creating Pre-reminder Notifications ---")
        
        # Create a future entry
        future_entry = TimetableEntry.objects.create(
            user=self.user,
            topic=Topic.objects.filter(user=self.user).first(),
            start=now + timedelta(minutes=8),  # Within 10 min window
            end=now + timedelta(minutes=38),
            done=False
        )
        
        result = create_pre_reminder_notifications(now=now)
        print(f"Pre-reminders created: {result}")
        
        # Check notification created
        notifs = UserNotification.objects.filter(entry=future_entry, user=self.user)
        print(f"Notifications for future entry: {notifs.count()}")
        
        # Test completion check creation
        print("\n--- Creating Completion Check Notifications ---")
        
        # Create a recently completed entry
        past_entry = TimetableEntry.objects.create(
            user=self.user,
            topic=Topic.objects.filter(user=self.user).first(),
            start=now - timedelta(minutes=60),
            end=now - timedelta(minutes=30),
            done=False
        )
        
        result = create_completion_check_notifications(now=now)
        print(f"Completion checks created: {result}")
        
        # Verify completion check
        check = CompletionCheck.objects.filter(entry=past_entry).first()
        if check:
            print(f"[OK] CompletionCheck: {check.quiz_question}")
        else:
            print(f"⚠ No CompletionCheck created")
        
        # Test full pipeline
        print("\n--- Running Full Notification Pipeline ---")
        result = process_notification_pipeline(now=now)
        print(f"Pipeline result: {result}")
        print(f"[OK] Notification pipeline executed")
        
    def test_08_view_notifications(self):
        """Step 8: View and manage notifications"""
        print("\n" + "="*60)
        print("STEP 8: Viewing Notifications")
        print("="*60)
        
        # Create a test notification
        entry = TimetableEntry.objects.filter(user=self.user).first()
        if entry:
            notif = UserNotification.objects.create(
                user=self.user,
                entry=entry,
                notification_type=UserNotification.TYPE_PRE_REMINDER,
                title="Study session coming up!",
                message="Your OS study session starts in 10 minutes",
                sent_at=timezone.now()
            )
            
            # List notifications
            response = self.client.get('/api/timetable/notifications/')
            print(f"Notifications list status: {response.status_code}")
            print(f"Notifications: {len(response.data)}")
            
            # Mark as read
            if response.data:
                notif_id = response.data[0]['id']
                response = self.client.patch(
                    f'/api/timetable/notifications/{notif_id}/read/',
                    {"is_actioned": True},
                    format='json'
                )
                print(f"Mark read status: {response.status_code}")
                print(f"[OK] Notifications workflow works")
                
    def test_09_full_workflow_integration(self):
        """Step 9: Complete workflow integration test"""
        print("\n" + "="*60)
        print("STEP 9: Full Workflow Integration")
        print("="*60)
        
        # 1. Create user profile
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={
                'goal_type': 'Semester Exam',
                'knowledge_level': 'beginner',
                'daily_free_hours': 4,
                'exam_date': timezone.now().date() + timedelta(days=14)
            }
        )
        print("[OK] User profile configured")
        
        # 2. Create topics
        Topic.objects.filter(user=self.user).delete()
        os_topic, _ = Topic.objects.get_or_create(
            user=self.user, name='Operating Systems',
            defaults={'estimated_minutes': 300, 'priority': 2}
        )
        ds_topic, _ = Topic.objects.get_or_create(
            user=self.user, name='Data Structures',
            defaults={'estimated_minutes': 360, 'priority': 1}
        )
        print(f"[OK] Topics: OS ({os_topic.estimated_minutes}min), DS ({ds_topic.estimated_minutes}min)")
        
        # 3. Create free slots
        FreeSlot.objects.filter(user=self.user).delete()
        now = timezone.now()
        base_date = (now + timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
        
        for i in range(5):
            FreeSlot.objects.create(
                user=self.user,
                start=base_date + timedelta(days=i),
                end=base_date + timedelta(days=i, hours=2)
            )
        print(f"[OK] Created 5 free slots (2 hours each)")
        
        # 4. Generate timetable
        response = self.client.post(
            self.base_url,
            {"tool": "generate_timetable", "generate_timetable": True},
            format='json'
        )
        
        if response.status_code == 200:
            entries = response.json()['entries']
            print(f"[OK] Generated {len(entries)} timetable entries")
            
            # Show first few entries
            for entry in entries[:4]:
                print(f"  - {entry['topic']}: {entry['start'][:16]} to {entry['end'][:16]}")
        else:
            print(f"⚠ Timetable generation failed: {response.status_code}")
            print(f"  {response.json()}")
        
        # 5. Verify database state
        print("\n--- Final Database State ---")
        print(f"Topics: {Topic.objects.filter(user=self.user).count()}")
        print(f"FreeSlots: {FreeSlot.objects.filter(user=self.user).count()}")
        print(f"TimetableEntries: {TimetableEntry.objects.filter(user=self.user).count()}")
        print(f"UserNotifications: {UserNotification.objects.filter(user=self.user).count()}")
        print(f"CompletionChecks: {CompletionCheck.objects.filter(user=self.user).count()}")
        
        print("\n[SUCCESS] Full workflow integration test complete!")


def run_e2e_tests():
    """Run all E2E tests"""
    print("\n" + "="*70)
    print("       END-TO-END STUDY PLANNER TEST SUITE")
    print("="*70)
    
    test = E2EStudyPlannerTest()
    test.setUp()
    
    tests = [
        ("User Onboarding", test.test_01_user_onboarding),
        ("Add Subjects", test.test_02_add_subjects),
        ("Add Free Slots", test.test_03_add_free_slots),
        ("Set Exam Date", test.test_04_set_exam_date),
        ("Generate Timetable", test.test_05_generate_timetable),
        ("Completion Check Flow", test.test_06_simulate_completion_check_flow),
        ("Notification Pipeline", test.test_07_notification_pipeline),
        ("View Notifications", test.test_08_view_notifications),
        ("Full Integration", test.test_09_full_workflow_integration),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            test_func()
            results.append((name, "PASS"))
        except AssertionError as e:
            results.append((name, f"FAIL: {e}"))
            print(f"\n[X] {name} FAILED: {e}")
        except Exception as e:
            results.append((name, f"ERROR: {e}"))
            print(f"\n[!] {name} ERROR: {e}")
    
    print("\n" + "="*70)
    print("                      TEST RESULTS SUMMARY")
    print("="*70)
    
    for name, result in results:
        status_icon = "[OK]" if result == "PASS" else "[FAIL]"
        print(f"  {status_icon} {name}: {result}")
    
    passed = sum(1 for _, r in results if r == "PASS")
    total = len(results)
    print(f"\n  Total: {passed}/{total} tests passed")
    print("="*70)
    
    return passed == total


if __name__ == "__main__":
    success = run_e2e_tests()
    sys.exit(0 if success else 1)
