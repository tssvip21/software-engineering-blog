from django.apps import AppConfig
from django.db.backends.signals import connection_created


def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor == "sqlite" and "memory" not in str(connection.settings_dict["NAME"]):
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")

class CareConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "care"
    def ready(self):
        connection_created.connect(configure_sqlite, dispatch_uid="care_sqlite_config")
