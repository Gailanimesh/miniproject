import os
import django
from datetime import timedelta, datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from timetable.models import Topic, FreeSlot, TimetableEntry
from chatbot.services.timetable_generator import generate_timetable_for_user
from chatbot.services.feedback_analyzer import adaptive_reschedule_for_user
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()
user = User.objects.first() or User.objects.create_user(email='test_feedback@example.com', password='password123')

# Clean up
Topic.objects.filter(user=user).delete()
FreeSlot.objects.filter(user=user).delete()
TimetableEntry.objects.filter(user=user).delete()

print("--- Testing Advanced Revision & Exam-Eve ---")
# OS exam tomorrow
t1 = Topic.objects.create(user=user, name='OS', estimated_minutes=60, target_date=timezone.now().date() + timedelta(days=1))
# DS exam in 3 days
t2 = Topic.objects.create(user=user, name='DS', estimated_minutes=120, target_date=timezone.now().date() + timedelta(days=3))

# Slot today
fs = FreeSlot.objects.create(user=user, start=timezone.now().replace(hour=19, minute=0), end=timezone.now().replace(hour=22, minute=0))

print("Generating initial timetable...")
entries = list(generate_timetable_for_user(user))
print(f"Generated {len(entries)} entries.")
for e in entries:
    print(f"  {e.start:%H:%M}-{e.end:%H:%M}: {e.session_label}")

# Verify OS (Exam tomorrow) dominates today
assert any("OS" in e.session_label for e in entries)
os_entries = [e for e in entries if "OS" in e.session_label]
print(f"Found {len(os_entries)} OS entries on exam-eve.")

print("\n--- Testing Feedback Loop (Rescheduling) ---")
# Simulate marking OS session as "too hard"
target_entry = os_entries[0]
print(f"Simulating 'hard/difficult' feedback for entry {target_entry.id}...")
result = adaptive_reschedule_for_user(user, reason="This OS topic is very difficult and complex.", entry_id=target_entry.id)

print(f"Reschedule result: {result['message']}")
print(f"Strategy: {result['strategy'].action}")
print(f"Extra minutes added: {result['extra_minutes']}")

# Check if OS priority or estimated_minutes increased
t1.refresh_from_db()
print(f"Updated OS: priority={t1.priority}, estimate={t1.estimated_minutes}")

new_entries = result['entries']
print(f"New generated entries: {len(new_entries)}")
for e in new_entries:
    print(f"  {e.start:%H:%M}-{e.end:%H:%M}: {e.session_label}")

# Verify that smaller chunks or more sessions are used if strategy was 'split'
if result['strategy'].max_chunk_minutes < 60:
    print("Success: Strategy reduced chunk size!")

print("\nVerification complete!")
