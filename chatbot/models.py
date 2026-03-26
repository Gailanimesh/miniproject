import os

import requests
from django.db import models
from django.conf import settings

def generate_conversation_title(user_message: str) -> str:
    """
    Calls Groq to produce a short (4-6 word) title for a conversation
    based on the user's first message. Falls back to a truncated version
    of the message if Groq is unavailable.
    """
    if not user_message:
        return ""

    fallback = user_message.strip()[:60]

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return fallback

    system_prompt = (
        "You are a conversation title generator. "
        "Given the first message from a user, respond with ONLY a 4 to 6 word title "
        "that summarises the topic. No punctuation, no quotes, no explanation."
    )
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 20,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        title = response.json()["choices"][0]["message"]["content"].strip()
        return title[:255] if title else fallback
    except Exception:
        return fallback


class Conversation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversations')
    title = models.CharField(max_length=255, blank=True, default='')
    started_at = models.DateTimeField(auto_now_add=True)
    pending_ocr_data = models.JSONField(null=True, blank=True)
    parsed_subjects_for_setup = models.JSONField(null=True, blank=True)

    def __str__(self):
        if self.title:
            return self.title
        started = self.started_at.strftime('%Y-%m-%d') if self.started_at else ''
        return f"Conversation #{self.pk} ({started})"


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=10)  # 'user' or 'bot'
    text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(null=True, blank=True, default=dict)

    def __str__(self):
        preview = self.text[:50].replace('\n', ' ')
        return f"[{self.sender}] {preview}"

class Document(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField()
    embedding = models.BinaryField(null=True)  # For future embedding storage
    def __str__(self):
        return self.title


class UserModelSnapshot(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="completion_model_snapshot",
    )
    model_version = models.PositiveIntegerField(default=1)
    feature_names = models.JSONField(default=list, blank=True)
    weights = models.JSONField(default=list, blank=True)
    mean_vector = models.JSONField(default=list, blank=True)
    scale_vector = models.JSONField(default=list, blank=True)
    bias = models.FloatField(default=0.0)
    training_source = models.CharField(max_length=24, default="synthetic")
    historical_samples = models.PositiveIntegerField(default=0)
    synthetic_samples = models.PositiveIntegerField(default=0)
    total_samples = models.PositiveIntegerField(default=0)
    epochs = models.PositiveIntegerField(default=0)
    train_loss = models.FloatField(default=0.0)
    val_loss = models.FloatField(default=0.0)
    val_accuracy = models.FloatField(default=0.0)
    regularization = models.FloatField(default=0.02)
    trained_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-trained_at"]

    def __str__(self):
        return f"ModelSnapshot<{self.user_id}>"

class StudyNote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='study_notes')
    parent_topic = models.CharField(max_length=255, default='', blank=True)
    topic_title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Note: {self.parent_topic} (User: {self.user.id})"
