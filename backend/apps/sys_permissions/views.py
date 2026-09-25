from apps.common.permissions import PermViewSetMixin
from django.db.models import Case, IntegerField, Value, When
from rest_framework import mixins, viewsets

from .models import Permission
from .perms import PERMISSIONS
from .serializers import PermissionSerializer

# Порядок каталога = меню и порядок действий на странице; коды вне каталога — в конце.
_CATALOG_RANK = Case(
    *(When(code=permission["code"], then=Value(rank)) for rank, permission in enumerate(PERMISSIONS)),
    default=Value(len(PERMISSIONS)),
    output_field=IntegerField(),
)


class PermissionViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Permission.objects.order_by(_CATALOG_RANK, "code")
    serializer_class = PermissionSerializer

    required_perms = {
        "list": ("sys_permissions.manage", "employees.manage"),
    }
