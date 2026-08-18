"""Compile .po files to .mo without the gettext binaries.

Django's compilemessages needs msgfmt, which is usually missing on Windows.
This does the same job with polib so translations can be built anywhere.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'کامپایل فایل‌های .po به .mo با polib (بدون نیاز به gettext)'

    def handle(self, *args, **options):
        try:
            import polib
        except ImportError:
            raise CommandError(
                'polib نصب نیست. اجرا کن: pip install polib'
            )

        locale_paths = list(getattr(settings, 'LOCALE_PATHS', []))
        if not locale_paths:
            raise CommandError('LOCALE_PATHS در تنظیمات خالی است')

        compiled = 0
        for locale_path in locale_paths:
            for po_path in sorted(Path(locale_path).rglob('*.po')):
                mo_path = po_path.with_suffix('.mo')
                catalog = polib.pofile(str(po_path))
                catalog.save_as_mofile(str(mo_path))

                translated = len(catalog.translated_entries())
                total = len([e for e in catalog if not e.obsolete])
                self.stdout.write(
                    f'{po_path.parent.parent.name}: {translated}/{total} '
                    f'-> {mo_path.name}'
                )
                compiled += 1

        if not compiled:
            self.stdout.write(self.style.WARNING('هیچ فایل .po پیدا نشد'))
        else:
            self.stdout.write(self.style.SUCCESS(f'{compiled} کاتالوگ کامپایل شد'))
