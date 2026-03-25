from rest_framework import serializers
from .models import Conversation, Message, StudyNote


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Conversation
        fields = ["id", "title", "started_at", "message_count"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "conversation", "sender", "text", "timestamp"]

class StudyNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyNote
        fields = ["id", "topic_title", "content", "created_at"]