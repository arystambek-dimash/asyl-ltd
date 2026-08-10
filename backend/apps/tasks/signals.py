from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import TaskAttachment


@receiver(post_delete, sender=TaskAttachment)
def delete_attachment_file_after_commit(sender, instance, using, **kwargs):
    """Delete private media only after its database row is really gone."""

    if not instance.file or not instance.file.name:
        return
    name = instance.file.name
    storage = instance.file.storage

    def delete_if_unreferenced():
        # A legacy/imported row may intentionally share one physical object.
        # Removing either row must not break the remaining attachment.
        if TaskAttachment.objects.using(using).filter(file=name).exists():
            return
        storage.delete(name)

    # Rollbacks discard this callback, so the still-existing row never loses
    # its file. Robust mode avoids reporting a failed request after the DB
    # deletion has already committed; Django logs any storage error.
    transaction.on_commit(delete_if_unreferenced, using=using, robust=True)
