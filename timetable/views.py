from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from chatbot.services.timetable_generator import generate_timetable_for_user

from .models import FreeSlot, TimetableEntry, Topic, UserNotification
from .notification_service import apply_completion_response
from .serializers import (
    CompletionCheckResponseSerializer,
    FreeSlotSerializer,
    TimetableEntrySerializer,
    TopicSerializer,
    UserNotificationSerializer,
)

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

        entries = generate_timetable_for_user(user, use_model_priority=True)
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
            generate_timetable_for_user(self.request.user, use_model_priority=True)


def _entry_summary(entry):
    return {
        "id": entry.id,
        "topic": entry.topic.name,
        "topic_id": entry.topic_id,
        "start": entry.start,
        "end": entry.end,
        "done": entry.done,
    }


class CompletionCheckResponseView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        entry = get_object_or_404(TimetableEntry, id=pk, user=request.user)
        serializer = CompletionCheckResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = apply_completion_response(
            user=request.user,
            entry=entry,
            completed=serializer.validated_data["completed"],
            response_text=serializer.validated_data.get("response_text", ""),
            quiz_answer=serializer.validated_data.get("quiz_answer", ""),
        )

        check = result.get("completion_check")
        check_payload = {
            "entry_id": check.entry_id if check else entry.id,
            "asked_at": getattr(check, "asked_at", None),
            "response_received_at": getattr(check, "response_received_at", None),
            "completed": getattr(check, "completed", None),
            "quiz_question": getattr(check, "quiz_question", ""),
        }

        if result.get("status") == "completed":
            return Response(
                {
                    "status": "completed",
                    "entry_done": True,
                    "completion_check": check_payload,
                    "generation": result.get("generation", {}),
                    "entries": [_entry_summary(item) for item in result.get("entries", [])],
                },
                status=status.HTTP_200_OK,
            )

        reschedule = result.get("reschedule", {})
        strategy = reschedule.get("strategy")
        strategy_payload = (
            strategy.to_dict()
            if hasattr(strategy, "to_dict")
            else {
                "action": getattr(strategy, "action", "reschedule"),
                "max_chunk_minutes": getattr(strategy, "max_chunk_minutes", 60),
                "priority_boost": getattr(strategy, "priority_boost", 1),
                "extra_minutes_ratio": getattr(strategy, "extra_minutes_ratio", 0.0),
            }
        )
        return Response(
            {
                "status": "rescheduled",
                "entry_done": False,
                "completion_check": check_payload,
                "strategy": strategy_payload,
                "generation": reschedule.get("generation_meta", {}),
                "entries": [_entry_summary(item) for item in reschedule.get("entries", [])],
            },
            status=status.HTTP_200_OK,
        )


class NotificationListView(generics.ListAPIView):
    serializer_class = UserNotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = UserNotification.objects.filter(user=self.request.user).select_related("entry")

        unread = self.request.query_params.get("unread")
        if unread and unread.lower() in {"1", "true", "yes"}:
            queryset = queryset.filter(is_read=False)

        action_required = self.request.query_params.get("action_required")
        if action_required and action_required.lower() in {"1", "true", "yes"}:
            queryset = queryset.filter(is_actioned=False)

        return queryset.order_by("-created_at")


class NotificationReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        notification = get_object_or_404(UserNotification, id=pk, user=request.user)
        notification.is_read = True
        if request.data.get("is_actioned") is True:
            notification.is_actioned = True
        notification.save(update_fields=["is_read", "is_actioned", "updated_at"])

        serialized = UserNotificationSerializer(notification)
        return Response(serialized.data, status=status.HTTP_200_OK)
