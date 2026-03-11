from django.urls import path

from .views import (
    ChatbotSaveView,
    CompletionCheckResponseView,
    NotificationListView,
    NotificationReadView,
    TimetableEntryDetailView,
    TimetableListView,
)

urlpatterns = [
    path('api/timetable/chatbot/', ChatbotSaveView.as_view(), name='timetable-chatbot'),
    path('api/timetable/entries/', TimetableListView.as_view(), name='timetable-entries'),
    path('api/timetable/entries/<int:pk>/', TimetableEntryDetailView.as_view(), name='timetable-entry-detail'),
    path(
        'api/timetable/entries/<int:pk>/completion-response/',
        CompletionCheckResponseView.as_view(),
        name='timetable-entry-completion-response',
    ),
    path('api/timetable/notifications/', NotificationListView.as_view(), name='timetable-notifications'),
    path(
        'api/timetable/notifications/<int:pk>/read/',
        NotificationReadView.as_view(),
        name='timetable-notification-read',
    ),
]