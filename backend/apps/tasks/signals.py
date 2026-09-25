from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .attachments import delete_file_if_unreferenced
from .models import TaskAttachment


@receiver(post_delete, sender=TaskAttachment)
def delete_attachment_file_after_commit(sender, instance, using, **kwargs):
    """Delete private media only after its database row is really gone."""

    if not instance.file or not instance.file.name:
        return
    name = instance.file.name
    storage = instance.file.storage

    # Rollbacks discard this callback, so the still-existing row never loses
    # its file. Robust mode avoids reporting a failed request after the DB
    # deletion has already committed; Django logs any storage error.
    transaction.on_commit(
        lambda: delete_file_if_unreferenced(storage, name, using=using),
        using=using, robust=True,
    )
