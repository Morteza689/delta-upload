"""Move uploaded files from local disk to S3.

Run once, after USE_S3 is switched on and the bucket is confirmed private.

Safe to re-run: files already present with a matching size are skipped, so
an interrupted run continues rather than starting over.

Local files are left in place. Delete them yourself once you have checked
the transfer, so a mistake is still recoverable.
"""
from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.base import BaseCommand, CommandError

from upload_module.models import UploadFile


class Command(BaseCommand):
    help = 'فایل‌های آپلودی را از دیسک محلی به S3 منتقل می‌کند'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='فقط گزارش بده، چیزی منتقل نکن')
        parser.add_argument('--delete-local', action='store_true',
                            help='بعد از انتقال موفق، نسخه محلی را حذف کن')
        parser.add_argument('--limit', type=int, default=0,
                            help='حداکثر تعداد فایل (۰ = بدون محدودیت)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if not dry_run and not getattr(settings, 'USE_S3', False):
            raise CommandError(
                'USE_S3 فعال نیست. بدون آن storage پیش‌فرض همان دیسک محلی است '
                'و این دستور کاری انجام نمی‌دهد.'
            )

        local = FileSystemStorage(location=settings.MEDIA_ROOT)

        queryset = UploadFile.objects.exclude(file='').exclude(file__isnull=True)
        if options['limit']:
            queryset = queryset[:options['limit']]

        moved = skipped = missing = failed = 0
        total_bytes = 0

        for upload in queryset:
            name = upload.file.name

            if not local.exists(name):
                self.stdout.write(self.style.WARNING(f'[missing] {name}'))
                missing += 1
                continue

            size = local.size(name)

            if not dry_run and default_storage.exists(name):
                # Already moved. The size is compared so a partial transfer is not mistaken
                # for a finished one and skipped.
                if default_storage.size(name) == size:
                    self.stdout.write(f'[skip] {name} (already on S3)')
                    skipped += 1
                    continue
                self.stdout.write(self.style.WARNING(
                    f'[resend] {name} (size mismatch on S3)'
                ))
                default_storage.delete(name)

            if dry_run:
                self.stdout.write(f'[would move] {name} ({size:,} bytes)')
                moved += 1
                total_bytes += size
                continue

            try:
                with local.open(name, 'rb') as handle:
                    saved_name = default_storage.save(name, File(handle))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'[failed] {name}: {exc}'))
                failed += 1
                continue

            # If the bucket refuses a duplicate name the object may be renamed, and the
            # database row then has to point at the new name or the file is lost.
            if saved_name != name:
                UploadFile.objects.filter(pk=upload.pk).update(file=saved_name)
                self.stdout.write(self.style.WARNING(
                    f'[renamed] {name} -> {saved_name}'
                ))

            if default_storage.size(saved_name) != size:
                self.stdout.write(self.style.ERROR(
                    f'[failed] {name}: size mismatch after upload'
                ))
                failed += 1
                continue

            self.stdout.write(f'[moved] {name} ({size:,} bytes)')
            moved += 1
            total_bytes += size

            if options['delete_local']:
                local.delete(name)

        self.stdout.write('')
        self.stdout.write(
            f'moved={moved} skipped={skipped} missing={missing} failed={failed} '
            f'bytes={total_bytes:,}'
        )
        if dry_run:
            self.stdout.write(self.style.WARNING('dry-run — هیچ فایلی منتقل نشد'))
        elif failed:
            raise CommandError(f'{failed} فایل منتقل نشد')
        else:
            self.stdout.write(self.style.SUCCESS('انتقال کامل شد'))
            if not options['delete_local']:
                self.stdout.write(
                    'نسخه‌های محلی دست‌نخورده ماندند. بعد از اطمینان، دوباره با '
                    '--delete-local اجرا کن.'
                )
