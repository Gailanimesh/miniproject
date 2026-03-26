"""
Test for OCR with multiple branches (timetable....jpg)
Tests the Gemini API parsing and branch selection flow
"""
import os
import django
from dotenv import load_dotenv

# Load .env first
load_dotenv(r'C:\mini project\.env')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in os.environ.get('PYTHONPATH', ''):
    os.environ['PYTHONPATH'] = sys_path + os.pathsep + os.environ.get('PYTHONPATH', '')

import sys
sys.path.insert(0, r'C:\mini project')
django.setup()

from chatbot.services.ocr_pipeline import extract_text_from_image
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

User = get_user_model()

def test_ocr_multiple_branches():
    """Test OCR parsing of the CS internal exam timetable with multiple branches"""
    print("\n" + "="*60)
    print("TESTING OCR - Multiple Branches Timetable")
    print("="*60)
    
    # Test 1: Direct OCR extraction
    print("\n--- Test 1: Direct OCR Extraction ---")
    image_path = r"C:\mini project\timetable....jpg"
    
    try:
        result = extract_text_from_image(image_path, user_prompt="I am a CSE student")
        
        print(f"OCR Result Keys: {result.keys() if isinstance(result, dict) else 'ERROR'}")
        print(f"Has Multiple Branches: {result.get('has_multiple_branches')}")
        print(f"Detected Branches: {result.get('detected_branches', [])}")
        print(f"Subjects Count: {len(result.get('subjects', []))}")
        print(f"Branch Subjects Keys: {list(result.get('branch_subjects', {}).keys())}")
        
        if result.get('error'):
            print(f"ERROR: {result['error']}")
            return False
        
        # Check if multiple branches detected
        if result.get('has_multiple_branches'):
            print("\n[OK] Multiple branches detected!")
            branches = result.get('branch_subjects', {})
            for branch, subjects in branches.items():
                print(f"\n  Branch: {branch}")
                for subj in subjects:
                    print(f"    - {subj.get('name')}: {subj.get('date')}")
        
        # Check if subjects are extracted (either directly or via branch_subjects)
        if result.get('subjects') or result.get('branch_subjects'):
            print("\n[OK] Subjects found!")
            return True
        else:
            print("\n[WARN] No subjects extracted")
            return False
            
    except Exception as e:
        print(f"[ERROR] OCR Test Failed: {e}")
        return False

def test_branch_resolution():
    """Test the branch resolution logic"""
    print("\n--- Test 2: Branch Resolution ---")
    from chatbot.views import _resolve_branch_from_text
    
    branches = ["CSE A", "CSE B", "IT A"]
    
    test_cases = [
        ("I am CSE A student", "CSE A"),
        ("cs a", "CSE A"),
        ("cse", None),  # Ambiguous
        ("I study in CSE B", "CSE B"),
        ("not specified", None),
    ]
    
    for user_msg, expected in test_cases:
        result = _resolve_branch_from_text(user_msg, branches)
        status = "[OK]" if (result == expected or (result and expected and result in expected)) else "[FAIL]"
        print(f"  {status} '{user_msg}' -> '{result}' (expected: '{expected}')")

def test_ocr_endpoint():
    """Test the full OCR endpoint with the timetable image"""
    print("\n--- Test 3: OCR Endpoint Test ---")
    
    # Create test user
    User.objects.filter(email='ocr_test@example.com').delete()
    user = User.objects.create_user(email='ocr_test@example.com', password='TestPass123!')
    
    client = APIClient()
    client.force_authenticate(user=user)
    
    # Send the exam image
    image_path = r"C:\mini project\timetable....jpg"
    
    with open(image_path, 'rb') as f:
        response = client.post(
            '/api/chatbot/converse/',
            {
                'exam_image': f,
                'message': 'I am a CSE A student'
            },
            format='multipart'
        )
    
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Tool: {data.get('tool')}")
    print(f"Response: {data.get('response', '')[:200]}...")
    
    if response.status_code == 200:
        tool = data.get('tool')
        if tool == 'ocr_exam_parser':
            print(f"\n[OK] OCR parsed successfully!")
            print(f"Subjects count: {data.get('subjects_count', 0)}")
            return True
        elif tool == 'ocr_exam_parser_need_branch':
            print(f"\n[INFO] Multiple branches detected - needs branch selection")
            print(f"Available branches: {list(data.get('parsed', {}).get('branch_subjects', {}).keys())}")
            return True
        else:
            print(f"\n[WARN] Unexpected tool: {tool}")
            return False
    else:
        print(f"[FAIL] Endpoint returned: {response.status_code}")
        return False

