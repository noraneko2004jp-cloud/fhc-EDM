from django.apps import AppConfig


class DrawingsConfig(AppConfig):
    name = "drawings"
    verbose_name = "図面"

    def ready(self):
        from . import signals  # noqa: F401
