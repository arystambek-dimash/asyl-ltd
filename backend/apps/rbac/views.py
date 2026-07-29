from django.db.models import Count
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError

from apps.common.permissions import PermViewSetMixin

from .models import Permission, Role
from .serializers import PermissionSerializer, RoleSerializer


RBAC_VIEW = "rbac.view"
RBAC_MANAGE = "rbac.manage"


class PermissionViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Permission.objects.order_by("section", "action", "code")
    serializer_class = PermissionSerializer
    http_method_names = ["get", "head", "options"]
    # Каталог прав нужен и тому, кто создаёт сотрудников (выбор доступов в форме).
    required_perms = {
        "list": (RBAC_VIEW, "employees.manage"),
        "retrieve": (RBAC_VIEW, "employees.manage"),
    }


class RoleViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = RoleSerializer
    http_method_names = ["get", "post", "patch", "put", "delete", "head", "options"]
    required_perms = {
        "list": RBAC_VIEW,
        "retrieve": RBAC_VIEW,
        "create": RBAC_MANAGE,
        "update": RBAC_MANAGE,
        "partial_update": RBAC_MANAGE,
        "destroy": RBAC_MANAGE,
    }

    def get_queryset(self):
        return (
            Role.objects.prefetch_related("permissions")
            .annotate(employee_count=Count("employees", distinct=True))
            .order_by("-is_system", "name")
        )

    def _assert_no_escalation(self, serializer):
        """Раздать можно только то, чем владеешь сам.

        Права роли входят в effective_perm_codes сотрудника, поэтому без этой
        проверки владелец rbac.manage мог бы выписать роли payments.confirm
        или shipping.debt_override. Штатные пресеты этого не допускают —
        rbac есть только у «Начальника», у которого и так все права, — но
        проверка закрывает саму возможность собрать такую роль руками.
        """
        user = self.request.user
        permissions = serializer.validated_data.get("permissions")
        if permissions is None or user.is_superuser:
            return
        excess = sorted({p.code for p in permissions} - user.perm_codes)
        if excess:
            raise ValidationError({
                "detail": "Нельзя выдать права, которых нет у вас: "
                          + ", ".join(excess),
                "code": "perm_escalation",
            })

    def perform_create(self, serializer):
        self._assert_no_escalation(serializer)
        serializer.save()

    def _assert_not_own_role(self, instance):
        """Свою роль не редактируют: это обход проверки на эскалацию.

        Иначе владелец rbac.manage дописывает права себе же — они попадают
        в effective_perm_codes сразу, без участия второго человека.
        """
        user = self.request.user
        if user.is_superuser:
            return
        employee = getattr(user, "_employee", None)
        if employee is not None and employee.role_id == instance.pk:
            raise ValidationError({
                "detail": "Свою роль изменить нельзя — попросите администратора",
                "code": "self_role_edit",
            })

    def perform_update(self, serializer):
        self._assert_not_own_role(serializer.instance)
        self._assert_no_escalation(serializer)
        serializer.save()

    def perform_destroy(self, instance):
        # Удалять можно любые роли (включая системные), кроме тех, на которые
        # назначены сотрудники — иначе они остались бы без роли. Свою роль
        # удалять нельзя: это лишило бы прав самого удаляющего.
        self._assert_not_own_role(instance)
        if instance.employees.exists():
            raise ValidationError({"detail": "На роль назначены сотрудники — удаление запрещено", "code": "role_in_use"})
        instance.delete()
