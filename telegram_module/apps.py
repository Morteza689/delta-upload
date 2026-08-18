from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TelegramModuleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'telegram_module'
    verbose_name = _('ذخیره در تلگرام')

    def ready(self):
        from . import signals  # noqa: F401
