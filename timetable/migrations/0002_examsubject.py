# Generated manually for exam timetable parsing support

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("timetable", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExamSubject",
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
                ("name", models.CharField(max_length=100)),
                ("exam_date", models.DateField()),
                ("difficulty", models.CharField(default="medium", max_length=50)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="exam_subjects",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["exam_date", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="examsubject",
            constraint=models.UniqueConstraint(
                fields=("user", "name", "exam_date"),
                name="uniq_exam_subject_per_day",
            ),
        ),
    ]
