from django.urls import path
from .views import ChatbotConversationView

urlpatterns = [
    path('api/chatbot/converse/', ChatbotConversationView.as_view(), name='chatbot-converse'),
]