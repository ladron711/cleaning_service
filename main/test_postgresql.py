import unittest

import psycopg2
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from .models import Application, Direction, Equipment, RoadSection


@unittest.skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL")
class PostgreSQLTests(TransactionTestCase):
    def setUp(self):
        direction = Direction.objects.create(name="Тест")
        self.section = RoadSection.objects.create(
            direction=direction, start_point="А", end_point="Б",
        )
        self.equipment = Equipment.objects.create(name="Погрузчик", bucket_volume="2.70")

    def test_database_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Equipment.objects.create(name="Недопустимый", bucket_volume="0")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RoadSection.objects.create(
                direction=self.section.direction, start_point="А", end_point="А",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            RoadSection.objects.create(
                direction=self.section.direction, start_point="А", end_point="Б",
            )
        fields = dict(
            section=self.section, equipment=self.equipment,
            customer_name="Тест", phone="123", urgency=Application.Urgency.NORMAL,
        )
        Application.objects.create(**fields, status=Application.Status.IN_PROGRESS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Application.objects.create(**fields, status=Application.Status.IN_PROGRESS)
        Application.objects.create(**fields, status=Application.Status.WAITING)
        self.assertEqual(Application.objects.count(), 2)

    def test_transaction_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Equipment.objects.filter(pk=self.equipment.pk).update(status=Equipment.Status.BUSY)
                raise RuntimeError("Rollback")
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.status, Equipment.Status.AVAILABLE)

    def test_row_lock_blocks_another_connection_and_releases_after_commit(self):
        config = connection.settings_dict
        other = psycopg2.connect(
            dbname=config["NAME"], user=config["USER"], password=config["PASSWORD"],
            host=config["HOST"], port=config["PORT"], connect_timeout=5,
        )
        try:
            with other.cursor() as cursor:
                cursor.execute("SET lock_timeout = '2s'")
                other.commit()
                with transaction.atomic():
                    Equipment.objects.select_for_update().get(pk=self.equipment.pk)
                    with self.assertRaises(psycopg2.errors.LockNotAvailable):
                        cursor.execute(
                            "SELECT id FROM main_equipment WHERE id = %s FOR UPDATE NOWAIT",
                            [self.equipment.pk],
                        )
                    other.rollback()
                cursor.execute(
                    "SELECT id FROM main_equipment WHERE id = %s FOR UPDATE NOWAIT",
                    [self.equipment.pk],
                )
                self.assertEqual(cursor.fetchone()[0], self.equipment.pk)
                other.rollback()
        finally:
            other.close()
