"""Every CharField must be able to hold what the code puts in it.

SQLite ignores a column's declared width and Postgres enforces it, so a value
that is too long works in development and raises DataError in production. These
tests exercise the paths that write user-controlled text into a bounded column.
"""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from upload_module.models import UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()

#: Longer than any column in the project.
OVERLONG = 'ط' * 400


def field_width(model, name):
    return model._meta.get_field(name).max_length


@override_settings(MEDIA_ROOT=MEDIA_ROOT, FREE_STORAGE_BYTES=10 ** 9)
class UploadFieldWidthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='widths@example.com', email='widths@example.com',
            password='pw-for-testing-1',
        )

    def test_a_very_long_upload_filename_is_trimmed(self):
        from django.urls import reverse

        self.client.force_login(self.user)
        name = OVERLONG[:200] + '.zip'
        self.client.post(reverse('upload_page'), {
            'name': '', 'description': '',
            'visibility': UploadFile.Visibility.PRIVATE,
            'file': SimpleUploadedFile(name, b'x' * 10),
        })

        upload = UploadFile.objects.order_by('-id').first()
        self.assertIsNotNone(upload)
        self.assertLessEqual(
            len(upload.original_filename),
            field_width(UploadFile, 'original_filename'),
        )

    def test_the_api_trims_it_too(self):
        from api_module.models import ApiKey

        _key, raw = ApiKey.generate(self.user, 'widths')
        name = OVERLONG[:200] + '.bin'
        response = self.client.post(
            '/api/v1/files/',
            {'file': SimpleUploadedFile(name, b'x' * 10)},
            HTTP_AUTHORIZATION=f'Bearer {raw}',
        )

        self.assertEqual(response.status_code, 201)
        upload = UploadFile.objects.order_by('-id').first()
        self.assertLessEqual(len(upload.name), field_width(UploadFile, 'name'))
        self.assertLessEqual(
            len(upload.original_filename),
            field_width(UploadFile, 'original_filename'),
        )


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class TelegramFieldWidthTests(TestCase):
    """Values that arrive from Telegram, which sets no limits of its own."""

    def setUp(self):
        from django.core.cache import cache

        cache.delete('telegram:enabled')
        self.user = User.objects.create_user(
            username='tgwidths@example.com', email='tgwidths@example.com',
            password='pw-for-testing-1',
        )

    def test_a_long_channel_title_is_trimmed(self):
        from unittest.mock import patch

        from telegram_module import linking
        from telegram_module.models import TelegramConnection

        connection = TelegramConnection(user=self.user, bot_id=42,
                                        bot_username='b')
        connection.set_token('123456789:' + 'A' * 35)
        connection.save()

        with patch('telegram_module.linking.bot_can_post',
                   return_value=(True, '')), \
             patch('telegram_module.linking.api.send_message'):
            linking._claim(connection, 'tok', -100999, OVERLONG)

        connection.refresh_from_db()
        self.assertLessEqual(
            len(connection.chat_title),
            field_width(TelegramConnection, 'chat_title'),
        )

    def test_a_long_bot_username_is_trimmed(self):
        from unittest.mock import patch

        from telegram_module import linking
        from telegram_module.models import TelegramConnection

        with patch('telegram_module.linking.api.delete_webhook'), \
             patch('telegram_module.linking.api.get_me',
                   return_value={'id': 7, 'is_bot': True,
                                 'username': 'u' * 300}):
            connection = linking.register_bot(self.user, '123456789:' + 'A' * 35)

        self.assertLessEqual(
            len(connection.bot_username),
            field_width(TelegramConnection, 'bot_username'),
        )

    def test_a_long_imported_filename_is_trimmed(self):
        from unittest.mock import patch

        from telegram_module import importing
        from telegram_module.models import TelegramConnection, TelegramFile

        connection = TelegramConnection(
            user=self.user, bot_id=42, bot_username='b',
            chat_id=-1001234567890, status=TelegramConnection.Status.ACTIVE,
        )
        connection.set_token('123456789:' + 'A' * 35)
        connection.save()

        with patch('telegram_module.importing.api.delete_message'), \
             patch('telegram_module.importing.api.call', return_value={
                 'message_id': 9,
                 'document': {'file_id': 'f', 'file_unique_id': 'u',
                              'file_name': OVERLONG, 'file_size': 10},
             }):
            upload = importing.import_post(
                self.user, connection, 'https://t.me/c/1234567890/42',
            )

        telegram_file = TelegramFile.objects.get(upload_file=upload)
        self.assertLessEqual(len(upload.name), field_width(UploadFile, 'name'))
        self.assertLessEqual(
            len(telegram_file.original_name),
            field_width(TelegramFile, 'original_name'),
        )


class ErrorTextWidthTests(TestCase):
    """Error text comes from Telegram and can be any length."""

    def test_a_long_connection_error_is_trimmed(self):
        from telegram_module.models import TelegramConnection

        user = User.objects.create_user(
            username='err@example.com', email='err@example.com',
            password='pw-for-testing-1',
        )
        connection = TelegramConnection.objects.create(user=user)
        connection.mark_broken('x' * 5000)

        connection.refresh_from_db()
        # TextField has no width, but the slice keeps one runaway message from
        # filling a row.
        self.assertLessEqual(len(connection.last_error), 2000)
