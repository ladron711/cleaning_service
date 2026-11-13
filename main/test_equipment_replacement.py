from functools import partial
from threading import Event, Thread
from time import monotonic
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .models import Application, Direction, Equipment, RoadSection
from .services import (
    cancel_application, complete_application, replace_application_equipment,
    set_equipment_maintenance, start_application,
)


class ReplacementFixtures:
    def setUp(self):
        super().setUp()
        direction = Direction.objects.create(name="Направление")
        self.section = RoadSection.objects.create(
            direction=direction, start_point="Начало", end_point="Конец",
        )

    def equipment(self):
        return Equipment.objects.create(name="Погрузчик", bucket_volume="2.70")

    def application(self, working=False):
        app = Application.objects.create(
            section=self.section, equipment=self.equipment(), customer_name="Заказчик",
            phone="123", urgency=Application.Urgency.NORMAL,
        )
        if working:
            start_application(app.pk)
            app.refresh_from_db()
        return app

    def assert_equipment_consistent(self):
        for equipment in Equipment.objects.all():
            active = equipment.application_set.filter(status=Application.Status.IN_PROGRESS).count()
            self.assertLessEqual(active, 1)
            self.assertEqual(equipment.status == Equipment.Status.BUSY, active == 1)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL")
class ConcurrentReplacementTests(ReplacementFixtures, TransactionTestCase):
    def run_contended(self, first, second):
        """Hold the first transaction until PostgreSQL confirms the second waits on it."""
        ready, release, attempting = Event(), Event(), Event()
        outcomes, errors, pids = {}, {}, {}

        def worker(index, operation):
            db = connections["default"]  # Django connections are thread-local.
            db.settings_dict = {
                **db.settings_dict,
                "OPTIONS": {**db.settings_dict.get("OPTIONS", {}), "connect_timeout": 5},
            }
            try:
                db.ensure_connection()
                with db.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '8s'")
                    cursor.execute("SET statement_timeout = '10s'")
                    cursor.execute("SET idle_in_transaction_session_timeout = '15s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[index] = cursor.fetchone()[0]
                if index == 0:
                    with transaction.atomic():
                        operation()
                        outcomes[index] = "ok"
                        ready.set()
                        if not release.wait(10):
                            raise AssertionError("Timed out waiting to release first transaction")
                else:
                    if not ready.wait(10):
                        raise AssertionError("First operation did not acquire its locks")

                    def observe(execute, sql, params, many, context):
                        if "FOR UPDATE" in sql:
                            attempting.set()
                        return execute(sql, params, many, context)

                    with db.execute_wrapper(observe):
                        try:
                            operation()
                            outcomes[index] = "ok"
                        except ValidationError as error:
                            outcomes[index] = error
            except BaseException as error:
                errors[index] = error
            finally:
                db.close()

        threads = [Thread(target=worker, args=(i, op), daemon=True)
                   for i, op in enumerate((first, second))]
        for thread in threads:
            thread.start()
        try:
            self.assertTrue(ready.wait(10), repr(errors))
            self.assertTrue(attempting.wait(10), repr(errors))
            self.assertNotEqual(pids[0], pids[1])
            deadline = monotonic() + 5
            blocked = False
            with connection.cursor() as cursor:
                while monotonic() < deadline:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [pids[1]])
                    if pids[0] in cursor.fetchone()[0]:
                        blocked = True
                        break
            self.assertTrue(blocked, "Second operation never waited on the first transaction")
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=12)
            # Cancel any unexpected lingering SQL before test database cleanup.
            for index, thread in enumerate(threads):
                if thread.is_alive() and index in pids:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_cancel_backend(%s)", [pids[index]])
                    thread.join(timeout=3)
            self.assertFalse(any(thread.is_alive() for thread in threads), "Worker did not stop")
        if errors:
            raise next(iter(errors.values()))
        return outcomes

    def test_two_working_applications_compete_for_one_loader(self):
        first, second, target = self.application(True), self.application(True), self.equipment()
        second_before = Application.objects.values().get(pk=second.pk)
        result = self.run_contended(
            partial(replace_application_equipment, first.pk, target.pk),
            partial(replace_application_equipment, second.pk, target.pk),
        )
        self.assertEqual(result[0], "ok")
        self.assertIsInstance(result[1], ValidationError)
        first.refresh_from_db()
        self.assertEqual(first.equipment_id, target.pk)
        self.assertEqual(Application.objects.values().get(pk=second.pk), second_before)
        self.assert_equipment_consistent()

    def test_replacement_and_start_in_both_orders(self):
        for replacement_first in (True, False):
            with self.subTest(replacement_first=replacement_first):
                app, target = self.application(), self.equipment()
                operations = [partial(replace_application_equipment, app.pk, target.pk),
                              partial(start_application, app.pk)]
                result = self.run_contended(*(operations if replacement_first else operations[::-1]))
                self.assertEqual(result, {0: "ok", 1: "ok"})
                app.refresh_from_db()
                self.assertEqual(app.status, Application.Status.IN_PROGRESS)
                self.assertEqual(app.equipment_id, target.pk)
                self.assertIsNotNone(app.started_at)
                self.assert_equipment_consistent()

    def test_replacement_competes_with_start_of_another_application(self):
        for replacement_first in (True, False):
            with self.subTest(replacement_first=replacement_first):
                app, waiting = self.application(True), self.application()
                original_equipment = app.equipment_id
                target_id = waiting.equipment_id
                operations = [partial(replace_application_equipment, app.pk, target_id),
                              partial(start_application, waiting.pk)]
                result = self.run_contended(*(operations if replacement_first else operations[::-1]))
                self.assertEqual(result[0], "ok")
                self.assertIsInstance(result[1], ValidationError)
                app.refresh_from_db()
                waiting.refresh_from_db()
                self.assertEqual(app.equipment_id, target_id if replacement_first else original_equipment)
                self.assertEqual(waiting.status, Application.Status.WAITING if replacement_first
                                 else Application.Status.IN_PROGRESS)
                self.assert_equipment_consistent()

    def test_replacement_and_completion_or_cancellation_in_both_orders(self):
        for operation, status in ((complete_application, Application.Status.COMPLETED),
                                  (cancel_application, Application.Status.CANCELLED)):
            for replacement_first in (True, False):
                with self.subTest(operation=operation.__name__, replacement_first=replacement_first):
                    app, target = self.application(True), self.equipment()
                    original_equipment = app.equipment_id
                    operations = [partial(replace_application_equipment, app.pk, target.pk),
                                  partial(operation, app.pk)]
                    result = self.run_contended(*(operations if replacement_first else operations[::-1]))
                    self.assertEqual(result[0], "ok")
                    if replacement_first:
                        self.assertEqual(result[1], "ok")
                    else:
                        self.assertIsInstance(result[1], ValidationError)
                    app.refresh_from_db()
                    self.assertEqual(app.status, status)
                    self.assertEqual(app.equipment_id, target.pk if replacement_first else original_equipment)
                    self.assert_equipment_consistent()

    def test_replacement_and_target_maintenance_in_both_orders(self):
        for replacement_first in (True, False):
            with self.subTest(replacement_first=replacement_first):
                app, target = self.application(True), self.equipment()
                original_equipment = app.equipment_id
                operations = [partial(replace_application_equipment, app.pk, target.pk),
                              partial(set_equipment_maintenance, target.pk, True)]
                result = self.run_contended(*(operations if replacement_first else operations[::-1]))
                self.assertEqual(result[0], "ok")
                self.assertIsInstance(result[1], ValidationError)
                app.refresh_from_db()
                target.refresh_from_db()
                self.assertEqual(app.equipment_id, target.pk if replacement_first else original_equipment)
                self.assertEqual(target.status, Equipment.Status.BUSY if replacement_first else Equipment.Status.MAINTENANCE)
                self.assert_equipment_consistent()


