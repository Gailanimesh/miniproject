from rest_framework import serializers
from .models import Topic, FreeSlot, TimetableEntry

class TopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = ['id', 'name', 'estimated_minutes', 'priority', 'completed_minutes']

    def validate_name(self, value):
        user = self.context['request'].user
        if Topic.objects.filter(user=user, name=value).exists():
            raise serializers.ValidationError("Topic with this name already exists.")
        return value

class FreeSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = FreeSlot
        fields = ['id', 'start', 'end']

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