import re
from datetime import datetime
from dateutil import parser as date_parser

def test_parse(subject_name, date_str):
    date_str = date_str.strip()
    try:
        # The logic we just added to views.py
        if len(date_str) == 8 and date_str.isdigit():
            date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        elif len(date_str) == 9 and '-' in date_str[4:]:
            if date_str[4] != '-':
                date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[7:]}"
            else:
                date_str = f"{date_str[:7]}-{date_str[7:]}"
        
        parsed_date = date_parser.parse(date_str, fuzzy=True).date()
        return parsed_date
    except Exception as e:
        return f"Error: {e}"

# Test cases
cases = [
    ("os", "202604-02"), # The user's case
    ("ds", "2026-0405"), # Another potential typo
    ("math", "20260315"), # No hyphens
    ("physics", "2026-04-20"), # Standard
]

for s, d in cases:
    result = test_parse(s, d)
    print(f"Input: {s} - {d} => Result: {result}")
