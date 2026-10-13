from django.db import transaction
from django.db.models import Count
from rest_framework import viewsets

from apps.common.permissions import HasPerm, IsStaff, IsSuperUser
from apps.orders.models import Order

from .access import scope_by_client_department
from .apipay_accounts import set_account_departments
from .models import ApiPayAccount, Department
from .serializers import ApiPayAccountSerializer, DepartmentSerializer


class DepartmentViewSet(viewsets.ModelViewSet):
    """Динамические отделы продаж, используемые сотрудниками и заказами.

    Отдел не удаляется, а отключается через PATCH ``is_active``.
    """

    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = Department.objects.select_related("apipay_account")
    serializer_class = DepartmentSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        return [HasPerm("sys_permissions.manage")]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list" and self.request.query_params.get("all") != "1":
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # Счётчик «N заказов» — как в отчётах: заказы из корзины не считаем.
        orders = scope_by_client_department(
            Order.objects.all(),
            self.request.user,
            client_path="client",
        )
        context["department_order_counts"] = dict(
            orders.values("department")
            .annotate(total=Count("id"))
            .values_list("department", "total")
        )
        return context


class ApiPayAccountViewSet(viewsets.ModelViewSet):
    """Подключения Kaspi (ApiPay) и их отделы — только суперюзеру.

    Удаление оставляет отделы подключения без Kaspi.
    """

    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    queryset = ApiPayAccount.objects.prefetch_related("departments")
    serializer_class = ApiPayAccountSerializer
    permission_classes = [IsSuperUser]

    @transaction.atomic
    def perform_create(self, serializer):
        self._save_with_departments(serializer)

    @transaction.atomic
    def perform_update(self, serializer):
        self._save_with_departments(serializer)

    @staticmethod
    def _save_with_departments(serializer):
        departments = serializer.validated_data.pop("departments", None)
        account = serializer.save()
        if departments is not None:
            set_account_departments(account, departments)
            # Ответ показывает отделы после переноса, а не из prefetch.
            serializer.instance = ApiPayAccount.objects.prefetch_related(
                "departments"
            ).get(pk=account.pk)
