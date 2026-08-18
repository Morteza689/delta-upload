"""Telegram storage, where each user brings their own bot.

Nothing here touches the network. Every Bot API call is patched, because a test
suite that needs a live bot is a test suite nobody runs.
"""
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache as django_cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from upload_module.models import UploadFile

from . import api, cache, crypto, feature, importing, linking, quota, storage
from .models import (
    TelegramChunk, TelegramConnection, TelegramFile, TelegramSetting,
)

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()

#: The shape register_bot insists on before spending a call on it.
TOKEN = '123456789:AAEabcdefghijklmnopqrstuvwxyz0123456'
OTHER_TOKEN = '987654321:BBEabcdefghijklmnopqrstuvwxyz0123456'


def make_user(email='u@example.com'):
    return User.objects.create_user(
        username=email, email=email, password='pw-for-testing-1',
    )


def make_connection(user, chat_id=-100123,
                    status=TelegramConnection.Status.ACTIVE, token=TOKEN,
                    bot_id=555):
    connection = TelegramConnection(
        user=user, chat_id=chat_id, chat_title='My files',
        status=status, linked_at=timezone.now(),
        bot_username='mybot', bot_id=bot_id,
    )
    if token:
        connection.set_token(token)
    connection.save()
    return connection


def make_file(user, content=b'hello world', name='report.bin'):
    return UploadFile.objects.create(
        name=name, author=user, slug='s' * 12,
        file=SimpleUploadedFile(name, content),
        type_file=UploadFile.ChoiceType.File,
    )


def identity(bot_id=555, username='mybot'):
    return {'id': bot_id, 'is_bot': True, 'username': username}


class TelegramTestCase(TestCase):
    """Clears the cached feature switch around every test.

    The switch is cached process-wide, so one test turning the feature off
    would otherwise leave it off for every test that ran afterwards.
    """

    def setUp(self):
        django_cache.delete('telegram:enabled')
        self.addCleanup(django_cache.delete, 'telegram:enabled')
        super().setUp()


