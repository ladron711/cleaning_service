from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Application, Direction, Equipment, RoadSection
from .services import start_application, complete_application, cancel_application


class ApplicationHistoryTests(TestCase):
    def setUp(self):
        direction = Direction.objects.create(name="Направление")
        section = RoadSection.objects.create(
            direction=direction, start_point="Начало", end_point="Конец"
        )
        equipment = Equipment.objects.create(name="Погрузчик", bucket_volume="2.70")
        self.fields = dict(
            section=section, equipment=equipment, customer_name="Частное имя",
            phone="+77001231234", urgency=Application.Urgency.NORMAL,
        )

    def test_creation_order_and_no_personal_data(self):
        first = Application.objects.create(**self.fields)
        Application.objects.filter(pk=first.pk).update(
            status_changed_at=timezone.now() - timedelta(days=1)
        )
        before = timezone.now()
        second = Application.objects.create(**self.fields)
        self.assertGreaterEqual(second.status_changed_at, before)
        response = self.client.get(reverse("home"))
        self.assertEqual(list(response.context["all_applications"]), [second, first])
        history = response.content.decode().split('class="applications-history"', 1)[1].split('</section>', 1)[0]
        self.assertLess(history.index(f"№{second.pk}"), history.index(f"№{first.pk}"))
        self.assertIn("<td>Начало — Конец</td>", history)
        self.assertIn("<td>В ожидании</td>", history)
        for heading in ("№", "Участок", "Статус"):
            self.assertIn(f'<th scope="col">{heading}</th>', history)
        self.assertNotIn(self.fields["customer_name"], history)
        self.assertNotIn(self.fields["phone"], history)

    def test_every_status_transition_moves_application_to_top(self):
        first = Application.objects.create(**self.fields)
        second = Application.objects.create(**self.fields)
        now = timezone.now()
        operations = [
            (start_application, first), (complete_application, first),
            (cancel_application, second),
        ]
        for index, (operation, application) in enumerate(operations, 1):
            changed_at = now + timedelta(minutes=index)
            with patch("main.services.timezone.now", return_value=changed_at):
                operation(application.pk)
            application.refresh_from_db()
            self.assertEqual(application.status_changed_at, changed_at)
            response = self.client.get(reverse("home"))
            self.assertEqual(response.context["all_applications"][0].pk, application.pk)
        self.assertEqual(response.context["all_applications"].count(), 2)

    def test_cancelling_current_work_updates_timestamp(self):
        application = Application.objects.create(**self.fields)
        start_application(application.pk)
        changed_at = timezone.now() + timedelta(minutes=1)
        with patch("main.services.timezone.now", return_value=changed_at):
            cancel_application(application.pk)
        application.refresh_from_db()
        self.assertEqual(application.status_changed_at, changed_at)

    def test_other_edits_and_rejected_transition_do_not_raise_application(self):
        first = Application.objects.create(**self.fields)
        old_time = timezone.now() - timedelta(days=1)
        Application.objects.filter(pk=first.pk).update(status_changed_at=old_time)
        second = Application.objects.create(**self.fields)
        first.refresh_from_db()
        first.customer_name = "Исправленное имя"
        first.phone = "+77005551122"
        first.urgency = Application.Urgency.URGENT
        first.save()
        with self.assertRaises(ValidationError):
            complete_application(first.pk)
        first.refresh_from_db()
        self.assertEqual(first.status_changed_at, old_time)
        response = self.client.get(reverse("home"))
        self.assertEqual(list(response.context["all_applications"]), [second, first])

    def test_empty_history(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Заявок пока нет")
