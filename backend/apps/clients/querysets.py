"""Reusable visibility rules for client-owned records."""

from .models import Client


def visible_clients(user, base_perm: str = "clients.view"):
    """Return clients available to a staff user with the requested permission."""
    if not user or not user.is_authenticated or getattr(user, "is_client", False):
        return Client.objects.none()
    if user.is_superuser or user.has_perm_code(base_perm):
        return Client.objects.all()
    return Client.objects.none()
