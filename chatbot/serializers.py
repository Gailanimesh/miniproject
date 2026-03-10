from rest_framework import serializers
from .models import Conversation, Message


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Conversation
        fields = ["id", "started_at", "message_count"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "conversation", "sender", "text", "timestamp"]