class ReplacementEdgeTests(ReplacementFixtures, TestCase):
    def test_unknown_loader_leaves_application_and_equipment_unchanged(self):
        for working in (False, True):
            with self.subTest(working=working):
                app = self.application(working)
                before = Application.objects.values().get(pk=app.pk)
                equipment_before = list(Equipment.objects.order_by("pk").values())
                missing_id = Equipment.objects.latest("pk").pk + 1
                with self.assertRaisesMessage(ValidationError, "Выбранный погрузчик больше не существует."):
                    replace_application_equipment(app.pk, missing_id)
                self.assertEqual(Application.objects.values().get(pk=app.pk), before)
                self.assertEqual(list(Equipment.objects.order_by("pk").values()), equipment_before)

    def test_public_history_order_survives_replacement(self):
        for working in (False, True):
            with self.subTest(working=working):
                app = self.application(working)
                newer = self.application()
                target = self.equipment()
                response = self.client.get(reverse("home"))
                order = [item.pk for item in response.context["all_applications"]]
                replace_application_equipment(app.pk, target.pk)
                response = self.client.get(reverse("home"))
                self.assertEqual([item.pk for item in response.context["all_applications"]], order)
                history = response.content.decode().split('class="applications-history"', 1)[1].split('</section>', 1)[0]
                self.assertLess(history.index(f"№{newer.pk}</td>"), history.index(f"№{app.pk}</td>"))

    def admin_clients(self):
        user = get_user_model().objects.create_superuser(username="replacement_tester", password=None)
        self.client.force_login(user)
        other = Client()
        other.force_login(user)
        return other

    def form_data(self, app, target):
        return {
            "section": app.section_id, "equipment": target.pk, "executor": "",
            "customer_name": app.customer_name, "phone": app.phone, "urgency": app.urgency,
            "photos-TOTAL_FORMS": "0", "photos-INITIAL_FORMS": "0",
            "photos-MIN_NUM_FORMS": "0", "photos-MAX_NUM_FORMS": "1000", "_save": "Save",
        }

    def test_stale_admin_form_uses_status_changed_by_another_request(self):
        other = self.admin_clients()
        for action in ("start_selected", "complete_selected", "cancel_selected"):
            with self.subTest(action=action):
                app = self.application(working=action == "complete_selected")
                target = self.equipment()
                url = reverse("admin:main_application_change", args=[app.pk])
                self.assertEqual(self.client.get(url).status_code, 200)
                data = self.form_data(app, target)
                response = other.post(reverse("admin:main_application_changelist"), {
                    "action": action, "_selected_action": [app.pk], "index": "0",
                })
                self.assertEqual(response.status_code, 302)
                after_action = Application.objects.values().get(pk=app.pk)
                expected_status = {"start_selected": Application.Status.IN_PROGRESS,
                                   "complete_selected": Application.Status.COMPLETED,
                                   "cancel_selected": Application.Status.CANCELLED}[action]
                self.assertEqual(after_action["status"], expected_status)
                self.assertEqual(self.client.post(url, data).status_code, 302)
                expected = dict(after_action)
                if action == "start_selected":
                    expected["equipment_id"] = target.pk
                self.assertEqual(Application.objects.values().get(pk=app.pk), expected)
                self.assert_equipment_consistent()

    def test_terminal_admin_post_cannot_override_readonly_equipment(self):
        self.admin_clients()
        for operation in (complete_application, cancel_application):
            with self.subTest(operation=operation.__name__):
                app, target = self.application(True), self.equipment()
                operation(app.pk)
                before = Application.objects.values().get(pk=app.pk)
                equipment_before = list(Equipment.objects.order_by("pk").values())
                url = reverse("admin:main_application_change", args=[app.pk])
                response = self.client.get(url)
                self.assertNotIn("equipment", response.context["adminform"].form.fields)
                self.assertEqual(self.client.post(url, self.form_data(app, target)).status_code, 302)
                self.assertEqual(Application.objects.values().get(pk=app.pk), before)
                self.assertEqual(list(Equipment.objects.order_by("pk").values()), equipment_before)
