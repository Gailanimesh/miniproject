from django.urls import path
from .views import ChatbotSaveView, TimetableListView, TimetableEntryDetailView

urlpatterns = [
    path('api/timetable/chatbot/', ChatbotSaveView.as_view(), name='timetable-chatbot'),
    path('api/timetable/entries/', TimetableListView.as_view(), name='timetable-entries'),
    path('api/timetable/entries/<int:pk>/', TimetableEntryDetailView.as_view(), name='timetable-entry-detail'),
]