import os
import django
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from timetable.models import Topic, FreeSlot, TimetableEntry
from chatbot.services.timetable_generator import generate_timetable_for_user
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()
user = User.objects.first()

if not user:
    user = User.objects.create_user(email='test@example.com', password='password123')

# Clean up
Topic.objects.filter(user=user).delete()
FreeSlot.objects.filter(user=user).delete()
TimetableEntry.objects.filter(user=user).delete()

print("Setting up test data...")
t1 = Topic.objects.create(user=user, name='OS', estimated_minutes=60)
# Slot for 2 sessions
fs = FreeSlot.objects.create(user=user, start=timezone.now() + timedelta(days=1, hours=19), end=timezone.now() + timedelta(days=1, hours=22))

print("Generating timetable...")
entries = generate_timetable_for_user(user, max_chunk_minutes=30)

print(f"Generated {len(entries)} entries.")
for e in entries:
    print(f"Entry {e.id}: topic={e.topic.name}, label={e.session_label}")
    # Verify that session_label is populated
    assert e.session_label is not None
    assert "OS" in e.session_label

print("Verification complete!")
