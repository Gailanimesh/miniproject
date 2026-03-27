import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from timetable.models import Topic
from django.contrib.auth import get_user_model

User = get_user_model()
user = User.objects.first()

if not user:
    user = User.objects.create_user(email='test@example.com', password='password123')

# Clean up existing test data
Topic.objects.filter(user=user, name__in=['OS', 'DS', 'OOPS']).delete()

print("Creating Topics...")
t1 = Topic.objects.create(user=user, name='OS', estimated_minutes=120)
t2 = Topic.objects.create(user=user, name='DS', estimated_minutes=120)
t3 = Topic.objects.create(user=user, name='OOPS', estimated_minutes=120)

print(f"Topic 1: {t1.name}, unique_key: {t1.unique_key}")
print(f"Topic 2: {t2.name}, unique_key: {t2.unique_key}")
print(f"Topic 3: {t3.name}, unique_key: {t3.unique_key}")

assert t1.unique_key == f"{user.id}:os"
assert t2.unique_key == f"{user.id}:ds"
assert t3.unique_key == f"{user.id}:oops"

print("Trying duplicate creation (should fail/update)...")
try:
    Topic.objects.create(user=user, name='os', estimated_minutes=60)
    print("Error: Duplicate OS created without IntegrityError!")
except Exception as e:
    print(f"Success: Expected error caught: {type(e).__name__}")

print("Verification complete!")
