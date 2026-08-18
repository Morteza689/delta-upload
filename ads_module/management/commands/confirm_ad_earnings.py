"""Move matured ad revenue into wallets.

For cron, daily:

    python manage.py confirm_ad_earnings

Without it earnings stay pending forever and no user can ever withdraw them.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from ads_module.models import AdEarning
from ads_module.services import confirm_user_earnings


class Command(BaseCommand):
    help = 'درآمدهای تبلیغات رسیده را به کیف پول کاربران واریز می‌کند'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='فقط گزارش بده، چیزی واریز نکن',
        )

    def handle(self, *args, **options):
        now = timezone.now()

        user_ids = (
            AdEarning.objects
            .filter(status=AdEarning.Status.PENDING, available_at__lte=now)
            .values_list('user_id', flat=True)
            .distinct()
        )
        user_ids = list(user_ids)

        if not user_ids:
            self.stdout.write('هیچ درآمد آماده‌ای برای تأیید نیست')
            return

        if options['dry_run']:
            for user_id in user_ids:
                pending = AdEarning.objects.filter(
                    user_id=user_id, status=AdEarning.Status.PENDING,
                    available_at__lte=now,
                )
                total = sum(item.micro_cents for item in pending)
                self.stdout.write(
                    f'user {user_id}: {pending.count()} رکورد، {total} میکروسنت'
                )
            self.stdout.write(self.style.WARNING('dry-run — چیزی واریز نشد'))
            return

        from account_module.models import User

        total_records = 0
        total_cents = 0
        for user in User.objects.filter(id__in=user_ids):
            records, cents = confirm_user_earnings(user, now=now)
            total_records += records
            total_cents += cents
            if records:
                self.stdout.write(f'{user}: {records} رکورد -> {cents} سنت')

        self.stdout.write(self.style.SUCCESS(
            f'{total_records} رکورد تأیید شد، مجموع {total_cents} سنت واریز شد'
        ))
