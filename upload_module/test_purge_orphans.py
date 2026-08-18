"""Sweeping up stored files no row points at.

Several paths deliberately leave bytes behind rather than failing the operation
that produced them: a Telegram transfer that clears the file field but cannot
delete the local copy, a delete whose storage call raises, an upload whose
request dies before the row is saved. Each is the right call in the moment, and
together they fill a disk with nothing to find them.
"""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from upload_module.models import UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class PurgeOrphanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='sweeper@example.com', email='sweeper@example.com',
            password='pw-for-testing-1', email_active_code='s' * 20,
        )
        self.kept = UploadFile.objects.create(
            name='kept.bin', author=self.user, slug='keptfile123',
            type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile('kept.bin', b'x' * 40),
        )
        self.orphan = default_storage.save(
            'dl/files/orphan.bin', ContentFile(b'y' * 80),
        )

    def sweep(self, **options):
        call_command('purge_orphan_files', grace_hours=0, **options)

    def test_a_file_with_a_row_is_left_alone(self):
        self.sweep(delete=True)
        self.assertTrue(default_storage.exists(self.kept.file.name))

    def test_a_file_with_no_row_is_removed(self):
        self.sweep(delete=True)
        self.assertFalse(default_storage.exists(self.orphan))

    def test_it_reports_without_deleting_by_default(self):
        call_command('purge_orphan_files', grace_hours=0)
        # A sweep that deletes by default is one nobody dares run.
        self.assertTrue(default_storage.exists(self.orphan))

    def test_a_recent_file_is_left_alone(self):
        # An upload in flight has bytes on disk before its row exists.
        call_command('purge_orphan_files', grace_hours=24, delete=True)
        self.assertTrue(default_storage.exists(self.orphan))

    def test_a_file_released_by_a_telegram_transfer_is_swept(self):
        released = default_storage.save(
            'dl/files/released.bin', ContentFile(b'z' * 10),
        )
        upload = UploadFile.objects.create(
            name='moved.bin', author=self.user, slug='movedfile12',
            type_file=UploadFile.ChoiceType.File, file=released,
            storage_backend=UploadFile.StorageBackend.TELEGRAM,
        )
        # What a finished transfer does: the row stops pointing at the bytes.
        UploadFile.objects.filter(pk=upload.pk).update(file='')

        self.sweep(delete=True)
        self.assertFalse(default_storage.exists(released))

    def test_a_soft_deleted_file_leaves_nothing_behind(self):
        upload = UploadFile.objects.create(
            name='doomed.bin', author=self.user, slug='doomedfile1',
            type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile('doomed.bin', b'q' * 20),
        )
        name = upload.file.name
        upload.soft_delete()

        # soft_delete removes the bytes itself; the sweep is the safety net for
        # when that call fails.
        self.assertFalse(default_storage.exists(name))
        self.sweep(delete=True)
        self.assertTrue(default_storage.exists(self.kept.file.name))

    def test_it_survives_an_empty_directory(self):
        default_storage.delete(self.orphan)
        default_storage.delete(self.kept.file.name)
        self.sweep(delete=True)

    def test_it_does_not_query_once_per_file(self):
        for index in range(12):
            default_storage.save(f'dl/files/extra{index}.bin', ContentFile(b'.'))

        with self.assertNumQueries(1):
            call_command('purge_orphan_files', grace_hours=0)
