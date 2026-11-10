"""Preserve discoverability once when historical functions are consolidated.
Old type settings and records remain unchanged; users may subsequently turn off
the replacement. Do not continuously OR legacy activation into the new UI.
"""
from django.db import migrations
from django.db.models import F


def activate_replacements(apps,schema_editor):
    Setting=apps.get_model('care','ModuleSetting')
    Room=apps.get_model('care','Room')
    Version=apps.get_model('care','ModuleConfigVersion')
    affected=set()
    for old,new in [('grooming','hygiene'),('reproduction','observation')]:
        for original in Setting.objects.filter(key=old,enabled=True).iterator():
            replacement=Setting.objects.filter(room_id=original.room_id,key=new).first()
            if replacement and not replacement.enabled:
                replacement.enabled=True
                replacement.pinned=replacement.pinned or original.pinned
                replacement.save(update_fields=['enabled','pinned'])
                affected.add(original.room_id)
    for room_id in affected:
        Room.objects.filter(pk=room_id).update(settings_version=F('settings_version')+1)
        rows=list(Setting.objects.filter(room_id=room_id).order_by('position','key'))
        Version.objects.create(room_id=room_id,settings={'enabled':[r.key for r in rows if r.enabled],
            'order':[r.key for r in rows],'pinned':[r.key for r in rows if r.pinned and r.enabled],
            'reason':'phase7 non-destructive module consolidation'})


class Migration(migrations.Migration):
    dependencies=[('care','0005_presets_actions_and_schedule_periods')]
    operations=[migrations.RunPython(activate_replacements,migrations.RunPython.noop)]
