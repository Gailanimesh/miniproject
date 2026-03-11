from django.core.management.base import BaseCommand, CommandError
from chatbot.models import Document
import numpy as np

class Command(BaseCommand):
    help = 'Generate embeddings for all documents'

    def handle(self, *args, **kwargs):
        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as exc:
            raise CommandError(
                "sentence-transformers is not installed. "
                "Install ML extras with: pip install -r requirements-ml.txt"
            ) from exc

        model = SentenceTransformer('all-MiniLM-L6-v2')
        docs = Document.objects.all()
        for doc in docs:
            embedding = model.encode(doc.content).astype(np.float32).tobytes()
            doc.embedding = embedding
            doc.save()
            self.stdout.write(self.style.SUCCESS(f'Updated embedding for: {doc.title}'))