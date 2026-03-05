from django.core.management.base import BaseCommand
from chatbot.models import Document
from sentence_transformers import SentenceTransformer
import numpy as np

class Command(BaseCommand):
    help = 'Generate embeddings for all documents'

    def handle(self, *args, **kwargs):
        model = SentenceTransformer('all-MiniLM-L6-v2')
        docs = Document.objects.all()
        for doc in docs:
            embedding = model.encode(doc.content).astype(np.float32).tobytes()
            doc.embedding = embedding
            doc.save()
            self.stdout.write(self.style.SUCCESS(f'Updated embedding for: {doc.title}'))