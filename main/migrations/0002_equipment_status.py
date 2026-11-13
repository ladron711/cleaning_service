from django.db import migrations, models


def copy_status(apps, schema_editor):
    equipment = apps.get_model("main", "Equipment").objects.using(schema_editor.connection.alias)
    equipment.filter(is_on_line=True).update(status="busy")
    equipment.filter(is_on_line=False).update(status="available")


def restore_flag(apps, schema_editor):
    equipment = apps.get_model("main", "Equipment").objects.using(schema_editor.connection.alias)
    equipment.update(is_on_line=False)
    equipment.filter(status="busy").update(is_on_line=True)


class Migration(migrations.Migration):
    dependencies = [("main", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="equipment",
            name="status",
            field=models.CharField(
                choices=[("available", "Свободен"), ("busy", "Занят"),
                         ("maintenance", "Техническое обслуживание")],
                default="available", max_length=16,
            ),
        ),
        # Переносим значения до удаления старого поля.
        migrations.RunPython(copy_status, restore_flag),
        migrations.RemoveField(model_name="equipment", name="is_on_line"),
    ]
