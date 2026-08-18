"""Keeping the channel in step with deletions on our side.

Deleting a file in the panel has to remove it from the channel too. Otherwise
the user deletes something, sees it disappear, and it is still sitting in their
Telegram, which is not what deleting means.

This lives in a signal rather than in UploadFile.delete so upload_module keeps
knowing nothing about Telegram, and so bulk deletes are covered as well.
"""
import logging

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from upload_module.models import UploadFile

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=UploadFile)
def remove_from_telegram(sender, instance, **kwargs):
    # pre_delete rather than post_delete: the chunk rows cascade away with the
    # file, and once they are gone nothing knows which messages to remove.
    if instance.storage_backend != UploadFile.StorageBackend.TELEGRAM:
        return

    from . import storage

    telegram_file = getattr(instance, 'telegram_copy', None)
    if telegram_file is None:
        return

    try:
        storage.delete_from_telegram(telegram_file)
    except Exception:
        # A file the user asked to delete is deleted here regardless. Leftover
        # messages in the channel are the smaller problem, and the user can
        # remove them by hand.
        logger.exception('could not clean up telegram messages for %s', instance.pk)
