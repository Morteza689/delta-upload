"""Delete guest files whose deadline has passed.

For cron, hourly or daily:

    python manage.py purge_expired_uploads

Without it guest files are never removed and the disk fills.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from upload_module.guest import purge_expired
from upload_module.models import UploadFile


class Command(BaseCommand):
    help = 'فایل‌های مهمان منقضی‌شده را حذف می‌کند'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='فقط گزارش بده، چیزی حذف نکن')

    def handle(self, *args, **options):
        now = timezone.now()

        if options['dry_run']:
            expired = UploadFile.objects.filter(
                auto_delete_at__isnull=False, auto_delete_at__lte=now,
            )
            for upload in expired:
                self.stdout.write(
                    f'[would delete] {upload.slug} '
                    f'({upload.size_bytes or 0:,} bytes, '
                    f'expired {upload.auto_delete_at:%Y-%m-%d %H:%M})'
                )
            self.stdout.write(self.style.WARNING(
                f'dry-run — {expired.count()} فایل حذف نشد'
            ))
            return

        removed, freed = purge_expired(now)
        self.stdout.write(self.style.SUCCESS(
            f'{removed} فایل حذف شد، {freed:,} بایت آزاد شد'
        ))
