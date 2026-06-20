from rest_framework.permissions import BasePermission


def _auth(request):
    return bool(request.user and request.user.is_authenticated)


def _super(request):
    # A superuser (admin) has full access to every staff-role action.
    return _auth(request) and request.user.is_superuser


class IsStaff(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and not request.user.is_client


class IsClientUser(BasePermission):
    def has_permission(self, request, view):
        return _auth(request) and request.user.is_client


class IsManager(BasePermission):
    def has_permission(self, request, view):
        return _super(request) or (_auth(request) and request.user.is_manager)


class IsAccountant(BasePermission):
    def has_permission(self, request, view):
        return _super(request) or (_auth(request) and request.user.is_accountant)


class IsOperator(BasePermission):
    def has_permission(self, request, view):
        return _super(request) or (_auth(request) and request.user.is_operator)


class IsBoss(BasePermission):
    def has_permission(self, request, view):
        return _super(request) or (_auth(request) and request.user.is_boss)


class IsOperatorOrBoss(BasePermission):
    def has_permission(self, request, view):
        if _super(request):
            return True
        return _auth(request) and not request.user.is_client and (
            request.user.is_operator or request.user.is_boss
        )
