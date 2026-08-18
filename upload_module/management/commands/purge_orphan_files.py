"""Delete stored files that no database row points at.

Several paths leave bytes behind on purpose rather than failing the operation
that produced them:

  - a Telegram transfer finishes, clears the file field, and the delete of the
    local copy fails
  - a delete removes the row's bytes but the storage call raises
  - an upload is written and the request dies before the row is saved

Each of those is the right call in the moment; none of them is a reason to
leave the disk filling up forever. This is the sweep that closes them out.

Deliberately conservative. It only considers files older than a grace period,
because an upload in flight has bytes on disk before its row exists, and it
prints what it would remove unless told to act.
"""
from datetime import timedelta

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.utils import timezone

from upload_module.models import UploadFile

#: Where uploads land. Matches the model's upload_to.
UPLOAD_DIR = 'dl/files'

#: Younger than this and it may be an upload that has not finished saving.
DEFAULT_GRACE_HOURS = 24


class Command(BaseCommand):
    help = 'Remove stored files with no database row pointing at them'

    def add_arguments(self, parser):
        parser.add_argument(
            '--delete', action='store_true',
            help='actually remove them; without this it only reports',
        )
        parser.add_argument(
            '--grace-hours', type=int, default=DEFAULT_GRACE_HOURS,
            help='leave files younger than this alone',
        )

    def handle(self, *args, **options):
        try:
            _dirs, names = default_storage.listdir(UPLOAD_DIR)
        except (OSError, NotImplementedError) as exc:
            self.stderr.write(f'could not list {UPLOAD_DIR}: {exc}')
            return

        # One query, not one per file. A few thousand uploads is a small set of
        # strings and a much better trade than a round trip each.
        known = set(
            UploadFile.objects.exclude(file='')
            .values_list('file', flat=True)
        )

        cutoff = timezone.now() - timedelta(hours=options['grace_hours'])
        orphans, freed, skipped = [], 0, 0

        for name in names:
            path = f'{UPLOAD_DIR}/{name}'
            if path in known:
                continue

            try:
                modified = default_storage.get_modified_time(path)
                size = default_storage.size(path)
            except (OSError, NotImplementedError):
                continue

            if timezone.is_naive(modified):
                modified = timezone.make_aware(modified)
            if modified > cutoff:
                # Probably an upload still being written.
                skipped += 1
                continue

            orphans.append((path, size))
            freed += size

        if not orphans:
            self.stdout.write(
                f'هیچ فایل یتیمی نیست ({skipped} فایل تازه نادیده گرفته شد).'
            )
            return

        for path, size in sorted(orphans, key=lambda row: -row[1])[:20]:
            self.stdout.write(f'  {size / 1024 / 1024:>9.1f} MB  {path}')
        if len(orphans) > 20:
            self.stdout.write(f'  ... و {len(orphans) - 20} فایل دیگر')

        summary = (f'{len(orphans)} فایل، {freed / 1024 / 1024:.1f} مگابایت'
                   f' ({skipped} فایل تازه نادیده گرفته شد)')

        if not options['delete']:
            self.stdout.write(f'{summary} — چیزی حذف نشد. با --delete اجرا کن.')
            return

        removed = 0
        for path, _size in orphans:
            try:
                default_storage.delete(path)
                removed += 1
            except OSError as exc:
                self.stderr.write(f'  {path}: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'{removed} از {len(orphans)} فایل حذف شد، '
            f'{freed / 1024 / 1024:.1f} مگابایت آزاد شد'
        ))
