from django.db import models
from django.utils import timezone


class Direction(models.Model):
    name = models.CharField(max_length=255, unique=True)


class RoadSection(models.Model):
    direction = models.ForeignKey(Direction, on_delete=models.PROTECT)
    start_point = models.CharField(max_length=255)
    end_point = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["direction", "start_point", "end_point"],
                name="unique_direction_section",
            ),
            models.CheckConstraint(
                condition=~models.Q(start_point=models.F("end_point")),
                name="section_distinct_endpoints",
            ),
        ]


class Equipment(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Свободен"
        BUSY = "busy", "Занят"
        MAINTENANCE = "maintenance", "Техническое обслуживание"

    name = models.CharField(max_length=255)
    bucket_volume = models.DecimalField(max_digits=5, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.AVAILABLE)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(bucket_volume__gt=0),
                name="equipment_positive_bucket_volume",
            ),
        ]


class Executor(models.Model):
    name = models.CharField(max_length=255)
    telegram_id = models.BigIntegerField(null=True, blank=True, unique=True)


class Application(models.Model):
    class Urgency(models.TextChoices):
        URGENT = "urgent", "Срочно"
        SOON = "soon", "В ближайшее время"
        NORMAL = "normal", "В порядке очередности"

    class Status(models.TextChoices):
        WAITING = "waiting", "В ожидании"
        IN_PROGRESS = "in_progress", "В работе"
        COMPLETED = "completed", "Выполнено"
        CANCELLED = "cancelled", "Отменено"

    section = models.ForeignKey(RoadSection, on_delete=models.PROTECT)
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT)
    executor = models.ForeignKey(
        Executor, on_delete=models.SET_NULL, null=True, blank=True
    )
    customer_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=32)
    urgency = models.CharField(max_length=16, choices=Urgency.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.WAITING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    status_changed_at = models.DateTimeField(default=timezone.now, editable=False)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["equipment"],
                condition=models.Q(status="in_progress"),
                name="one_in_progress_application_per_equipment",
            ),
        ]


class WorkPhoto(models.Model):
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="photos"
    )
    image = models.ImageField(upload_to="work_photos/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
