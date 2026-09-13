from django.apps import AppConfig

class BotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bot'

    def ready(self):
        # Wire the Celery worker boot hooks (embedding-model pre-warm).
        from . import celery_signals  # noqa: F401
