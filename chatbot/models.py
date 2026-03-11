from django.db import models
from django.conf import settings

class Conversation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversations')
    started_at = models.DateTimeField(auto_now_add=True)

class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=10)  # 'user' or 'bot'
    text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

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
