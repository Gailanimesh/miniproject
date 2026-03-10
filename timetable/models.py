from django.db import models
from django.conf import settings

class Topic(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='topics')
    name = models.CharField(max_length=255)
    estimated_minutes = models.PositiveIntegerField(default=60)
    priority = models.IntegerField(default=1)  # higher = more important
    completed_minutes = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name

class FreeSlot(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='free_slots')
    start = models.DateTimeField()
    end = models.DateTimeField()

    class Meta:
        ordering = ['start']

class TimetableEntry(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='timetable_entries')
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='entries')
    start = models.DateTimeField()
    end = models.DateTimeField()
    notified = models.BooleanField(default=False)
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ['start']

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
