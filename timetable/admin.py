from django.contrib import admin
from .models import ExamSubject, FreeSlot, Reminder, TimetableEntry, Topic


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
	list_display = ('name', 'user', 'priority', 'estimated_minutes', 'completed_minutes')
	search_fields = ('name', 'user__email')


@admin.register(FreeSlot)
class FreeSlotAdmin(admin.ModelAdmin):
	list_display = ('user', 'start', 'end')
	search_fields = ('user__email',)


@admin.register(TimetableEntry)
class TimetableEntryAdmin(admin.ModelAdmin):
	list_display = ('user', 'topic', 'start', 'end', 'done', 'notified')
	search_fields = ('user__email', 'topic__name')


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
	list_display = ('entry', 'remind_at', 'sent')


@admin.register(ExamSubject)
class ExamSubjectAdmin(admin.ModelAdmin):
	list_display = ('name', 'user', 'exam_date', 'difficulty')
	search_fields = ('name', 'user__email')
