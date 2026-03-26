import datetime
from rest_framework import serializers
from .models import CompletionCheck, FreeSlot, TimetableEntry, Topic, UserNotification

IST_OFFSET = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

class TopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = ['id', 'name', 'estimated_minutes', 'priority', 'completed_minutes', 'target_date', 'is_overdue']

    def validate_name(self, value):
        user = self.context['request'].user
        instance = getattr(self, 'instance', None)
        if Topic.objects.filter(user=user, name=value).exclude(pk=instance.pk if instance else None).exists():
            raise serializers.ValidationError("Topic with this name already exists.")
        return value

class FreeSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = FreeSlot
        fields = ['id', 'start', 'end']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['start'] = instance.start.astimezone(IST_OFFSET)
        data['end'] = instance.end.astimezone(IST_OFFSET)
        return data

    def validate(self, data):
        user = self.context['request'].user
        start = data['start']
        end = data['end']
        if end <= start:
            raise serializers.ValidationError("End time must be after start time.")
        if FreeSlot.objects.filter(
            user=user,
            start__lt=end,
            end__gt=start
        ).exists():
            raise serializers.ValidationError("Free slot overlaps with an existing slot.")
        return data

class TimetableEntrySerializer(serializers.ModelSerializer):
    topic = TopicSerializer(read_only=True)
    
    class Meta:
        model = TimetableEntry
        fields = ['id', 'topic', 'start', 'end', 'notified', 'done']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['start'] = instance.start.astimezone(IST_OFFSET)
        data['end'] = instance.end.astimezone(IST_OFFSET)
        return data


class UserNotificationSerializer(serializers.ModelSerializer):
    entry_id = serializers.IntegerField(source="entry.id", read_only=True)

    class Meta:
        model = UserNotification
        fields = [
            "id",
            "entry_id",
            "notification_type",
            "title",
            "message",
            "payload",
            "scheduled_for",
            "sent_at",
            "action_due_at",
            "is_read",
            "is_actioned",
            "created_at",
        ]


class CompletionCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompletionCheck
        fields = [
            "entry",
            "asked_at",
            "response_received_at",
            "completed",
            "response_text",
            "quiz_question",
            "quiz_answer",
            "auto_rescheduled",
        ]
        read_only_fields = [
            "entry",
            "asked_at",
            "response_received_at",
            "auto_rescheduled",
        ]


class CompletionCheckResponseSerializer(serializers.Serializer):
    completed = serializers.BooleanField()
    response_text = serializers.CharField(required=False, allow_blank=True)
    quiz_answer = serializers.CharField(required=False, allow_blank=True)