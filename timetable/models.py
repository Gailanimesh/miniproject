from django.db import models
from django.conf import settings
from django.utils import timezone

class Topic(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='topics')
    name = models.CharField(max_length=255, db_index=True)
    estimated_minutes = models.PositiveIntegerField(default=60)
    priority = models.IntegerField(default=1)  # higher = more important
    completed_minutes = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'name']),
        ]

    def __str__(self):
        return self.name

class FreeSlot(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='free_slots')
    start = models.DateTimeField()
    end = models.DateTimeField()

    class Meta:
        ordering = ['start']
        indexes = [
            models.Index(fields=['user', 'start']),
            models.Index(fields=['user', 'end']),
        ]

class TimetableEntry(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='timetable_entries')
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='entries')
    start = models.DateTimeField()
    end = models.DateTimeField()
    notified = models.BooleanField(default=False)
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ['start']
        indexes = [
            models.Index(fields=['user', 'start']),
            models.Index(fields=['user', 'done', 'start']),
        ]

class Reminder(models.Model):
    entry = models.ForeignKey(TimetableEntry, on_delete=models.CASCADE, related_name='reminders')
    remind_at = models.DateTimeField()
    sent = models.BooleanField(default=False)


class ExamSubject(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='exam_subjects'
    )
    name = models.CharField(max_length=100)
    exam_date = models.DateField()
    difficulty = models.CharField(max_length=50, default='medium')

    class Meta:
        ordering = ['exam_date', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'name', 'exam_date'],
                name='uniq_exam_subject_per_day'
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.exam_date})"


class UserNotification(models.Model):
    TYPE_PRE_REMINDER = "pre_reminder"
    TYPE_COMPLETION_CHECK = "completion_check"
    TYPE_RESCHEDULE = "reschedule"

    TYPE_CHOICES = [
        (TYPE_PRE_REMINDER, "Pre Reminder"),
        (TYPE_COMPLETION_CHECK, "Completion Check"),
        (TYPE_RESCHEDULE, "Reschedule"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    entry = models.ForeignKey(
        TimetableEntry,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    notification_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    title = models.CharField(max_length=255)
    message = models.TextField()
    payload = models.JSONField(default=dict, blank=True)
    scheduled_for = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    action_due_at = models.DateTimeField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    is_actioned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "notification_type", "is_read"]),
            models.Index(fields=["scheduled_for", "sent_at"]),
        ]


class CompletionCheck(models.Model):
    entry = models.OneToOneField(
        TimetableEntry,
        on_delete=models.CASCADE,
        related_name="completion_check",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="completion_checks",
    )
    asked_at = models.DateTimeField(default=timezone.now)
    response_received_at = models.DateTimeField(null=True, blank=True)
    completed = models.BooleanField(null=True, blank=True)
    response_text = models.TextField(blank=True)
    quiz_question = models.TextField(blank=True)
    quiz_answer = models.TextField(blank=True)
    auto_rescheduled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-asked_at"]
