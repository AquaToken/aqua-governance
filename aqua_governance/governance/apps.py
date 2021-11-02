from django.apps import AppConfig


class MainConfig(AppConfig):
    name = 'aqua_governance.governance'

    def ready(self):
        from aqua_governance.governance import receivers  # NOQA: F401
