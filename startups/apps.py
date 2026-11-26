from django.apps import AppConfig


class StartupsConfig(AppConfig):
    """Kept only for its migrations (ASSUMPTIONS 458): the last one drops the STARTUP table."""

    name = "startups"
