"""Fill in size_bytes_cached for older files.

Files created before the field existed have a size of zero, and zero means the
quota ignores them: unlimited free space for anyone with an old account.
"""
from django.core.management.base import BaseCommand

from upload_module.models import UploadFile


class Command(BaseCommand):
    help = 'محاسبه و ذخیره‌ی حجم فایل‌هایی که هنوز ثبت نشده'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        pending = UploadFile.objects.filter(size_bytes_cached=0).exclude(file='')
        fixed = missing = 0

        for upload in pending.iterator():
            size = upload.size_bytes
            if size is None:
                missing += 1
                continue

            if not options['dry_run']:
                UploadFile.objects.filter(pk=upload.pk).update(size_bytes_cached=size)
            fixed += 1

        verb = 'محاسبه شد' if options['dry_run'] else 'ثبت شد'
        self.stdout.write(f'{fixed} فایل {verb}، {missing} فایل روی storage نبود')
