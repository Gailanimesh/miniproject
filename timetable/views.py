from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status
from .serializers import TopicSerializer, FreeSlotSerializer, TimetableEntrySerializer
from .models import Topic, FreeSlot, TimetableEntry
from .services import schedule_timetable_for_user
from rest_framework import generics, permissions

class IsOwnerOrReadOnly(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.user == request.user

class ChatbotSaveView(APIView):
    """
    Accepts JSON:
    {
      "topics": [{"name":"Math","estimated_minutes":120,"priority":2}, ...],
      "free_slots": [{"start":"2026-03-02T14:00:00Z","end":"2026-03-02T16:00:00Z"}, ...]
    }
    Saves topics and free slots for request.user and runs scheduler.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        topics = request.data.get('topics', [])
        free_slots = request.data.get('free_slots', [])

        # upsert topics
        for t in topics:
            serializer = TopicSerializer(data=t, context={'request': request})
            serializer.is_valid(raise_exception=True)
            serializer.save(user=user)

        # add free slots incrementally (no delete)
        for fs in free_slots:
            fs_serializer = FreeSlotSerializer(data=fs, context={'request': request})
            fs_serializer.is_valid(raise_exception=True)
            fs_serializer.save(user=user)

        entries = schedule_timetable_for_user(user)
        out = TimetableEntrySerializer(entries, many=True)
        return Response(out.data, status=status.HTTP_200_OK)

class TimetableListView(generics.ListAPIView):
    serializer_class = TimetableEntrySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return TimetableEntry.objects.filter(user=self.request.user).order_by('start')

class TimetableEntryDetailView(generics.RetrieveUpdateAPIView):
    queryset = TimetableEntry.objects.all()
    serializer_class = TimetableEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]

    def get_queryset(self):
        return TimetableEntry.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        prev = self.get_object()
        prev_done = prev.done
        instance = serializer.save()
        if not prev_done and instance.done:
            minutes = int((instance.end - instance.start).total_seconds() // 60)
            topic = instance.topic
            topic.completed_minutes = (topic.completed_minutes or 0) + minutes
            topic.save()
            schedule_timetable_for_user(self.request.user)
