from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status
from .models import Conversation, Message
from .serializers import MessageSerializer

class ChatbotConversationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        text = request.data.get('text')
        # Find or create a conversation
        conversation, _ = Conversation.objects.get_or_create(user=user)
        # Save user message
        user_msg = Message.objects.create(conversation=conversation, sender='user', text=text)
        # Simple scripted bot response (replace with RAG/ML later)
        if 'hi' in text.lower():
            bot_text = "Hi! Let's build a timetable for you. What are you trying to accomplish?"
        else:
            bot_text = "Tell me more about your goals or your free time."
        bot_msg = Message.objects.create(conversation=conversation, sender='bot', text=bot_text)
        # Return both messages
        return Response({
            'user_message': MessageSerializer(user_msg).data,
            'bot_message': MessageSerializer(bot_msg).data
        }, status=status.HTTP_200_OK)
