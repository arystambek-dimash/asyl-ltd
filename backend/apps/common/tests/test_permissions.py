import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.common.permissions import SUPERUSER_ONLY, PermAPIViewMixin

pytestmark = pytest.mark.django_db


class ExampleView(PermAPIViewMixin, APIView):
    required_perms = {"get": "payments.view", "patch": SUPERUSER_ONLY}

    def get(self, request):
        return Response({"ok": True})

    def patch(self, request):
        return Response({"ok": True})

    def put(self, request):
        return Response({"ok": True})


def _call(method, user):
    request = getattr(APIRequestFactory(), method)("/example/")
    force_authenticate(request, user=user)
    return ExampleView.as_view()(request)


def test_perm_api_view_maps_methods_to_codes(user_with_perms):
    user = user_with_perms("perm-viewer", codes=["payments.view"])

    assert _call("get", user).status_code == 200
    assert _call("head", user).status_code == 200
    assert _call("patch", user).status_code == 403


def test_perm_api_view_denies_undeclared_handled_method(user_with_perms):
    user = user_with_perms("perm-put", codes=["payments.view"])

    assert _call("put", user).status_code == 403


def test_perm_api_view_answers_405_for_method_without_handler(user_with_perms):
    user = user_with_perms("perm-post", codes=["payments.view"])

    assert _call("post", user).status_code == 405
