from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect

from .models import Application, Direction, Equipment, Executor, RoadSection, WorkPhoto
from .services import (
    cancel_application, complete_application, start_application, set_equipment_maintenance,
    replace_application_equipment,
)


class WorkPhotoInline(admin.TabularInline):
    model = WorkPhoto
    extra = 1


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "number", "customer_name", "section_name", "equipment_name",
        "urgency", "status", "created_at",
    )
    search_fields = ("=id", "customer_name", "phone")
    list_filter = ("status", "urgency", "equipment", "created_at")
    list_select_related = ("section", "equipment")
    inlines = (WorkPhotoInline,)
    readonly_fields = ("status", "started_at", "completed_at")
    actions = ("start_selected", "complete_selected", "cancel_selected")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status in (Application.Status.COMPLETED, Application.Status.CANCELLED):
            return self.readonly_fields + ("equipment",)
        return self.readonly_fields

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            # Django admin сам оборачивает POST в транзакцию, включая фотографии.
            return super().changeform_view(request, object_id, form_url, extra_context)
        except ValidationError as error:
            self.message_user(request, "; ".join(error.messages), messages.ERROR)
            return redirect(request.path)

    def has_delete_permission(self, request, obj=None):
        # Заявки сохраняются в истории; вместо удаления используется отмена.
        return False

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "equipment":
            field.label_from_instance = (
                lambda equipment: f"{equipment.name}, {equipment.bucket_volume} м³ — {equipment.get_status_display()}"
            )
        return field

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        # Открытая ранее карточка не должна откатывать статус после действия админа.
        if change:
            if "equipment" in form.changed_data:
                updated = replace_application_equipment(obj.pk, obj.equipment_id)
                obj.equipment_id = updated.equipment_id
            # Технику сохраняет только сервис; даты и статус нельзя перезаписать.
            obj.save(update_fields=[
                field for field in form.changed_data
                if field not in ("equipment", "status", "started_at", "completed_at", "created_at", "status_changed_at")
            ])
        else:
            super().save_model(request, obj, form, change)

    def _run_action(self, request, queryset, operation):
        succeeded = 0
        for application_id in queryset.order_by("pk").values_list("pk", flat=True):
            try:
                operation(application_id)
            except ValidationError as error:
                self.message_user(
                    request, f"Заявка №{application_id}: {'; '.join(error.messages)}",
                    level=messages.ERROR,
                )
            else:
                succeeded += 1
        if succeeded:
            self.message_user(request, f"Обработано заявок: {succeeded}.", messages.SUCCESS)

    @admin.action(description="Начать работу")
    def start_selected(self, request, queryset):
        self._run_action(request, queryset, start_application)

    @admin.action(description="Завершить работу")
    def complete_selected(self, request, queryset):
        self._run_action(request, queryset, complete_application)

    @admin.action(description="Отменить заявки")
    def cancel_selected(self, request, queryset):
        self._run_action(request, queryset, cancel_application)

    @admin.display(description="Номер", ordering="id")
    def number(self, obj):
        return obj.pk

    @admin.display(description="Участок", ordering="section__start_point")
    def section_name(self, obj):
        return f"{obj.section.start_point} — {obj.section.end_point}"

    @admin.display(description="Погрузчик", ordering="equipment__name")
    def equipment_name(self, obj):
        return obj.equipment.name


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "bucket_volume", "status")
    readonly_fields = ("status",)
    actions = ("enable_maintenance", "disable_maintenance")

    def save_model(self, request, obj, form, change):
        # Редактирование названия не перезаписывает статус из открытой ранее формы.
        if change:
            obj.save(update_fields=["name", "bucket_volume"])
        else:
            super().save_model(request, obj, form, change)

    def _set_maintenance(self, request, queryset, enabled):
        for equipment_id in queryset.order_by("pk").values_list("pk", flat=True):
            try:
                set_equipment_maintenance(equipment_id, enabled)
            except ValidationError as error:
                self.message_user(request, f"Техника №{equipment_id}: {'; '.join(error.messages)}", messages.ERROR)
            else:
                self.message_user(request, f"Статус техники №{equipment_id} обновлён.", messages.SUCCESS)

    @admin.action(description="Включить техническое обслуживание")
    def enable_maintenance(self, request, queryset):
        self._set_maintenance(request, queryset, True)

    @admin.action(description="Снять техническое обслуживание")
    def disable_maintenance(self, request, queryset):
        self._set_maintenance(request, queryset, False)


@admin.register(Direction)
class DirectionAdmin(admin.ModelAdmin):
    list_display = ("id", "name")


@admin.register(RoadSection)
class RoadSectionAdmin(admin.ModelAdmin):
    list_display = ("id", "direction", "start_point", "end_point")


@admin.register(Executor)
class ExecutorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "telegram_id")


@admin.register(WorkPhoto)
class WorkPhotoAdmin(admin.ModelAdmin):
    list_display = ("id", "application", "image", "uploaded_at")
