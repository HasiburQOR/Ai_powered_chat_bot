from django.apps import AppConfig

class KnowledgeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'knowledge'

    def ready(self):
        import knowledge.signals  # noqa: F401

