from django.db import migrations


def initial_pins(apps, schema_editor):
    ModuleSetting = apps.get_model("care", "ModuleSetting")
    ModuleSetting.objects.filter(enabled=True, key__in=("meal", "treat", "water", "walk", "toilet", "weight")).update(pinned=True)


class Migration(migrations.Migration):
    dependencies = [("care", "0002_today_and_cadence")]
    operations = [migrations.RunPython(initial_pins, migrations.RunPython.noop)]
