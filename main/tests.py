from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Application, Direction, Equipment, RoadSection, WorkPhoto
from .services import cancel_application, complete_application, start_application, set_equipment_maintenance, replace_application_equipment


class ApplicationServiceTests(TestCase):
    def setUp(self):
        direction = Direction.objects.create(name="Направление")
        section = RoadSection.objects.create(
            direction=direction, start_point="А", end_point="Б"
        )
        self.equipment = Equipment.objects.create(name="Погрузчик", bucket_volume="2.70")
        fields = dict(
            section=section, equipment=self.equipment, customer_name="Заказчик",
            phone="123", urgency=Application.Urgency.NORMAL,
        )
        self.first = Application.objects.create(**fields)
        self.second = Application.objects.create(**fields)

    def test_start_and_complete_do_not_start_next_application(self):
        started = start_application(self.first.pk)
        self.equipment.refresh_from_db()
        self.assertEqual(started.status, Application.Status.IN_PROGRESS)
        self.assertIsNotNone(started.started_at)
        self.assertEqual(self.equipment.status, Equipment.Status.BUSY)

        completed = complete_application(self.first.pk)
        self.equipment.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(completed.status, Application.Status.COMPLETED)
        self.assertGreaterEqual(completed.completed_at, completed.started_at)
        self.assertEqual(self.equipment.status, Equipment.Status.AVAILABLE)
        self.assertEqual(self.second.status, Application.Status.WAITING)
        self.assertIsNone(self.second.started_at)

    def test_second_work_is_rejected_even_if_status_is_stale(self):
        start_application(self.first.pk)
        with self.assertRaises(ValidationError):
            start_application(self.second.pk)
        Equipment.objects.filter(pk=self.equipment.pk).update(status=Equipment.Status.AVAILABLE)
        with self.assertRaises(ValidationError):
            start_application(self.second.pk)
        self.second.refresh_from_db()
        self.assertEqual(self.second.status, Application.Status.WAITING)
        self.assertIsNone(self.second.started_at)

    def test_busy_status_prevents_start(self):
        Equipment.objects.filter(pk=self.equipment.pk).update(status=Equipment.Status.BUSY)
        with self.assertRaises(ValidationError):
            start_application(self.first.pk)

    def test_cancel_waiting_does_not_release_working_equipment(self):
        start_application(self.first.pk)
        cancelled = cancel_application(self.second.pk)
        self.equipment.refresh_from_db()
        self.assertEqual(cancelled.status, Application.Status.CANCELLED)
        self.assertEqual(self.equipment.status, Equipment.Status.BUSY)
        self.assertIsNone(cancelled.completed_at)

    def test_cancel_working_releases_equipment(self):
        start_application(self.first.pk)
        cancelled = cancel_application(self.first.pk)
        self.equipment.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(cancelled.status, Application.Status.CANCELLED)
        self.assertEqual(self.equipment.status, Equipment.Status.AVAILABLE)
        self.assertIsNone(cancelled.completed_at)
        self.assertEqual(self.second.status, Application.Status.WAITING)

    def test_invalid_transitions(self):
        with self.assertRaises(ValidationError):
            complete_application(self.first.pk)
        start_application(self.first.pk)
        with self.assertRaises(ValidationError):
            start_application(self.first.pk)
        complete_application(self.first.pk)
        cancel_application(self.second.pk)
        for application in (self.first, self.second):
            for operation in (start_application, complete_application, cancel_application):
                with self.subTest(application=application.pk, operation=operation.__name__):
                    with self.assertRaises(ValidationError):
                        operation(application.pk)

    def test_equipment_failure_rolls_back_application_change(self):
        # Ошибка второй записи не должна оставлять заявку в новом статусе.
        with patch.object(Equipment, "save", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                start_application(self.first.pk)
        self.first.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(self.first.status, Application.Status.WAITING)
        self.assertIsNone(self.first.started_at)
        self.assertEqual(self.equipment.status, Equipment.Status.AVAILABLE)


    def test_maintenance_blocks_start_and_waiting_cancel_preserves_status(self):
        set_equipment_maintenance(self.equipment.pk, True)
        with self.assertRaises(ValidationError):
            start_application(self.first.pk)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Application.Status.WAITING)
        self.assertIsNone(self.first.started_at)
        cancel_application(self.second.pk)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.status, Equipment.Status.MAINTENANCE)
        set_equipment_maintenance(self.equipment.pk, False)
        start_application(self.first.pk)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.status, Equipment.Status.BUSY)

    def test_current_work_prevents_both_maintenance_actions(self):
        start_application(self.first.pk)
        for enabled in (True, False):
            with self.assertRaises(ValidationError):
                set_equipment_maintenance(self.equipment.pk, enabled)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.status, Equipment.Status.BUSY)

    def test_maintenance_checks_work_even_if_equipment_status_is_stale(self):
        start_application(self.first.pk)
        Equipment.objects.filter(pk=self.equipment.pk).update(status=Equipment.Status.AVAILABLE)
        with self.assertRaises(ValidationError):
            set_equipment_maintenance(self.equipment.pk, True)


    def test_admin_maintenance_actions_and_stale_equipment_edit(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from .admin import EquipmentAdmin

        model_admin = EquipmentAdmin(Equipment, admin.site)
        request = RequestFactory().post("/admin/")
        queryset = Equipment.objects.filter(pk=self.equipment.pk)
        stale_equipment = Equipment.objects.get(pk=self.equipment.pk)
        with patch.object(model_admin, "message_user"):
            model_admin.enable_maintenance(request, queryset)
            self.equipment.refresh_from_db()
            self.assertEqual(self.equipment.status, Equipment.Status.MAINTENANCE)
            model_admin.save_model(request, stale_equipment, None, True)
            self.equipment.refresh_from_db()
            self.assertEqual(self.equipment.status, Equipment.Status.MAINTENANCE)
            model_admin.disable_maintenance(request, queryset)
            start_application(self.first.pk)
            model_admin.enable_maintenance(request, queryset)
            model_admin.disable_maintenance(request, queryset)
            self.equipment.refresh_from_db()
            self.assertEqual(self.equipment.status, Equipment.Status.BUSY)

    def test_stale_admin_application_edit_preserves_service_transition(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from types import SimpleNamespace
        from .admin import ApplicationAdmin

        start_application(self.first.pk)
        self.first.customer_name = "Новое имя"
        model_admin = ApplicationAdmin(Application, admin.site)
        model_admin.save_model(
            RequestFactory().post("/admin/"), self.first,
            SimpleNamespace(changed_data=["customer_name"]), True,
        )
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Application.Status.IN_PROGRESS)
        self.assertIsNotNone(self.first.started_at)
        self.assertEqual(self.first.customer_name, "Новое имя")


    def test_replace_waiting_with_available_or_busy_equipment_preserves_statuses(self):
        start_application(self.second.pk)
        for status in (Equipment.Status.AVAILABLE, Equipment.Status.BUSY):
            with self.subTest(status=status):
                target = Equipment.objects.create(name="Другой", bucket_volume="3.00", status=status)
                before = Application.objects.values().get(pk=self.first.pk)
                equipment_before = list(Equipment.objects.values())
                replace_application_equipment(self.first.pk, target.pk)
                after = Application.objects.values().get(pk=self.first.pk)
                self.assertEqual(after, {**before, "equipment_id": target.pk})
                self.assertEqual(list(Equipment.objects.values()), equipment_before)

    def test_replace_working_releases_previous_and_updates_public_cards(self):
        start_application(self.first.pk)
        target = Equipment.objects.create(name="Новый погрузчик", bucket_volume="3.00")
        before = Application.objects.values().get(pk=self.first.pk)
        replace_application_equipment(self.first.pk, target.pk)
        self.assertEqual(Application.objects.values().get(pk=self.first.pk), {**before, "equipment_id": target.pk})
        self.equipment.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(self.equipment.status, Equipment.Status.AVAILABLE)
        self.assertEqual(target.status, Equipment.Status.BUSY)
        response = self.client.get(reverse("home"))
        cards = {item.pk: item for item in response.context["equipment_list"]}
        self.assertEqual(cards[self.equipment.pk].current_work, [])
        self.assertEqual([app.pk for app in cards[target.pk].current_work], [self.first.pk])
        self.assertContains(response, "Текущая работа: А — Б")
        self.assertContains(response, target.name)

    def test_replace_working_rejects_busy_and_current_work_with_stale_status(self):
        target = Equipment.objects.create(name="Другой", bucket_volume="3.00")
        replace_application_equipment(self.second.pk, target.pk)
        start_application(self.first.pk)
        start_application(self.second.pk)
        for status in (Equipment.Status.BUSY, Equipment.Status.AVAILABLE):
            with self.subTest(status=status):
                Equipment.objects.filter(pk=target.pk).update(status=status)
                before = Application.objects.values().get(pk=self.first.pk)
                equipment_before = list(Equipment.objects.values())
                with self.assertRaises(ValidationError):
                    replace_application_equipment(self.first.pk, target.pk)
                self.assertEqual(Application.objects.values().get(pk=self.first.pk), before)
                self.assertEqual(list(Equipment.objects.values()), equipment_before)

    def test_replace_rejects_maintenance_and_terminal_applications(self):
        target = Equipment.objects.create(name="Другой", bucket_volume="3.00")
        for status in Application.Status.values:
            with self.subTest(status=status):
                Application.objects.filter(pk=self.first.pk).update(status=status)
                target_status = Equipment.Status.MAINTENANCE if status in (
                    Application.Status.WAITING, Application.Status.IN_PROGRESS
                ) else Equipment.Status.AVAILABLE
                Equipment.objects.filter(pk=target.pk).update(status=target_status)
                before = Application.objects.values().get(pk=self.first.pk)
                equipment_before = list(Equipment.objects.values())
                with self.assertRaises(ValidationError):
                    replace_application_equipment(self.first.pk, target.pk)
                self.assertEqual(Application.objects.values().get(pk=self.first.pk), before)
                self.assertEqual(list(Equipment.objects.values()), equipment_before)

    def test_replace_same_equipment_is_noop_in_every_status(self):
        for status in Application.Status.values:
            with self.subTest(status=status):
                Application.objects.filter(pk=self.first.pk).update(status=status)
                before = Application.objects.values().get(pk=self.first.pk)
                equipment_before = list(Equipment.objects.values())
                with patch.object(Application, "save") as save_application, patch.object(Equipment, "save") as save_equipment:
                    replace_application_equipment(self.first.pk, self.equipment.pk)
                save_application.assert_not_called()
                save_equipment.assert_not_called()
                self.assertEqual(Application.objects.values().get(pk=self.first.pk), before)
                self.assertEqual(list(Equipment.objects.values()), equipment_before)

    def test_replace_rolls_back_if_equipment_write_fails(self):
        target = Equipment.objects.create(name="Другой", bucket_volume="3.00")
        start_application(self.first.pk)
        before = Application.objects.values().get(pk=self.first.pk)
        equipment_before = list(Equipment.objects.values())
        original_save = Equipment.save

        def fail_on_replacement(equipment, *args, **kwargs):
            if equipment.pk == target.pk:
                raise RuntimeError("write failed")
            return original_save(equipment, *args, **kwargs)

        with patch.object(Equipment, "save", fail_on_replacement):
            with self.assertRaises(RuntimeError):
                replace_application_equipment(self.first.pk, target.pk)
        self.assertEqual(Application.objects.values().get(pk=self.first.pk), before)
        self.assertEqual(list(Equipment.objects.values()), equipment_before)

    def test_admin_replacement_saves_other_fields_without_changing_dates(self):
        from django.contrib.auth import get_user_model
        admin_user = get_user_model().objects.create_superuser(username="replacement_admin", password=None)
        self.client.force_login(admin_user)
        start_application(self.first.pk)
        target = Equipment.objects.create(name="Другой", bucket_volume="3.00")
        before = Application.objects.values().get(pk=self.first.pk)
        response = self.client.post(reverse("admin:main_application_change", args=[self.first.pk]), {
            "section": self.first.section_id, "equipment": target.pk, "executor": "",
            "customer_name": "Новое имя", "phone": self.first.phone,
            "urgency": self.first.urgency, "photos-TOTAL_FORMS": "0",
            "photos-INITIAL_FORMS": "0", "photos-MIN_NUM_FORMS": "0", "photos-MAX_NUM_FORMS": "1000",
            "_save": "Сохранить",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Application.objects.values().get(pk=self.first.pk), {
            **before, "equipment_id": target.pk, "customer_name": "Новое имя",
        })
        self.equipment.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(self.equipment.status, Equipment.Status.AVAILABLE)
        self.assertEqual(target.status, Equipment.Status.BUSY)

    def test_admin_replacement_error_is_message_and_preserves_all_data(self):
        from django.contrib.auth import get_user_model
        admin_user = get_user_model().objects.create_superuser(username="replacement_admin", password=None)
        self.client.force_login(admin_user)
        start_application(self.first.pk)
        target = Equipment.objects.create(name="Другой", bucket_volume="3.00", status=Equipment.Status.MAINTENANCE)
        before = Application.objects.values().get(pk=self.first.pk)
        equipment_before = list(Equipment.objects.values())
        response = self.client.post(reverse("admin:main_application_change", args=[self.first.pk]), {
            "section": self.first.section_id, "equipment": target.pk, "executor": "",
            "customer_name": "Не сохранять", "phone": self.first.phone,
            "urgency": self.first.urgency, "photos-TOTAL_FORMS": "0",
            "photos-INITIAL_FORMS": "0", "photos-MIN_NUM_FORMS": "0", "photos-MAX_NUM_FORMS": "1000",
            "_save": "Сохранить",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Выбранный погрузчик на техническом обслуживании.")
        self.assertEqual(Application.objects.values().get(pk=self.first.pk), before)
        self.assertEqual(list(Equipment.objects.values()), equipment_before)


class PublicApplicationTests(TestCase):
    def setUp(self):
        self.direction = Direction.objects.create(name="Первое направление")
        self.section = RoadSection.objects.create(
            direction=self.direction, start_point="Начало", end_point="Конец"
        )
        self.equipment = Equipment.objects.create(name="Погрузчик", bucket_volume="2.70")
        self.data = {
            "direction": self.direction.pk, "section": self.section.pk,
            "equipment": self.equipment.pk, "urgency": Application.Urgency.NORMAL,
            "customer_name": "Частное имя", "phone": "+77001234567",
        }

    def test_submission_redirects_and_does_not_change_availability(self):
        for status in (Equipment.Status.AVAILABLE, Equipment.Status.BUSY):
            with self.subTest(status=status):
                Equipment.objects.filter(pk=self.equipment.pk).update(status=status)
                response = self.client.post(reverse("home"), {
                    **self.data, "status": "in_progress", "equipment_status": "busy",
                })
                self.assertRedirects(response, reverse("home"))
                application = Application.objects.latest("pk")
                self.assertEqual(application.status, Application.Status.WAITING)
                self.assertEqual(application.customer_name, self.data["customer_name"])
                self.assertEqual(application.phone, self.data["phone"])
                self.assertIsNone(application.started_at)
                self.equipment.refresh_from_db()
                self.assertEqual(self.equipment.status, status)
        self.assertEqual(Application.objects.count(), 2)

    def test_confirmation_contains_number_but_no_contacts_and_refresh_does_not_resubmit(self):
        response = self.client.post(reverse("home"), self.data, follow=True)
        application = Application.objects.get()
        self.assertContains(response, f"Заявка №{application.pk} принята")
        self.assertNotContains(response, self.data["customer_name"])
        self.assertNotContains(response, self.data["phone"])
        self.client.get(reverse("home"))
        self.assertEqual(Application.objects.count(), 1)

    def test_section_must_belong_to_direction(self):
        other = Direction.objects.create(name="Другое направление")
        response = self.client.post(reverse("home"), {**self.data, "direction": other.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn("section", response.context["form"].errors)
        self.assertContains(response, self.data["customer_name"])
        self.assertFalse(Application.objects.exists())

    def test_all_fields_are_required(self):
        for field in self.data:
            with self.subTest(field=field):
                response = self.client.post(reverse("home"), {**self.data, field: ""})
                self.assertIn(field, response.context["form"].errors)
        self.assertFalse(Application.objects.exists())

    def test_invalid_choices_are_rejected(self):
        for field in ("direction", "section", "equipment", "urgency"):
            with self.subTest(field=field):
                response = self.client.post(reverse("home"), {**self.data, field: "invalid"})
                self.assertIn(field, response.context["form"].errors)
        self.assertFalse(Application.objects.exists())

    def test_busy_equipment_shows_current_section_without_contacts(self):
        application = Application.objects.create(
            section=self.section, equipment=self.equipment,
            customer_name="Скрытый заказчик", phone="987654321",
            urgency=Application.Urgency.NORMAL,
        )
        start_application(application.pk)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Занят")
        self.assertContains(response, "Начало — Конец")
        self.assertNotContains(response, "Направление: Первое направление")
        self.assertNotContains(response, application.customer_name)
        self.assertNotContains(response, application.phone)


    def test_old_search_parameter_is_ignored_and_history_is_preserved(self):
        applications = [
            Application.objects.create(
                section=self.section, equipment=self.equipment,
                customer_name="Секретное имя", phone="+77009998877",
                urgency=Application.Urgency.NORMAL, status=status,
            )
            for status in Application.Status.values
        ]
        baseline = self.client.get(reverse("home"))
        for number in (str(applications[0].pk), "999999", "", "abc", "-1", "1.5",
                       "9" * 100, "9223372036854775808", "0", "١", "１２"):
            with self.subTest(number=number):
                response = self.client.get(reverse("home"), {"number": number})
                self.assertEqual(response.status_code, 200)
                for key in ("search_requested", "search_number", "found_application"):
                    self.assertNotIn(key, response.context)
                self.assertEqual(
                    list(response.context["all_applications"]),
                    list(baseline.context["all_applications"]),
                )
                self.assertNotContains(response, 'id="search-result"')
                self.assertNotContains(response, "Заявка не найдена")
                for application in applications:
                    self.assertContains(response, f"<td>№{application.pk}</td>", html=True)
                    self.assertContains(response, application.get_status_display())
                    self.assertNotContains(response, application.customer_name)
                    self.assertNotContains(response, application.phone)

    def test_gallery_is_not_public_for_any_application_status(self):
        completed_at = timezone.now()
        photos = []
        for status in Application.Status.values:
            application = Application.objects.create(
                section=self.section, equipment=self.equipment,
                customer_name="Непубличное имя", phone="+77005554433",
                urgency=Application.Urgency.NORMAL, status=status,
                completed_at=completed_at if status == Application.Status.COMPLETED else None,
            )
            photo = WorkPhoto.objects.create(
                application=application, image=f"work_photos/{status}.jpg"
            )
            photos.append(photo)
        Application.objects.create(
            section=self.section, equipment=self.equipment, customer_name="Без фото",
            phone="+77001112233", urgency=Application.Urgency.NORMAL,
            status=Application.Status.COMPLETED, completed_at=completed_at,
        )
        response = self.client.get(reverse("home"))
        self.assertNotIn("photos", response.context)
        for photo in photos:
            self.assertNotContains(response, photo.image.url)
        self.assertEqual(WorkPhoto.objects.count(), len(photos))
        self.assertNotContains(response, "Непубличное имя")
        self.assertNotContains(response, "+77005554433")
        self.assertNotContains(response, "Без фото")
        self.assertNotContains(response, "+77001112233")


    def test_search_is_absent_on_desktop_and_mobile(self):
        user_agents = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36",
        )
        for user_agent in user_agents:
            with self.subTest(user_agent=user_agent):
                response = self.client.get(reverse("home"), HTTP_USER_AGENT=user_agent)
                self.assertTemplateUsed(response, "main/home.html")
                for marker in (
                    'id="application-search"', 'id="search-heading"',
                    'id="search-number"', 'id="search-result"', 'name="number"',
                    "Поиск заявки по номеру", "Найти заявку", "Заявка не найдена",
                ):
                    self.assertNotContains(response, marker)
                self.assertContains(response, '<form id="application-form" method="post">')
                self.assertContains(response, 'class="history-table"')

    def test_admin_photo_uploads_are_preserved(self):
        from io import BytesIO
        from tempfile import TemporaryDirectory

        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        application = Application.objects.create(
            section=self.section, equipment=self.equipment,
            customer_name="Заказчик", phone="123", urgency=Application.Urgency.NORMAL,
        )
        self.client.force_login(get_user_model().objects.create_superuser(
            username="photo_admin", password=None,
        ))
        image = BytesIO()
        Image.new("RGB", (1, 1)).save(image, format="PNG")
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            for inline in (False, True):
                with self.subTest(inline=inline):
                    upload = SimpleUploadedFile("photo.png", image.getvalue(), content_type="image/png")
                    if inline:
                        url = reverse("admin:main_application_change", args=[application.pk])
                        existing = application.photos.get()
                        data = {
                            "section": self.section.pk, "equipment": self.equipment.pk,
                            "executor": "", "customer_name": application.customer_name,
                            "phone": application.phone, "urgency": application.urgency,
                            "photos-TOTAL_FORMS": "2", "photos-INITIAL_FORMS": "1",
                            "photos-MIN_NUM_FORMS": "0", "photos-MAX_NUM_FORMS": "1000",
                            "photos-0-id": existing.pk, "photos-0-application": application.pk,
                            "photos-1-application": application.pk, "photos-1-image": upload,
                            "_save": "Сохранить",
                        }
                    else:
                        url = reverse("admin:main_workphoto_add")
                        data = {"application": application.pk, "image": upload, "_save": "Сохранить"}
                    self.assertEqual(self.client.get(url).status_code, 200)
                    response = self.client.post(url, data)
                    self.assertEqual(response.status_code, 302)
                    photo = application.photos.latest("pk")
                    with photo.image.open("rb") as stored:
                        self.assertEqual(stored.read(), image.getvalue())
                    self.assertIsNotNone(photo.uploaded_at)
                    self.assertNotContains(self.client.get(reverse("home")), photo.image.url)
            self.assertEqual(application.photos.count(), 2)

    def test_maintenance_is_visible_but_cannot_be_selected_or_submitted(self):
        set_equipment_maintenance(self.equipment.pk, True)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Техническое обслуживание")
        options = response.context["form"]["equipment"].subwidgets
        self.assertTrue(next(option.data["attrs"].get("disabled") for option in options
                             if str(option.data["value"]) == str(self.equipment.pk)))
        response = self.client.post(reverse("home"), self.data)
        self.assertIn("equipment", response.context["form"].errors)
        self.assertFalse(Application.objects.exists())
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.status, Equipment.Status.MAINTENANCE)

    def test_maintenance_is_rechecked_after_form_validation(self):
        from .forms import ApplicationForm
        original_is_valid = ApplicationForm.is_valid

        def validate_then_enable_maintenance(form):
            result = original_is_valid(form)
            set_equipment_maintenance(self.equipment.pk, True)
            return result

        with patch.object(ApplicationForm, "is_valid", validate_then_enable_maintenance):
            response = self.client.post(reverse("home"), self.data)
        self.assertIn("equipment", response.context["form"].errors)
        self.assertFalse(Application.objects.exists())

    def test_radio_fields_hero_and_equipment_cards(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'type="radio" name="direction"')
        self.assertContains(response, 'type="radio" name="section"')
        self.assertNotContains(response, 'id="astana-clock"')
        self.assertContains(response, "Содержание автомобильных дорог")
        self.assertContains(response, 'placeholder="имя"')
        self.assertContains(response, 'placeholder="+7-------"')
        self.assertContains(response, 'class="equipment-card"')
        self.assertContains(response, 'Detailed%20Yellow%20Wheel%20Loader%20Cutout.png')
        content = response.content.decode()
        self.assertLess(content.index('class="hero"'), content.index('id="application-form"'))
        self.assertLess(content.index('id="application-form"'), content.index('id="equipment-heading"'))
        self.assertLess(content.index('id="equipment-heading"'), content.index('id="all-applications-heading"'))


    def test_header_preserves_current_contact_links(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'href="tel:+77064066711"')
        self.assertContains(response, 'href="https://wa.me/77064066711"')
        self.assertNotContains(response, "по Астане")


    def test_form_radio_labels_and_selected_values_survive_errors(self):
        for status in (Equipment.Status.AVAILABLE, Equipment.Status.BUSY):
            with self.subTest(status=status):
                Equipment.objects.filter(pk=self.equipment.pk).update(status=status)
                response = self.client.post(reverse("home"), {**self.data, "phone": ""})
                form = response.context["form"]
                for name in ("direction", "section", "equipment", "urgency"):
                    self.assertContains(response, f'type="radio" name="{name}"')
                    checked = [option for option in form[name].subwidgets if option.data["selected"]]
                    self.assertEqual(len(checked), 1)
                    self.assertEqual(str(checked[0].data["value"]), str(self.data[name]))
                self.assertEqual(form.fields["section"].label_from_instance(self.section), "Начало — Конец")
                expected = "Свободен" if status == Equipment.Status.AVAILABLE else "Не свободен"
                self.assertContains(response, f"Ковш 2,7 м³ — {expected}")
                self.assertFalse(Application.objects.exists())
