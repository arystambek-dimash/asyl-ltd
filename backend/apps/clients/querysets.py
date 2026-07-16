"""Reusable visibility rules for client-owned records."""

from apps.rbac.scoping import scope_by_department

from .models import Client


def visible_clients(user, base_perm: str = "clients.view"):
    """Return only clients the staff user may use in cross-app operations."""
    return scope_by_department(Client.objects.all(), user, base_perm)
