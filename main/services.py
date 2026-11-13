from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Application, Equipment


def _lock_application(application_id):
    # Блокировки удерживаются до конца транзакции, включая обновление погрузчика.
    application = Application.objects.select_for_update().get(pk=application_id)
    equipment = Equipment.objects.select_for_update().get(pk=application.equipment_id)
    return application, equipment


@transaction.atomic
def start_application(application_id):
    application, equipment = _lock_application(application_id)
    if application.status != Application.Status.WAITING:
        raise ValidationError("Начать можно только ожидающую заявку.")
    if equipment.status == Equipment.Status.MAINTENANCE:
        raise ValidationError("Погрузчик на техническом обслуживании.")
    if equipment.status == Equipment.Status.BUSY or Application.objects.filter(
        equipment=equipment, status=Application.Status.IN_PROGRESS
    ).exists():
        raise ValidationError("Погрузчик уже занят.")

    application.status = Application.Status.IN_PROGRESS
    application.started_at = timezone.now()
    application.status_changed_at = application.started_at
    application.save(update_fields=["status", "started_at", "status_changed_at"])
    equipment.status = Equipment.Status.BUSY
    equipment.save(update_fields=["status"])
    return application


@transaction.atomic
def complete_application(application_id):
    application, equipment = _lock_application(application_id)
    if application.status != Application.Status.IN_PROGRESS:
        raise ValidationError("Завершить можно только работающую заявку.")

    application.status = Application.Status.COMPLETED
    application.completed_at = timezone.now()
    application.status_changed_at = application.completed_at
    application.save(update_fields=["status", "completed_at", "status_changed_at"])
    equipment.status = Equipment.Status.AVAILABLE
    equipment.save(update_fields=["status"])
    return application


@transaction.atomic
def cancel_application(application_id):
    application, equipment = _lock_application(application_id)
    if application.status not in (
        Application.Status.WAITING, Application.Status.IN_PROGRESS
    ):
        raise ValidationError("Отменить можно только ожидающую или работающую заявку.")

    was_in_progress = application.status == Application.Status.IN_PROGRESS
    application.status = Application.Status.CANCELLED
    application.status_changed_at = timezone.now()
    application.save(update_fields=["status", "status_changed_at"])
    # Отмена ожидающей заявки не влияет на работу другой заявки.
    if was_in_progress:
        equipment.status = Equipment.Status.AVAILABLE
        equipment.save(update_fields=["status"])
    return application


@transaction.atomic
def set_equipment_maintenance(equipment_id, enabled):
    # Та же блокировка, что при начале работы: операции не могут пересечься.
    equipment = Equipment.objects.select_for_update().get(pk=equipment_id)
    if Application.objects.filter(
        equipment=equipment, status=Application.Status.IN_PROGRESS
    ).exists():
        raise ValidationError("Нельзя менять обслуживание во время текущей работы.")
    equipment.status = Equipment.Status.MAINTENANCE if enabled else Equipment.Status.AVAILABLE
    equipment.save(update_fields=["status"])
    return equipment


@transaction.atomic
def replace_application_equipment(application_id, equipment_id):
    application = Application.objects.select_for_update().get(pk=application_id)
    # Оба погрузчика блокируем по ID, независимо от направления замены.
    equipment = {
        item.pk: item for item in Equipment.objects.select_for_update()
        .filter(pk__in=[application.equipment_id, equipment_id]).order_by("pk")
    }
    if equipment_id not in equipment:
        raise ValidationError("Выбранный погрузчик больше не существует.")
    if application.equipment_id == equipment_id:
        return application
    if application.status not in (Application.Status.WAITING, Application.Status.IN_PROGRESS):
        raise ValidationError("У выполненной или отменённой заявки нельзя заменить погрузчик.")

    previous = equipment[application.equipment_id]
    replacement = equipment[equipment_id]
    if replacement.status == Equipment.Status.MAINTENANCE:
        raise ValidationError("Выбранный погрузчик на техническом обслуживании.")
    if application.status == Application.Status.IN_PROGRESS:
        if replacement.status != Equipment.Status.AVAILABLE or Application.objects.filter(
            equipment=replacement, status=Application.Status.IN_PROGRESS
        ).exists():
            raise ValidationError("Для работающей заявки нужен свободный погрузчик без текущей работы.")

    application.equipment = replacement
    application.save(update_fields=["equipment"])
    if application.status == Application.Status.IN_PROGRESS:
        previous.status = Equipment.Status.AVAILABLE
        previous.save(update_fields=["status"])
        replacement.status = Equipment.Status.BUSY
        replacement.save(update_fields=["status"])
    return application
