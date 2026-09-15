from django.apps import AppConfig


class OverbaeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "overbae"
    verbose_name = "Overbae"

    def ready(self):
        import overbae.signals  # noqa: F401
