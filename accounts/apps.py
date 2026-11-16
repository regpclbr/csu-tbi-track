from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self) -> None:
        # The server settings checks of `check --deploy` (config/checks.py); `config` is not an app of its own.
        from config import checks  # noqa: F401
