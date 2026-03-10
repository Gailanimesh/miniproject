# Generated manually for UserProfile conversational onboarding model

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("goal_type", models.CharField(blank=True, max_length=100)),
                ("exam_date", models.DateField(blank=True, null=True)),
                ("knowledge_level", models.CharField(blank=True, max_length=50)),
                ("daily_free_hours", models.PositiveIntegerField(default=0)),
                ("occupation", models.CharField(blank=True, max_length=100)),
                ("preferred_study_time", models.CharField(blank=True, max_length=50)),
                ("learning_style", models.CharField(blank=True, max_length=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
