import os
import requests
from dotenv import load_dotenv

load_dotenv(r"c:\mini project\.env")
api_key = os.getenv("GROQ_API_KEY")

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

payload = {
    "model": "llama3-8b-8192",
    "messages": [
        {"role": "system", "content": "You are a study assistant."},
        {"role": "user", "content": "user: make some notes"}
    ],
    "temperature": 0.3,
    "max_tokens": 512,
}

resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
print(resp.status_code)
print(resp.text)
