from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class EquipmentStatusMigrationTests(TransactionTestCase):
    def test_existing_flags_are_preserved_as_statuses(self):
        old_target = [("main", "0001_initial")]
        new_target = [("main", "0002_equipment_status")]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            old_apps = executor.loader.project_state(old_target).apps
            equipment = old_apps.get_model("main", "Equipment")
            busy = equipment.objects.create(name="Занятый", bucket_volume="2.70", is_on_line=True)
            available = equipment.objects.create(name="Свободный", bucket_volume="3.00", is_on_line=False)
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            new_apps = executor.loader.project_state(new_target).apps
            equipment = new_apps.get_model("main", "Equipment")
            self.assertEqual(equipment.objects.get(pk=busy.pk).status, "busy")
            self.assertEqual(equipment.objects.get(pk=available.pk).status, "available")
            self.assertEqual(equipment.objects.get(pk=busy.pk).bucket_volume, equipment._meta.get_field("bucket_volume").to_python(busy.bucket_volume))
            self.assertEqual(equipment.objects.get(pk=available.pk).name, available.name)
            self.assertNotIn("is_on_line", [field.name for field in equipment._meta.fields])
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

    def test_history_timestamp_uses_existing_dates(self):
        from datetime import timedelta
        from django.utils import timezone

        old_target = [("main", "0003_alter_equipment_name")]
        new_target = [("main", "0004_application_status_changed_at")]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            direction = apps.get_model("main", "Direction").objects.create(name="Тест")
            section = apps.get_model("main", "RoadSection").objects.create(
                direction=direction, start_point="А", end_point="Б"
            )
            equipment = apps.get_model("main", "Equipment").objects.create(
                name="Погрузчик", bucket_volume="2.70"
            )
            application = apps.get_model("main", "Application")
            created_at = timezone.now() - timedelta(days=3)
            started_at = created_at + timedelta(days=1)
            completed_at = started_at + timedelta(days=1)
            expected = {}
            for status, start, end in [
                ("waiting", None, None), ("in_progress", started_at, None),
                ("completed", started_at, completed_at), ("cancelled", started_at, None),
            ]:
                obj = application.objects.create(
                    section=section, equipment=equipment, status=status,
                    customer_name="Тест", phone="123", urgency="normal",
                    started_at=start, completed_at=end,
                )
                application.objects.filter(pk=obj.pk).update(created_at=created_at)
                expected[obj.pk] = end or start or created_at
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            application = executor.loader.project_state(new_target).apps.get_model("main", "Application")
            self.assertEqual(dict(application.objects.values_list("pk", "status_changed_at")), expected)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
