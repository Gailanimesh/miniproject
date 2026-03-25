from django.urls import path
from .views import ChatbotConversationView, ConversationListView, ConversationMessagesView, StudyNoteListView, StudyNoteDetailView

urlpatterns = [
    path('api/chatbot/converse/', ChatbotConversationView.as_view(), name='chatbot-converse'),
    path('api/chatbot/conversations/', ConversationListView.as_view(), name='chatbot-conversations'),
    path(
        'api/chatbot/conversations/<int:conversation_id>/messages/',
        ConversationMessagesView.as_view(),
        name='chatbot-conversation-messages',
    ),
    path('api/chatbot/notes/', StudyNoteListView.as_view(), name='chatbot-notes-list'),
    path('api/chatbot/notes/<int:pk>/', StudyNoteDetailView.as_view(), name='chatbot-notes-detail'),
]