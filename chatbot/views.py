import os
from dotenv import load_dotenv
import requests
import numpy as np
from rest_framework.views import APIView
from rest_framework.response import Response
from sentence_transformers import SentenceTransformer
from .models import Document

load_dotenv()  # Loads variables from .env

def call_groq_api(prompt, context):
    api_key = os.getenv("GROQ_API_KEY")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    data = {
        "model": "openai/gpt-oss-120b",
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Answer the user's question using only the following context. "
                    f"Be brief and clear. If the context does not contain the answer, reply 'Sorry, I don't know.' "
                    f"Context: {context}"
                )
            },
            {"role": "user", "content": prompt}
        ]
    }
    response = requests.post(url, headers=headers, json=data)
    resp_json = response.json()
    if "choices" not in resp_json:
        return resp_json.get("error", "Groq API error: No choices in response.")
    return resp_json["choices"][0]["message"]["content"]

class ChatbotConversationView(APIView):
    def post(self, request):
        user_message = request.data.get("message")
        if not user_message:
            return Response({"error": "No message provided."}, status=400)

        # Load embedding model (consider loading once in production)
        model = SentenceTransformer('all-MiniLM-L6-v2')
        query_embedding = model.encode(user_message).astype(np.float32)

        # Retrieve top document by cosine similarity
        docs = Document.objects.exclude(embedding=None)
        best_doc = None
        best_score = -1
        for doc in docs:
            doc_embedding = np.frombuffer(doc.embedding, dtype=np.float32)
            score = np.dot(query_embedding, doc_embedding) / (np.linalg.norm(query_embedding) * np.linalg.norm(doc_embedding))
            if score > best_score:
                best_score = score
                best_doc = doc
        context = best_doc.content if best_doc else ""

        # Call Groq API
        bot_response = call_groq_api(user_message, context)
        return Response({"response": bot_response})