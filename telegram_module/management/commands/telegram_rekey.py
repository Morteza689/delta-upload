"""Re-encrypt stored bot tokens under a new key.

Needed whenever TELEGRAM_TOKEN_KEY changes, including the first time one is
set: until then the key is derived from SECRET_KEY, so introducing an explicit
one makes every stored token unreadable and disconnects every user at once.

Run it with the old key still available:

    manage.py telegram_rekey --from-secret-key
    manage.py telegram_rekey --old-key <previous TELEGRAM_TOKEN_KEY>

Nothing is written until every token has been decrypted, so a wrong old key
leaves the table exactly as it was rather than half converted.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from telegram_module import crypto
from telegram_module.models import TelegramConnection


class Command(BaseCommand):
    help = 'Re-encrypt stored bot tokens under the current TELEGRAM_TOKEN_KEY'

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            '--old-key',
            help='the previous TELEGRAM_TOKEN_KEY',
        )
        group.add_argument(
            '--from-secret-key', action='store_true',
            help='tokens were encrypted with the key derived from SECRET_KEY',
        )
        parser.add_argument('--dry-run', action='store_true',
                            help='report what would change and write nothing')

    def handle(self, *args, **options):
        rows = list(
            TelegramConnection.objects.exclude(bot_token_encrypted='')
        )
        if not rows:
            self.stdout.write('هیچ توکن ذخیره‌شده‌ای نیست.')
            return

        old_key = self._old_key(options)
        new_key = crypto.current_key()
        if old_key == new_key:
            self.stdout.write('کلید عوض نشده؛ کاری لازم نیست.')
            return

        # Decrypt everything first. A key that turns out to be wrong then costs
        # nothing, instead of leaving half the table under each key.
        plain = {}
        failures = []
        for connection in rows:
            try:
                plain[connection.pk] = crypto.decrypt_with(
                    connection.bot_token_encrypted, old_key,
                )
            except crypto.DecryptionFailed:
                failures.append(connection)

        if failures:
            names = ', '.join(str(c.user) for c in failures[:5])
            raise CommandError(
                f'{len(failures)} توکن با کلید قدیمی باز نشد ({names}). '
                'هیچ چیزی نوشته نشد.'
            )

        if options['dry_run']:
            self.stdout.write(f'{len(plain)} توکن قابل تبدیل است (dry-run).')
            return

        with transaction.atomic():
            for connection in rows:
                connection.bot_token_encrypted = crypto.encrypt_with(
                    plain[connection.pk], new_key,
                )
                connection.save(update_fields=['bot_token_encrypted'])

        self.stdout.write(self.style.SUCCESS(
            f'{len(plain)} توکن با کلید تازه رمزگذاری شد.'
        ))

    def _old_key(self, options):
        if options['from_secret_key']:
            return crypto.secret_key_derived()
        return crypto.normalise_key(options['old_key'])
