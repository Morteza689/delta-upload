"""Tests for the S3 migration command.

Without touching real S3: a second FileSystemStorage plays the
destination, since the command only uses the standard Storage API and does
not care what sits behind it.
"""
import shutil
import tempfile
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from unittest import mock

from .models import UploadFile

LOCAL_ROOT = tempfile.mkdtemp()
REMOTE_ROOT = tempfile.mkdtemp()
User = get_user_model()

BODY = b'file-contents-' + b'y' * 500


@override_settings(MEDIA_ROOT=LOCAL_ROOT, USE_S3=True)
class MigrateMediaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(LOCAL_ROOT, ignore_errors=True)
        shutil.rmtree(REMOTE_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.remote = FileSystemStorage(location=REMOTE_ROOT)
        self.user = User.objects.create_user(
            username='owner', email='owner@example.com', password='pw',
            email_active_code='o' * 10,
        )
        self.upload = UploadFile.objects.create(
            name='demo', author=self.user, slug='demo1',
            file=SimpleUploadedFile('demo.bin', BODY),
        )

    def run_command(self, *args):
        out = StringIO()
        with mock.patch(
            'upload_module.management.commands.migrate_media_to_s3.default_storage',
            self.remote,
        ):
            call_command('migrate_media_to_s3', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_moves_nothing(self):
        output = self.run_command('--dry-run')

        self.assertIn('would move', output)
        self.assertFalse(self.remote.exists(self.upload.file.name))

    def test_file_is_copied_with_identical_contents(self):
        self.run_command()

        name = self.upload.file.name
        self.assertTrue(self.remote.exists(name))
        with self.remote.open(name, 'rb') as handle:
            self.assertEqual(handle.read(), BODY)

    def test_local_copy_is_kept_by_default(self):
        """Deleting too early leaves no way back if the transfer was wrong."""
        self.run_command()

        local = FileSystemStorage(location=LOCAL_ROOT)
        self.assertTrue(local.exists(self.upload.file.name))

    def test_delete_local_removes_the_original(self):
        self.run_command('--delete-local')

        local = FileSystemStorage(location=LOCAL_ROOT)
        self.assertFalse(local.exists(self.upload.file.name))
        self.assertTrue(self.remote.exists(self.upload.file.name))

    def test_running_twice_skips_already_migrated_files(self):
        self.run_command()
        output = self.run_command()

        self.assertIn('skip', output)
        self.assertIn('moved=0', output)

    def test_a_missing_local_file_is_reported_not_crashed(self):
        local = FileSystemStorage(location=LOCAL_ROOT)
        local.delete(self.upload.file.name)

        output = self.run_command()
        self.assertIn('missing', output)

    def test_records_without_a_file_are_ignored(self):
        UploadFile.objects.create(name='no file', author=self.user, slug='nofile')
        output = self.run_command()
        self.assertIn('moved=1', output)

    @override_settings(USE_S3=False)
    def test_command_refuses_to_run_without_s3_enabled(self):
        """Without USE_S3 the default storage is the local disk and the command
        would do nothing.
        """
        with self.assertRaises(CommandError):
            self.run_command()
