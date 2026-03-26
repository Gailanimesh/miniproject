import os, django, subprocess
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

result = subprocess.run(
    ["python", "manage.py", "test", "chatbot", "--verbosity=2"],
    cwd=r"c:\mini project", capture_output=True, text=True
)
with open(r"c:\mini project\test_out.txt", "w", encoding="utf-8") as f:
    f.write(result.stdout)
    f.write(result.stderr)
