"""Fetch the exchange rate from the configured provider.

For cron, for example every six hours:

    0 */6 * * * cd /path && python manage.py update_exchange_rate

Does nothing when automatic updates are off in the panel, so cron and the
panel setting cannot fight.
"""
from django.core.management.base import BaseCommand

from payment_module import rate_service
from payment_module.models import RateSetting


class Command(BaseCommand):
    help = 'گرفتن نرخ دلار به تومان از سرویس تنظیم‌شده'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='حتی اگر دریافت خودکار خاموش است هم بگیر',
        )

    def handle(self, *args, **options):
        setting = RateSetting.load()

        if not setting.auto_update and not options['force']:
            self.stdout.write('دریافت خودکار خاموش است؛ کاری انجام نشد.')
            return

        rate, error = rate_service.refresh(setting, force=options['force'])

        if error:
            # Non-zero exit so cron can report the failure.
            self.stderr.write(f'نرخ به‌روز نشد: {error}')
            raise SystemExit(1)

        self.stdout.write(
            f'نرخ به‌روز شد: هر دلار {rate.usd_to_toman:,} تومان '
            f'(از {setting.provider})'
        )