class TokenEncryptionTests(TelegramTestCase):
    def test_a_token_round_trips(self):
        self.assertEqual(crypto.decrypt(crypto.encrypt(TOKEN)), TOKEN)

    def test_the_stored_form_is_not_the_token(self):
        # The whole point: a database dump must not contain usable tokens.
        self.assertNotIn(TOKEN, crypto.encrypt(TOKEN))

    def test_an_empty_value_stays_empty(self):
        self.assertEqual(crypto.encrypt(''), '')
        self.assertEqual(crypto.decrypt(''), '')

    def test_tampering_is_refused_rather_than_ignored(self):
        stored = crypto.encrypt(TOKEN)
        with self.assertRaises(crypto.DecryptionFailed):
            crypto.decrypt(stored[:-4] + 'AAAA')

    def test_a_different_key_cannot_read_it(self):
        with override_settings(TELEGRAM_TOKEN_KEY='key-one'):
            stored = crypto.encrypt(TOKEN)
        with override_settings(TELEGRAM_TOKEN_KEY='key-two'):
            # Loud, not silent: treating this as "no token" would disconnect
            # every user with nothing on screen to explain why.
            with self.assertRaises(crypto.DecryptionFailed):
                crypto.decrypt(stored)

    def test_an_explicit_key_is_reported_as_such(self):
        with override_settings(TELEGRAM_TOKEN_KEY=''):
            self.assertFalse(crypto.key_is_explicit())
        with override_settings(TELEGRAM_TOKEN_KEY='something'):
            self.assertTrue(crypto.key_is_explicit())

    def test_a_generated_key_works(self):
        with override_settings(TELEGRAM_TOKEN_KEY=crypto.generate_key()):
            self.assertEqual(crypto.decrypt(crypto.encrypt(TOKEN)), TOKEN)

    def test_the_connection_never_holds_the_token_in_the_clear(self):
        connection = make_connection(make_user())
        self.assertNotIn(TOKEN, connection.bot_token_encrypted)
        self.assertEqual(connection.token, TOKEN)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class FeatureSwitchTests(TelegramTestCase):
    def test_it_is_on_by_default(self):
        self.assertTrue(feature.is_enabled())

    def test_the_database_switch_turns_it_off(self):
        setting = TelegramSetting.load()
        setting.is_enabled = False
        setting.save()
        self.assertFalse(feature.is_enabled())

    @override_settings(TELEGRAM_STORAGE_ENABLED=False)
    def test_the_environment_can_force_it_off(self):
        # A hard off switch the database cannot override.
        TelegramSetting.load()
        self.assertFalse(feature.is_enabled())

    def test_the_page_closes_when_the_feature_is_off(self):
        setting = TelegramSetting.load()
        setting.is_enabled = False
        setting.save()

        self.client.force_login(make_user())
        response = self.client.get(reverse('telegram_connect_page'))
        self.assertEqual(response.status_code, 404)

    def test_the_page_needs_a_login(self):
        response = self.client.get(reverse('telegram_connect_page'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class RegisterBotTests(TelegramTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()

    @patch('telegram_module.linking.api.delete_webhook')
    @patch('telegram_module.linking.api.get_me', return_value=identity())
    def test_a_good_token_is_stored_encrypted(self, _me, _hook):
        connection = linking.register_bot(self.user, TOKEN)

        self.assertEqual(connection.token, TOKEN)
        self.assertNotIn(TOKEN, connection.bot_token_encrypted)
        self.assertEqual(connection.bot_username, 'mybot')
        self.assertEqual(connection.status, TelegramConnection.Status.PENDING)

    @patch('telegram_module.linking.api.get_me')
    def test_a_token_shaped_wrong_costs_no_round_trip(self, get_me):
        for bad in ('', '@mybot', 'nonsense', '123:short'):
            with self.assertRaises(linking.LinkError):
                linking.register_bot(self.user, bad)
        get_me.assert_not_called()

    @patch('telegram_module.linking.api.get_me')
    def test_a_token_telegram_rejects_is_not_stored(self, get_me):
        get_me.side_effect = api.TelegramError('Unauthorized')
        with self.assertRaises(linking.LinkError):
            linking.register_bot(self.user, TOKEN)
        self.assertFalse(
            TelegramConnection.objects.filter(user=self.user)
            .exclude(bot_token_encrypted='').exists()
        )

    @patch('telegram_module.linking.api.get_me',
           return_value={'id': 1, 'is_bot': False})
    def test_a_token_that_is_not_a_bot_is_refused(self, _me):
        with self.assertRaises(linking.LinkError):
            linking.register_bot(self.user, TOKEN)

    @patch('telegram_module.linking.api.delete_webhook')
    @patch('telegram_module.linking.api.get_me', return_value=identity())
    def test_one_bot_cannot_serve_two_accounts(self, _me, _hook):
        linking.register_bot(self.user, TOKEN)

        with self.assertRaises(linking.LinkError):
            # A bot sees every chat it is in, so sharing one would let either
            # account reach the other's files.
            linking.register_bot(make_user('other@example.com'), OTHER_TOKEN)

    @patch('telegram_module.linking.api.delete_webhook')
    @patch('telegram_module.linking.api.get_me', return_value=identity())
    def test_any_webhook_on_the_bot_is_cleared(self, _me, delete_webhook):
        linking.register_bot(self.user, TOKEN)
        # Telegram refuses getUpdates while a webhook is set, and the user has
        # no way to guess that from the error.
        delete_webhook.assert_called_once_with(TOKEN)

    def test_forgetting_the_bot_keeps_the_file_records(self):
        connection = make_connection(self.user)
        TelegramFile.objects.create(
            upload_file=make_file(self.user), connection=connection,
            total_bytes=5, status=TelegramFile.Status.STORED,
        )

        linking.forget_bot(self.user)

        connection.refresh_from_db()
        self.assertFalse(connection.has_bot())
        self.assertEqual(connection.status, TelegramConnection.Status.NO_BOT)
        # The bytes are still in their channel, so the rows describing where
        # must survive or coming back would be useless.
        self.assertEqual(TelegramFile.objects.count(), 1)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class FindChannelTests(TelegramTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.connection = make_connection(
            self.user, chat_id=None, status=TelegramConnection.Status.PENDING,
        )

    def _updates(self, code, chat_id=-100999, title='Backup'):
        return [{
            'update_id': 1,
            'channel_post': {
                'message_id': 3, 'text': code,
                'chat': {'id': chat_id, 'title': title},
            },
        }]

    @patch('telegram_module.linking.api.send_message')
    @patch('telegram_module.linking.bot_can_post', return_value=(True, ''))
    @patch('telegram_module.linking.api.get_updates')
    def test_the_code_in_a_channel_links_it(self, get_updates, _can, _send):
        get_updates.return_value = self._updates(self.connection.link_code)

        connection = linking.find_channel(self.user)
        self.assertEqual(connection.chat_id, -100999)
        self.assertTrue(connection.is_usable())

    @patch('telegram_module.linking.api.send_message')
    @patch('telegram_module.linking.bot_can_post', return_value=(True, ''))
    @patch('telegram_module.linking.api.get_updates')
    def test_the_code_is_replaced_once_used(self, get_updates, _can, _send):
        code = self.connection.link_code
        get_updates.return_value = self._updates(code)

        connection = linking.find_channel(self.user)
        # Somebody who saw the code afterwards must not be able to replay it.
        self.assertNotEqual(connection.link_code, code)

    @patch('telegram_module.linking.api.get_updates', return_value=[])
    def test_no_matching_post_says_so(self, _updates):
        with self.assertRaises(linking.LinkError):
            linking.find_channel(self.user)
        self.connection.refresh_from_db()
        self.assertIsNone(self.connection.chat_id)

    @patch('telegram_module.linking.bot_can_post', return_value=(True, ''))
    @patch('telegram_module.linking.api.get_updates')
    def test_another_users_code_is_ignored(self, get_updates, _can):
        other = make_connection(
            make_user('other@example.com'), chat_id=None,
            status=TelegramConnection.Status.PENDING, bot_id=777,
        )
        get_updates.return_value = self._updates(other.link_code)

        with self.assertRaises(linking.LinkError):
            linking.find_channel(self.user)

    @patch('telegram_module.linking.bot_can_post',
           return_value=(False, 'ربات باید ادمین کانال باشد.'))
    @patch('telegram_module.linking.api.get_updates')
    def test_a_bot_that_cannot_post_does_not_link(self, get_updates, _can):
        get_updates.return_value = self._updates(self.connection.link_code)
        with self.assertRaises(linking.LinkError):
            linking.find_channel(self.user)

    @patch('telegram_module.linking.api.send_message')
    @patch('telegram_module.linking.bot_can_post', return_value=(True, ''))
    @patch('telegram_module.linking.api.get_updates')
    def test_one_channel_cannot_serve_two_accounts(self, get_updates, _can, _s):
        get_updates.return_value = self._updates(self.connection.link_code,
                                                 chat_id=-100777)
        linking.find_channel(self.user)

        other_user = make_user('other@example.com')
        other = make_connection(other_user, chat_id=None, bot_id=888,
                                status=TelegramConnection.Status.PENDING)
        get_updates.return_value = self._updates(other.link_code,
                                                 chat_id=-100777)

        # Either account could delete the other's files.
        with self.assertRaises(linking.LinkError):
            linking.find_channel(other_user)

    def test_a_user_without_a_bot_is_told_to_start_there(self):
        with self.assertRaises(linking.LinkError):
            linking.find_channel(make_user('nobot@example.com'))

    @patch('telegram_module.linking.api.get_updates')
    def test_an_unreadable_token_is_reported_not_swallowed(self, get_updates):
        self.connection.bot_token_encrypted = 'not-decryptable'
        self.connection.save()

        with self.assertRaises(linking.LinkError):
            linking.find_channel(self.user)
        get_updates.assert_not_called()


@override_settings(MEDIA_ROOT=MEDIA_ROOT, TELEGRAM_CHUNK_BYTES=4)
class TransferTests(TelegramTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.connection = make_connection(self.user)

    def _fake_send(self):
        """A sendDocument that hands back plausible message ids."""
        state = {'n': 0}

        def send(token, chat_id, filename, stream, caption=None):
            state['n'] += 1
            return {
                'message_id': 1000 + state['n'],
                'document': {
                    'file_id': f'fid-{state["n"]}',
                    'file_unique_id': f'uid-{state["n"]}',
                },
            }
        return send

    def test_queueing_flips_the_backend_immediately(self):
        upload = make_file(self.user)
        storage.queue(upload, self.connection)
        upload.refresh_from_db()

        # The quota has to stop counting it now, not when the worker runs.
        self.assertTrue(upload.on_telegram())

    @patch('telegram_module.storage.api.send_document')
    def test_a_file_is_split_into_parts(self, send):
        send.side_effect = self._fake_send()
        upload = make_file(self.user, b'0123456789')
        telegram_file = storage.queue(upload, self.connection)

        self.assertTrue(storage.transfer(telegram_file))
        telegram_file.refresh_from_db()

        # Ten bytes at four bytes a part.
        self.assertEqual(telegram_file.chunks.count(), 3)
        self.assertEqual(
            [c.size_bytes for c in telegram_file.chunks.all()], [4, 4, 2],
        )
        self.assertEqual(telegram_file.status, TelegramFile.Status.STORED)

    @patch('telegram_module.storage.api.send_document')
    def test_each_part_is_sent_with_the_owners_token(self, send):
        send.side_effect = self._fake_send()
        storage.transfer(storage.queue(make_file(self.user, b'0123'),
                                       self.connection))

        # Not a site-wide credential: whose bot uploaded this matters.
        self.assertEqual(send.call_args[0][0], TOKEN)

    @patch('telegram_module.storage.api.send_document')
    def test_the_local_copy_is_released_after_transfer(self, send):
        send.side_effect = self._fake_send()
        upload = make_file(self.user, b'0123456789')
        storage.transfer(storage.queue(upload, self.connection))

        upload.refresh_from_db()
        self.assertFalse(upload.file)
        # The row still knows how big it was, which the download needs.
        self.assertEqual(upload.size_bytes_cached, 10)
        self.assertTrue(upload.has_content())

    @patch('telegram_module.storage.api.send_document')
    def test_a_retry_resumes_instead_of_re_sending(self, send):
        calls = self._fake_send()
        failures = {'left': 1}

        def flaky(token, chat_id, filename, stream, caption=None):
            if failures['left'] and filename.endswith('part0002'):
                failures['left'] -= 1
                raise api.TelegramError('temporary trouble')
            return calls(token, chat_id, filename, stream, caption)

        send.side_effect = flaky
        upload = make_file(self.user, b'0123456789')
        telegram_file = storage.queue(upload, self.connection)

        self.assertFalse(storage.transfer(telegram_file))
        self.assertEqual(telegram_file.chunks.count(), 2)

        # The second pass must not re-send the two parts that arrived.
        self.assertTrue(storage.transfer(telegram_file))
        self.assertEqual(
            sorted(c.index for c in telegram_file.chunks.all()), [0, 1, 2],
        )

    @patch('telegram_module.storage.api.send_document')
    def test_losing_the_channel_marks_the_connection_broken(self, send):
        send.side_effect = api.TelegramError('Bad Request: chat not found')
        telegram_file = storage.queue(make_file(self.user), self.connection)

        self.assertFalse(storage.transfer(telegram_file))
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, TelegramConnection.Status.BROKEN)

    @patch('telegram_module.storage.api.send_document')
    def test_it_gives_up_rather_than_retrying_forever(self, send):
        send.side_effect = api.TelegramError('some other failure')
        telegram_file = storage.queue(make_file(self.user), self.connection)

        for _ in range(storage.MAX_ATTEMPTS):
            telegram_file.refresh_from_db()
            storage.transfer(telegram_file)

        telegram_file.refresh_from_db()
        self.assertEqual(telegram_file.status, TelegramFile.Status.FAILED)
        self.assertNotIn(telegram_file, storage.pending_queryset())

    def test_a_disconnected_channel_is_not_attempted(self):
        self.connection.status = TelegramConnection.Status.BROKEN
        self.connection.save()
        telegram_file = storage.queue(make_file(self.user), self.connection)
        self.assertFalse(storage.transfer(telegram_file))

    @patch('telegram_module.storage.api.send_document')
    def test_an_unreadable_token_fails_the_transfer_loudly(self, send):
        self.connection.bot_token_encrypted = 'not-decryptable'
        self.connection.save()
        telegram_file = storage.queue(make_file(self.user), self.connection)

        self.assertFalse(storage.transfer(telegram_file))
        send.assert_not_called()
        telegram_file.refresh_from_db()
        self.assertIn('decrypt', telegram_file.last_error)


class FakeResponse:
    """The slice of requests.Response that storage uses."""

    def __init__(self, body):
        self.body = body

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]

    def close(self):
        pass


@override_settings(MEDIA_ROOT=MEDIA_ROOT, TELEGRAM_CHUNK_BYTES=4,
                   TELEGRAM_CACHE_BYTES=0)
class StreamTests(TelegramTestCase):
    """Reading a stored file back, with the cache switched off."""

    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.connection = make_connection(self.user)
        self.content = b'ABCDEFGHIJ'
        self.upload = make_file(self.user, self.content)
        self.telegram_file = TelegramFile.objects.create(
            upload_file=self.upload, connection=self.connection,
            total_bytes=len(self.content), chunk_bytes=4,
            status=TelegramFile.Status.STORED,
        )
        for index, start in enumerate(range(0, len(self.content), 4)):
            TelegramChunk.objects.create(
                telegram_file=self.telegram_file, index=index,
                size_bytes=len(self.content[start:start + 4]),
                message_id=1000 + index, file_id=f'fid-{index}',
                file_unique_id=f'uid-{index}',
            )
        UploadFile.objects.filter(pk=self.upload.pk).update(
            storage_backend=UploadFile.StorageBackend.TELEGRAM,
            file='', size_bytes_cached=len(self.content),
        )
        self.upload.refresh_from_db()

    def _serve(self):
        """Patch getFile and the download host to serve from self.content."""
        def open_file(token, path, timeout=None):
            index = int(path.split('-')[-1])
            start = index * 4
            return FakeResponse(self.content[start:start + 4])

        return (
            patch('telegram_module.storage.api.get_file_path',
                  side_effect=lambda token, fid: f'path-{fid.split("-")[-1]}'),
            patch('telegram_module.storage.api.open_file', side_effect=open_file),
        )

    def test_the_whole_file_comes_back_byte_for_byte(self):
        first, second = self._serve()
        with first, second:
            self.assertEqual(b''.join(storage.stream(self.upload)), self.content)

    def test_a_range_spanning_two_parts(self):
        first, second = self._serve()
        with first, second:
            got = b''.join(storage.stream(self.upload, 2, 6))
        self.assertEqual(got, self.content[2:7])

    def test_a_range_inside_one_part(self):
        first, second = self._serve()
        with first, second:
            got = b''.join(storage.stream(self.upload, 5, 6))
        self.assertEqual(got, self.content[5:7])

    def test_a_range_at_the_very_end(self):
        first, second = self._serve()
        with first, second:
            got = b''.join(storage.stream(self.upload, 9, 9))
        self.assertEqual(got, self.content[9:10])

    def test_a_file_with_no_parts_refuses_rather_than_serving_nothing(self):
        self.telegram_file.chunks.all().delete()
        with self.assertRaises(storage.NotStored):
            list(storage.stream(self.upload))

    def test_a_file_still_in_the_queue_is_not_streamable(self):
        TelegramFile.objects.filter(pk=self.telegram_file.pk).update(
            status=TelegramFile.Status.PENDING,
        )
        with self.assertRaises(storage.NotStored):
            list(storage.stream(self.upload))

    def test_an_owner_without_a_bot_cannot_serve_their_files(self):
        self.connection.bot_token_encrypted = ''
        self.connection.save()
        with self.assertRaises(storage.NotStored):
            list(storage.stream(self.upload))


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class StagingCapTests(TelegramTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.connection = make_connection(self.user)

    @override_settings(TELEGRAM_STAGING_BYTES=100)
    def test_a_file_that_fits_is_accepted(self):
        quota.check_queue(self.user, 50)

    @override_settings(TELEGRAM_STAGING_BYTES=100)
    def test_the_queue_cannot_be_used_to_dodge_the_quota(self):
        TelegramFile.objects.create(
            upload_file=make_file(self.user), connection=self.connection,
            total_bytes=90, status=TelegramFile.Status.PENDING,
        )
        with self.assertRaises(quota.StagingFull):
            quota.check_queue(self.user, 50)

    @override_settings(TELEGRAM_STAGING_BYTES=100)
    def test_a_stored_file_leaves_the_queue(self):
        TelegramFile.objects.create(
            upload_file=make_file(self.user), connection=self.connection,
            total_bytes=90, status=TelegramFile.Status.STORED,
        )
        # It is in Telegram now and costs us nothing, so it must not hold a
        # slot in the queue.
        quota.check_queue(self.user, 50)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class QuotaInteractionTests(TelegramTestCase):
    @override_settings(FREE_STORAGE_BYTES=1000)
    def test_telegram_files_do_not_use_our_quota(self):
        from account_module import quota as storage_quota

        user = make_user()
        storage.queue(make_file(user, b'x' * 500), make_connection(user))

        self.assertEqual(storage_quota.used_by(user), 0)
        self.assertEqual(storage_quota.stored_on_telegram(user), 500)

    @override_settings(FREE_STORAGE_BYTES=1000)
    def test_ordinary_files_still_do(self):
        from account_module import quota as storage_quota

        user = make_user()
        make_file(user, b'x' * 500)
        self.assertEqual(storage_quota.used_by(user), 500)


class CacheTests(TelegramTestCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)

    def _settings(self, limit=1000):
        return override_settings(
            TELEGRAM_CACHE_DIR=self.directory, TELEGRAM_CACHE_BYTES=limit,
        )

    def test_a_miss_reads_as_none(self):
        with self._settings():
            self.assertIsNone(cache.read('nothing', 0, 10))

    def test_a_complete_part_round_trips(self):
        with self._settings():
            writer = cache.writer('k', 5)
            writer.write(b'abcde')
            writer.close(complete=True)

            self.assertEqual(b''.join(cache.read('k', 0, 5)), b'abcde')
            self.assertEqual(b''.join(cache.read('k', 1, 3)), b'bc')

    def test_a_truncated_part_is_thrown_away(self):
        with self._settings():
            writer = cache.writer('k', 10)
            writer.write(b'abc')
            writer.close(complete=False)

            # Serving three bytes as if they were ten would corrupt a download
            # silently, which is worse than fetching it again.
            self.assertIsNone(cache.read('k', 0, 10))

    def test_a_part_shorter_than_promised_is_thrown_away(self):
        with self._settings():
            writer = cache.writer('k', 10)
            writer.write(b'abc')
            writer.close(complete=True)
            self.assertIsNone(cache.read('k', 0, 10))

    def test_the_cache_stays_under_its_limit(self):
        with self._settings(limit=10):
            for key in ('a', 'b', 'c'):
                writer = cache.writer(key, 6)
                writer.write(b'x' * 6)
                writer.close(complete=True)

            self.assertLessEqual(cache.stats()['bytes'], 10)

    def test_a_zero_limit_disables_it(self):
        with self._settings(limit=0):
            writer = cache.writer('k', 3)
            writer.write(b'abc')
            writer.close(complete=True)
            self.assertIsNone(cache.read('k', 0, 3))


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ConnectPageTests(TelegramTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse('telegram_connect_page')

    def test_step_one_asks_for_a_token(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'name="bot_token"')
        self.assertContains(response, 'BotFather')

    def test_the_link_code_is_not_shown_before_a_bot_exists(self):
        response = self.client.get(self.url)
        connection = TelegramConnection.objects.get(user=self.user)
        # Showing it at step one would have people posting a code into a
        # channel no bot can read.
        self.assertNotContains(response, connection.link_code)

    def test_step_two_shows_the_code(self):
        connection = make_connection(
            self.user, chat_id=None, status=TelegramConnection.Status.PENDING,
        )
        response = self.client.get(self.url)
        self.assertContains(response, connection.link_code)

    def test_the_risks_are_on_the_page_itself(self):
        response = self.client.get(self.url)
        body = response.content.decode()
        # Not behind a link nobody opens.
        self.assertIn('رمز دو مرحله‌ای', body)
        self.assertIn('از بین می‌رود', body)

    def test_one_user_never_sees_another_users_code(self):
        make_connection(self.user, chat_id=None,
                        status=TelegramConnection.Status.PENDING)
        theirs = linking.connection_for(make_user('other@example.com'))

        response = self.client.get(self.url)
        self.assertNotContains(response, theirs.link_code)

    def test_the_stored_token_is_never_rendered(self):
        make_connection(self.user)
        self.assertNotContains(self.client.get(self.url), TOKEN)

    @patch('telegram_module.linking.api.delete_webhook')
    @patch('telegram_module.linking.api.get_me', return_value=identity())
    def test_posting_a_token_registers_it(self, _me, _hook):
        response = self.client.post(self.url, {
            'action': 'register_bot', 'bot_token': TOKEN,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            TelegramConnection.objects.get(user=self.user).token, TOKEN,
        )

    @patch('telegram_module.linking.api.get_me',
           side_effect=api.TelegramError('Unauthorized'))
    def test_a_rejected_token_re_renders_with_the_reason(self, _me):
        response = self.client.post(self.url, {
            'action': 'register_bot', 'bot_token': TOKEN,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Unauthorized')

    @patch('telegram_module.linking.api.send_message')
    def test_disconnecting_keeps_the_file_records(self, _send):
        connection = make_connection(self.user)
        TelegramFile.objects.create(
            upload_file=make_file(self.user), connection=connection,
            total_bytes=5, status=TelegramFile.Status.STORED,
        )

        self.client.post(self.url, {'action': 'disconnect'})

        connection.refresh_from_db()
        self.assertEqual(connection.status, TelegramConnection.Status.BROKEN)
        self.assertEqual(TelegramFile.objects.count(), 1)


@override_settings(MEDIA_ROOT=MEDIA_ROOT, TELEGRAM_CHUNK_BYTES=4)
class UploadChoiceTests(TelegramTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.client.force_login(self.user)

    def _post(self, extra=None):
        data = {
            'name': 'notes', 'description': 'x',
            'visibility': UploadFile.Visibility.PRIVATE,
            'file': SimpleUploadedFile('notes.bin', b'0123456789'),
        }
        data.update(extra or {})
        return self.client.post(reverse('upload_page'), data)

    def test_the_option_is_absent_without_a_connection(self):
        response = self.client.get(reverse('upload_page'))
        self.assertNotContains(response, 'store_on_telegram')

    def test_the_option_appears_once_connected(self):
        make_connection(self.user)
        response = self.client.get(reverse('upload_page'))
        self.assertContains(response, 'store_on_telegram')

    def test_a_broken_connection_does_not_offer_the_option(self):
        make_connection(self.user, status=TelegramConnection.Status.BROKEN)
        response = self.client.get(reverse('upload_page'))
        self.assertNotContains(response, 'store_on_telegram')

    def test_a_connection_without_a_bot_does_not_offer_the_option(self):
        make_connection(self.user, token=None)
        response = self.client.get(reverse('upload_page'))
        self.assertNotContains(response, 'store_on_telegram')

    def test_ticking_the_box_queues_the_transfer(self):
        make_connection(self.user)
        self._post({'store_on_telegram': 'on'})

        upload = UploadFile.objects.get(name='notes')
        self.assertTrue(upload.on_telegram())
        self.assertEqual(TelegramFile.objects.filter(upload_file=upload).count(), 1)

    def test_leaving_it_unticked_stores_with_us(self):
        make_connection(self.user)
        self._post()

        upload = UploadFile.objects.get(name='notes')
        self.assertFalse(upload.on_telegram())
        self.assertFalse(TelegramFile.objects.exists())

    @override_settings(FREE_STORAGE_BYTES=5)
    def test_telegram_uploads_are_not_blocked_by_a_full_quota(self):
        make_connection(self.user)
        response = self._post({'store_on_telegram': 'on'})

        # The whole point: a 1 GB allowance must not stop a file going to the
        # user's own storage.
        self.assertEqual(response.status_code, 302)
        self.assertTrue(UploadFile.objects.get(name='notes').on_telegram())

    @override_settings(FREE_STORAGE_BYTES=5)
    def test_ordinary_uploads_are_still_blocked_by_a_full_quota(self):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UploadFile.objects.filter(name='notes').exists())

    @override_settings(TELEGRAM_STAGING_BYTES=5)
    def test_the_queue_cap_is_enforced_at_upload_time(self):
        make_connection(self.user)
        response = self._post({'store_on_telegram': 'on'})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UploadFile.objects.filter(name='notes').exists())

    def test_someone_elses_connection_cannot_be_used(self):
        make_connection(make_user('other@example.com'), chat_id=-100321,
                        bot_id=999)

        self._post({'store_on_telegram': 'on'})
        # The form looks the connection up by the logged-in user, so a tick
        # without one of your own does nothing.
        self.assertFalse(UploadFile.objects.get(name='notes').on_telegram())


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ImportPostTests(TelegramTestCase):
    """Registering a file the user posted to their channel themselves."""

    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.connection = make_connection(self.user, chat_id=-1001234567890)

    def _document(self, size=1024, name='holiday.zip'):
        return {
            'message_id': 999,
            'document': {
                'file_id': 'fid-abc', 'file_unique_id': 'uid-abc',
                'file_name': name, 'file_size': size,
            },
        }

    def test_a_private_link_resolves_to_the_connected_chat(self):
        chat_id, message_id = importing.parse_link(
            'https://t.me/c/1234567890/42', self.connection,
        )
        self.assertEqual(chat_id, -1001234567890)
        self.assertEqual(message_id, 42)

    def test_a_forum_topic_link_takes_the_last_number(self):
        _chat, message_id = importing.parse_link(
            'https://t.me/c/1234567890/7/42', self.connection,
        )
        self.assertEqual(message_id, 42)

    def test_a_link_to_another_channel_is_refused(self):
        with self.assertRaises(importing.ImportError_):
            importing.parse_link('https://t.me/c/9999999999/42', self.connection)

    def test_nonsense_is_refused(self):
        for bad in ('', 'not a link', 'https://example.com/c/1/2'):
            with self.assertRaises(importing.ImportError_):
                importing.parse_link(bad, self.connection)

    @patch('telegram_module.importing.api.delete_message')
    @patch('telegram_module.importing.api.call')
    def test_a_post_becomes_a_file(self, call, _delete):
        call.return_value = self._document(size=2048)

        upload = importing.import_post(
            self.user, self.connection, 'https://t.me/c/1234567890/42',
        )

        self.assertEqual(upload.name, 'holiday.zip')
        self.assertEqual(upload.size_bytes_cached, 2048)
        self.assertTrue(upload.on_telegram())
        # Private until the owner decides otherwise.
        self.assertEqual(upload.visibility, UploadFile.Visibility.PRIVATE)

        chunk = TelegramChunk.objects.get(telegram_file__upload_file=upload)
        # The original post, not the forwarded copy that was deleted.
        self.assertEqual(chunk.message_id, 42)
        self.assertEqual(chunk.file_id, 'fid-abc')

    @patch('telegram_module.importing.api.delete_message')
    @patch('telegram_module.importing.api.call')
    def test_the_forwarded_copy_is_always_removed(self, call, delete_message):
        call.return_value = self._document()
        importing.import_post(
            self.user, self.connection, 'https://t.me/c/1234567890/42',
        )
        delete_message.assert_called_once_with(
            TOKEN, self.connection.chat_id, 999,
        )

    @patch('telegram_module.importing.api.delete_message')
    @patch('telegram_module.importing.api.call')
    def test_the_copy_is_removed_even_when_the_post_is_rejected(
            self, call, delete_message):
        call.return_value = {'message_id': 999, 'text': 'just a note'}

        with self.assertRaises(importing.ImportError_):
            importing.import_post(
                self.user, self.connection, 'https://t.me/c/1234567890/42',
            )
        # Otherwise every failed attempt litters the channel a little more.
        delete_message.assert_called_once()

    @patch('telegram_module.importing.api.delete_message')
    @patch('telegram_module.importing.api.call')
    def test_a_file_too_large_to_read_back_is_refused(self, call, _delete):
        call.return_value = self._document(size=importing.MAX_IMPORT_BYTES + 1)

        with self.assertRaises(importing.ImportError_) as caught:
            importing.import_post(
                self.user, self.connection, 'https://t.me/c/1234567890/42',
            )
        # Recording it would produce a file that 404s on every download.
        self.assertIn('۲۰ مگابایت', str(caught.exception))
        self.assertFalse(UploadFile.objects.exists())

    @patch('telegram_module.importing.api.delete_message')
    @patch('telegram_module.importing.api.call')
    def test_the_same_post_cannot_be_added_twice(self, call, _delete):
        call.return_value = self._document()
        importing.import_post(
            self.user, self.connection, 'https://t.me/c/1234567890/42',
        )
        with self.assertRaises(importing.ImportError_):
            importing.import_post(
                self.user, self.connection, 'https://t.me/c/1234567890/42',
            )
        self.assertEqual(UploadFile.objects.count(), 1)

    @patch('telegram_module.importing.api.delete_message')
    @patch('telegram_module.importing.api.call')
    def test_a_video_post_works_too(self, call, _delete):
        call.return_value = {
            'message_id': 999,
            'caption': 'family trip',
            'video': {'file_id': 'v', 'file_unique_id': 'vu', 'file_size': 500},
        }
        upload = importing.import_post(
            self.user, self.connection, 'https://t.me/c/1234567890/42',
        )
        # No file_name on a video, so the caption names it.
        self.assertEqual(upload.name, 'family trip')

    def test_a_disconnected_channel_cannot_import(self):
        self.connection.status = TelegramConnection.Status.BROKEN
        self.connection.save()
        with self.assertRaises(importing.ImportError_):
            importing.import_post(
                self.user, self.connection, 'https://t.me/c/1234567890/42',
            )


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ImportViewTests(TelegramTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse('telegram_connect_page')

    def test_the_input_is_hidden_until_the_channel_is_connected(self):
        response = self.client.get(self.url)
        self.assertNotContains(response, 'name="post_link"')

    def test_the_input_appears_once_connected(self):
        make_connection(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'name="post_link"')

    @patch('telegram_module.importing.api.delete_message')
    @patch('telegram_module.importing.api.call')
    def test_pasting_a_link_adds_the_file(self, call, _delete):
        make_connection(self.user, chat_id=-1001234567890)
        call.return_value = {
            'message_id': 999,
            'document': {'file_id': 'f', 'file_unique_id': 'u',
                         'file_name': 'notes.pdf', 'file_size': 10},
        }

        response = self.client.post(self.url, {
            'action': 'import', 'post_link': 'https://t.me/c/1234567890/42',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(UploadFile.objects.filter(name='notes.pdf').exists())

    def test_a_bad_link_keeps_what_was_typed(self):
        make_connection(self.user)
        response = self.client.post(self.url, {
            'action': 'import', 'post_link': 'rubbish',
        })
        self.assertEqual(response.status_code, 200)
        # Retyping a link they had to go and copy is a needless second trip.
        self.assertContains(response, 'rubbish')

    @patch('telegram_module.importing.api.delete_message')
    @patch('telegram_module.importing.api.call')
    def test_an_imported_file_never_lands_on_our_disk(self, call, _delete):
        from account_module import quota as storage_quota

        make_connection(self.user, chat_id=-1001234567890)
        call.return_value = {
            'message_id': 999,
            'document': {'file_id': 'f', 'file_unique_id': 'u',
                         'file_name': 'notes.pdf', 'file_size': 10},
        }
        self.client.post(self.url, {
            'action': 'import', 'post_link': 'https://t.me/c/1234567890/42',
        })

        upload = UploadFile.objects.get(name='notes.pdf')
        self.assertFalse(upload.file)
        # And so it costs nothing against the quota.
        self.assertEqual(storage_quota.used_by(self.user), 0)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class DeletionTests(TelegramTestCase):
    def _stored_file(self, user, connection, message_id=4242):
        upload = make_file(user, b'0123456789')
        telegram_file = TelegramFile.objects.create(
            upload_file=upload, connection=connection, total_bytes=10,
            status=TelegramFile.Status.STORED,
        )
        TelegramChunk.objects.create(
            telegram_file=telegram_file, index=0, size_bytes=10,
            message_id=message_id, file_id='fid', file_unique_id='uid',
        )
        UploadFile.objects.filter(pk=upload.pk).update(
            storage_backend=UploadFile.StorageBackend.TELEGRAM,
        )
        upload.refresh_from_db()
        return upload

    @patch('telegram_module.storage.api.delete_message')
    def test_deleting_a_file_clears_the_channel_too(self, delete_message):
        user = make_user()
        connection = make_connection(user)
        self._stored_file(user, connection).delete()

        delete_message.assert_called_once_with(TOKEN, connection.chat_id, 4242)
        self.assertFalse(TelegramFile.objects.exists())

    @patch('telegram_module.storage.api.delete_message',
           side_effect=api.TelegramError('message to delete not found'))
    def test_a_failed_cleanup_still_deletes_our_row(self, _delete):
        user = make_user()
        upload = self._stored_file(user, make_connection(user))

        upload.delete()
        self.assertFalse(UploadFile.objects.filter(pk=upload.pk).exists())


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class StaffPageTests(TelegramTestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_user('staff@example.com')
        self.staff.is_staff = True
        self.staff.save()
        self.url = reverse('staff_telegram')

    def test_an_ordinary_user_is_turned_away(self):
        self.client.force_login(make_user('plain@example.com'))
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_it_needs_a_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_staff_see_the_page(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_no_user_token_is_ever_rendered(self):
        make_connection(make_user('holder@example.com'))
        self.client.force_login(self.staff)

        # Staff hold no credential that reaches anyone's channel, and this page
        # must not become the place they can read one.
        self.assertNotContains(self.client.get(self.url), TOKEN)

    def test_the_switch_turns_the_feature_off(self):
        self.client.force_login(self.staff)
        self.client.post(self.url, {'action': 'toggle'})
        self.assertFalse(feature.is_enabled())

    def test_unreadable_tokens_are_counted(self):
        connection = make_connection(make_user('holder@example.com'))
        connection.bot_token_encrypted = 'not-decryptable'
        connection.save()

        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.context['unreadable'], 1)


@override_settings(MEDIA_ROOT=MEDIA_ROOT, TELEGRAM_CHUNK_BYTES=4)
class TransferProgressTests(TelegramTestCase):
    """The percentage shown while a file is on its way to the channel."""

    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.client.force_login(self.user)
        self.connection = make_connection(self.user)
        self.url = reverse('telegram_connect_page')

    def _queued(self, total=10):
        upload = make_file(self.user, b'0' * total)
        return TelegramFile.objects.create(
            upload_file=upload, connection=self.connection,
            total_bytes=total, chunk_bytes=4,
            status=TelegramFile.Status.UPLOADING,
        )

    def _chunk(self, telegram_file, index, size):
        return TelegramChunk.objects.create(
            telegram_file=telegram_file, index=index, size_bytes=size,
            message_id=1000 + index, file_id=f'f{index}',
            file_unique_id=f'u{index}',
        )

    def test_nothing_sent_reads_zero(self):
        self.assertEqual(self._queued().progress_percent, 0)

    def test_it_counts_the_parts_that_arrived(self):
        telegram_file = self._queued(total=10)
        self._chunk(telegram_file, 0, 4)
        self._chunk(telegram_file, 1, 4)

        self.assertEqual(telegram_file.sent_bytes, 8)
        self.assertEqual(telegram_file.progress_percent, 80)

    def test_it_never_reads_a_hundred_before_it_is_stored(self):
        telegram_file = self._queued(total=10)
        self._chunk(telegram_file, 0, 10)
        # Every part is in, but the row is not marked stored until the local
        # copy is released. Showing 100 here would be a lie for that moment.
        self.assertEqual(telegram_file.progress_percent, 99)

    def test_a_stored_file_reads_a_hundred(self):
        telegram_file = self._queued()
        TelegramFile.objects.filter(pk=telegram_file.pk).update(
            status=TelegramFile.Status.STORED,
        )
        telegram_file.refresh_from_db()
        self.assertEqual(telegram_file.progress_percent, 100)

    def test_a_file_of_unknown_size_does_not_divide_by_zero(self):
        telegram_file = self._queued()
        TelegramFile.objects.filter(pk=telegram_file.pk).update(total_bytes=0)
        telegram_file.refresh_from_db()
        self.assertEqual(telegram_file.progress_percent, 0)

    def test_the_percentage_is_in_the_html_without_javascript(self):
        telegram_file = self._queued(total=10)
        self._chunk(telegram_file, 0, 4)

        response = self.client.get(self.url)
        # The page must be right on load, not only after a script runs.
        self.assertContains(response, 'data-telegram-fill')
        self.assertContains(response, '40٪')

    def test_a_stored_file_shows_no_bar(self):
        telegram_file = self._queued()
        TelegramFile.objects.filter(pk=telegram_file.pk).update(
            status=TelegramFile.Status.STORED,
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, 'data-telegram-fill')

    def test_the_progress_endpoint_reports_each_file(self):
        telegram_file = self._queued(total=10)
        self._chunk(telegram_file, 0, 4)

        payload = self.client.get(self.url, {'progress': '1'}).json()
        row = payload['files'][0]
        self.assertEqual(row['id'], telegram_file.pk)
        self.assertEqual(row['percent'], 40)
        self.assertEqual(row['sent'], 4)
        self.assertTrue(payload['busy'])

    def test_it_reports_idle_when_nothing_is_moving(self):
        telegram_file = self._queued()
        TelegramFile.objects.filter(pk=telegram_file.pk).update(
            status=TelegramFile.Status.STORED,
        )
        payload = self.client.get(self.url, {'progress': '1'}).json()
        # The script stops polling on this, so an idle page costs one request.
        self.assertFalse(payload['busy'])

    def test_one_user_never_sees_another_users_transfers(self):
        other = make_user('other@example.com')
        other_connection = make_connection(other, chat_id=-100999, bot_id=777)
        TelegramFile.objects.create(
            upload_file=make_file(other), connection=other_connection,
            total_bytes=10, status=TelegramFile.Status.UPLOADING,
        )

        payload = self.client.get(self.url, {'progress': '1'}).json()
        self.assertEqual(payload['files'], [])

    def test_the_endpoint_needs_a_login(self):
        self.client.logout()
        response = self.client.get(self.url, {'progress': '1'})
        self.assertEqual(response.status_code, 302)

    @patch('telegram_module.storage.api.send_document')
    def test_progress_advances_across_a_resumed_transfer(self, send):
        state = {'n': 0}

        def send_part(token, chat_id, filename, stream, caption=None):
            state['n'] += 1
            if state['n'] == 3:
                raise api.TelegramError('temporary trouble')
            return {
                'message_id': 1000 + state['n'],
                'document': {'file_id': f'f{state["n"]}',
                             'file_unique_id': f'u{state["n"]}'},
            }

        send.side_effect = send_part
        upload = make_file(self.user, b'0123456789')
        telegram_file = storage.queue(upload, self.connection)

        storage.transfer(telegram_file)
        telegram_file.refresh_from_db()
        self.assertEqual(telegram_file.progress_percent, 80)

        storage.transfer(telegram_file)
        telegram_file.refresh_from_db()
        self.assertEqual(telegram_file.progress_percent, 100)


@override_settings(MEDIA_ROOT=MEDIA_ROOT, TELEGRAM_CHUNK_BYTES=4)
class DownloadNameTests(TelegramTestCase):
    """What a file is called once it no longer has a file field."""

    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.connection = make_connection(self.user)

    def _fake_send(self):
        state = {'n': 0}

        def send(token, chat_id, filename, stream, caption=None):
            state['n'] += 1
            return {'message_id': 1000 + state['n'],
                    'document': {'file_id': f'f{state["n"]}',
                                 'file_unique_id': f'u{state["n"]}'}}
        return send

    @patch('telegram_module.storage.api.send_document')
    def test_the_uploaded_filename_survives_the_transfer(self, send):
        from upload_module.downloads import download_filename

        send.side_effect = self._fake_send()
        upload = make_file(self.user, b'0123', name='installer.exe')
        # A display title without an extension, which is the usual case.
        upload.name = 'نصب برنامه'
        upload.save()

        storage.transfer(storage.queue(upload, self.connection))
        upload.refresh_from_db()

        # The file field is empty now, so without the captured name the
        # download would arrive called "نصب برنامه" with no extension.
        self.assertFalse(upload.file)
        self.assertEqual(download_filename(upload), 'installer.exe')

    @patch('telegram_module.storage.api.send_document')
    def test_a_file_with_no_title_still_downloads_named(self, send):
        from upload_module.downloads import download_filename

        send.side_effect = self._fake_send()
        upload = make_file(self.user, b'0123', name='archive.zip')
        upload.name = ''
        upload.save()

        storage.transfer(storage.queue(upload, self.connection))
        upload.refresh_from_db()
        self.assertEqual(download_filename(upload), 'archive.zip')

    @patch('telegram_module.storage.api.send_document')
    def test_the_stored_name_carries_no_directory(self, send):
        send.side_effect = self._fake_send()
        upload = make_file(self.user, b'0123', name='report.bin')
        telegram_file = storage.queue(upload, self.connection)

        # upload_to puts the file under dl/files/; the header must not.
        self.assertNotIn('/', telegram_file.original_name)
        self.assertTrue(telegram_file.original_name.startswith('report'))

    @patch('telegram_module.importing.api.delete_message')
    @patch('telegram_module.importing.api.call')
    def test_an_imported_post_keeps_telegrams_filename(self, call, _delete):
        from upload_module.downloads import download_filename

        connection = make_connection(
            make_user('importer@example.com'), chat_id=-1001234567890,
            bot_id=321,
        )
        call.return_value = {
            'message_id': 999,
            'document': {'file_id': 'f', 'file_unique_id': 'u',
                         'file_name': 'holiday.zip', 'file_size': 10},
        }
        upload = importing.import_post(
            connection.user, connection, 'https://t.me/c/1234567890/42',
        )
        self.assertEqual(download_filename(upload), 'holiday.zip')

    def test_a_row_from_before_the_field_falls_back_to_the_title(self):
        from upload_module.downloads import download_filename

        upload = make_file(self.user, b'0123', name='x.bin')
        upload.name = 'گزارش.pdf'
        upload.save()
        TelegramFile.objects.create(
            upload_file=upload, connection=self.connection, total_bytes=4,
            status=TelegramFile.Status.STORED, original_name='',
        )
        UploadFile.objects.filter(pk=upload.pk).update(
            storage_backend=UploadFile.StorageBackend.TELEGRAM, file='',
        )
        upload.refresh_from_db()
        self.assertEqual(download_filename(upload), 'گزارش.pdf')


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class SoftDeleteTests(TelegramTestCase):
    """Deleting a Telegram-backed file from the panel."""

    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.client.force_login(self.user)
        self.connection = make_connection(self.user)
        self.upload = make_file(self.user, b'0123456789')
        self.telegram_file = TelegramFile.objects.create(
            upload_file=self.upload, connection=self.connection,
            total_bytes=10, status=TelegramFile.Status.STORED,
            original_name='report.bin',
        )
        TelegramChunk.objects.create(
            telegram_file=self.telegram_file, index=0, size_bytes=10,
            message_id=4242, file_id='fid', file_unique_id='uid',
        )
        UploadFile.objects.filter(pk=self.upload.pk).update(
            storage_backend=UploadFile.StorageBackend.TELEGRAM,
            file='', size_bytes_cached=10,
        )
        self.upload.refresh_from_db()

    @patch('telegram_module.storage.api.delete_message')
    def test_the_channel_messages_go_too(self, delete_message):
        self.upload.soft_delete()
        delete_message.assert_called_once_with(
            TOKEN, self.connection.chat_id, 4242,
        )

    @patch('telegram_module.storage.api.delete_message')
    def test_the_transfer_record_goes_but_the_file_row_stays(self, _delete):
        self.upload.soft_delete()

        self.assertFalse(TelegramFile.objects.exists())
        self.assertFalse(TelegramChunk.objects.exists())
        # The file row survives so purchases and earnings keep a target.
        self.assertTrue(UploadFile.objects.filter(pk=self.upload.pk).exists())

    @patch('telegram_module.storage.api.delete_message',
           side_effect=api.TelegramError('message to delete not found'))
    def test_a_channel_that_refuses_does_not_block_the_delete(self, _delete):
        self.upload.soft_delete()

        self.upload.refresh_from_db()
        # Messages left in the user's own channel are theirs to remove; a
        # delete they asked for must still happen on our side.
        self.assertTrue(self.upload.is_deleted())

    @patch('telegram_module.storage.api.delete_message')
    def test_a_deleted_file_is_no_longer_downloadable(self, _delete):
        self.upload.soft_delete()
        self.upload.refresh_from_db()

        self.assertFalse(self.upload.has_content())
        self.assertFalse(self.upload.is_public_now())

    @patch('telegram_module.storage.api.delete_message')
    def test_it_releases_the_telegram_total(self, _delete):
        from account_module import quota as storage_quota

        self.assertEqual(storage_quota.stored_on_telegram(self.user), 10)
        self.upload.soft_delete()
        self.assertEqual(storage_quota.stored_on_telegram(self.user), 0)

    @patch('telegram_module.storage.api.delete_message')
    def test_deleting_twice_is_harmless(self, delete_message):
        self.upload.soft_delete()
        self.upload.soft_delete()
        delete_message.assert_called_once()
