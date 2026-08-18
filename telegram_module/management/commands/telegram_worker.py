"""Move queued uploads into their channels.

Run it from cron with --once, or leave it running with a loop interval. There
is no Celery here on purpose: one table and one command are enough for a queue
this shape, and they need no broker to keep alive.

Each file is pushed with its own owner's bot token, so one user hitting
Telegram's rate limit slows nobody else down.
"""
import time

from django.core.management.base import BaseCommand

from telegram_module import feature, storage


class Command(BaseCommand):
    help = 'Transfer queued uploads to their Telegram channels'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true',
                            help='handle the current queue and exit')
        parser.add_argument('--interval', type=int, default=15,
                            help='seconds between passes when looping')
        parser.add_argument('--limit', type=int, default=5,
                            help='files per pass')

    def handle(self, *args, **options):
        if not feature.is_enabled():
            self.stdout.write('Telegram storage is switched off; nothing to do.')
            return

        while True:
            moved, failed = self.pass_once(options['limit'])
            if moved or failed:
                self.stdout.write(f'{moved} منتقل شد، {failed} ناموفق')

            if options['once']:
                return
            time.sleep(options['interval'])

    def pass_once(self, limit):
        moved = failed = 0
        # Oldest first, so a queue that is falling behind still drains in the
        # order people uploaded rather than newest wins.
        for telegram_file in storage.pending_queryset().order_by('created_at')[:limit]:
            if storage.transfer(telegram_file):
                moved += 1
                self.stdout.write(
                    f'  {telegram_file.upload_file_id}: '
                    f'{telegram_file.total_bytes} bytes stored'
                )
            else:
                failed += 1
                telegram_file.refresh_from_db()
                self.stderr.write(
                    f'  {telegram_file.upload_file_id}: {telegram_file.last_error}'
                )
        return moved, failed
