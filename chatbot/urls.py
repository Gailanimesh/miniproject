from django.urls import path
from .views import ChatbotConversationView, ConversationListView, ConversationMessagesView

urlpatterns = [
    path('api/chatbot/converse/', ChatbotConversationView.as_view(), name='chatbot-converse'),
    path('api/chatbot/conversations/', ConversationListView.as_view(), name='chatbot-conversations'),
    path(
        'api/chatbot/conversations/<int:conversation_id>/messages/',
        ConversationMessagesView.as_view(),
        name='chatbot-conversation-messages',
    ),
]