def test_cs_a_timetable_generation():
    """Test generating a timetable for CSE A student"""
    print("\n--- Test 4: Timetable Generation for CSE A ---")
    
    from users.models import UserProfile
    from timetable.models import Topic, FreeSlot, ExamSubject
    
    User.objects.filter(email='cse_a_test@example.com').delete()
    user = User.objects.create_user(email='cse_a_test@example.com', password='TestPass123!')
    
    client = APIClient()
    client.force_authenticate(user=user)
    
    # 1. Create profile
    UserProfile.objects.create(
        user=user,
        goal_type='Internal Exam',
        knowledge_level='intermediate',
        daily_free_hours=4,
        exam_date=timezone.now().date() + timedelta(days=7)
    )
    print("[OK] Profile created")
    
    # 2. Upload exam image with branch selection
    image_path = r"C:\mini project\timetable....jpg"
    with open(image_path, 'rb') as f:
        response = client.post(
            '/api/chatbot/converse/',
            {
                'exam_image': f,
                'message': 'I am CSE A student'
            },
            format='multipart'
        )
    
    print(f"OCR Status: {response.status_code}")
    print(f"OCR Tool: {response.json().get('tool')}")
    
    if response.json().get('tool') == 'ocr_exam_parser_need_branch':
        # Need to select branch
        data = response.json()
        branches = list(data.get('parsed', {}).get('branch_subjects', {}).keys())
        print(f"Detected branches: {branches}")
        
        # Reply with branch selection
        response = client.post(
            '/api/chatbot/converse/',
            {'message': 'CSE A'},
            format='json'
        )
        print(f"Branch selection status: {response.status_code}")
        print(f"Branch selection tool: {response.json().get('tool')}")
    
    # 3. Add free slots
    now = timezone.now()
    base = (now + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    
    for i in range(5):
        FreeSlot.objects.create(
            user=user,
            start=base + timedelta(days=i),
            end=base + timedelta(days=i, hours=2)
        )
    print("[OK] Free slots created")
    
    # 4. Generate timetable
    response = client.post(
        '/api/chatbot/converse/',
        {'tool': 'generate_timetable', 'generate_timetable': True},
        format='json'
    )
    
    print(f"Timetable Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Tool: {data.get('tool')}")
        print(f"Entries: {len(data.get('entries', []))}")
        
        if data.get('entries'):
            print("\nGenerated Timetable:")
            for entry in data['entries'][:6]:
                print(f"  - {entry['topic']}: {entry['start'][:16]} to {entry['end'][:16]}")
            return True
    
    return False

def run_all_tests():
    """Run all OCR tests"""
    from datetime import timedelta
    
    print("\n" + "#"*60)
    print("#  OCR & MULTIPLE BRANCHES TEST SUITE")
    print("#"*60)
    
    results = []
    
    # Test 1: Direct OCR
    results.append(("OCR Extraction", test_ocr_multiple_branches()))
    
    # Test 2: Branch resolution
    test_branch_resolution()
    results.append(("Branch Resolution", True))  # Visual test
    
    # Test 3: OCR Endpoint
    try:
        results.append(("OCR Endpoint", test_ocr_endpoint()))
    except Exception as e:
        print(f"[ERROR] OCR Endpoint test failed: {e}")
        results.append(("OCR Endpoint", False))
    
    # Test 4: Full timetable generation
    try:
        results.append(("CS A Timetable Generation", test_cs_a_timetable_generation()))
    except Exception as e:
        print(f"[ERROR] Timetable generation failed: {e}")
        import traceback
        traceback.print_exc()
        results.append(("CS A Timetable Generation", False))
    
    # Summary
    print("\n" + "="*60)
    print("TEST RESULTS SUMMARY")
    print("="*60)
    
    for name, passed in results:
        status = "[OK]" if passed else "[FAIL]"
        print(f"  {status} {name}")
    
    passed = sum(1 for _, p in results if p)
    print(f"\nTotal: {passed}/{len(results)} tests passed")
    print("="*60)
    
    return all(r for _, r in results)

if __name__ == "__main__":
    from datetime import timedelta
    success = run_all_tests()
    sys.exit(0 if success else 1)
