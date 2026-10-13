"""POST /api/clients/{id}/act-import/ — предпросмотр и перенос акта сверки порциями."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.client_acts.models import ClientActDocument
from apps.clients.models import Client
from apps.orders.models import Order

from .act_factory import act_xlsx, bank, sale

pytestmark = pytest.mark.django_db

IMPORT_CODES = ["orders.view", "orders.edit", "orders.confirm", "payments.view", "payments.create"]


@pytest.fixture
def client(departments):
    return Client.objects.create_with_user(
        first_name="Дан Агро Групп", phone="+996 555 000 000", department=departments[0], currency="USD",
    )


@pytest.fixture
def importer(user_with_perms, departments):
    return user_with_perms("act-importer", codes=IMPORT_CODES, department=departments[0])


def _file(rows=None, **options):
    content = act_xlsx(rows or [sale("10.06.2026", 100, "1"), bank("12.06.2026", 40, "394")], **options)
    return SimpleUploadedFile("Акт сверки.xlsx", content, content_type="application/octet-stream")


def _post(api, client, **data):
    return api.post(f"/api/clients/{client.pk}/act-import/", data, format="multipart")


def test_preview_then_import_until_finished(api_as, client, importer):
    api = api_as(importer)

    preview = _post(api, client, file=_file(), preview="true")

    assert preview.status_code == 200, preview.data
    assert preview.data["sales"] == {"count": 1, "total": "100.00"}
    assert preview.data["act"]["closing"] == "60.00"
    assert not Order.objects.exists()

    done = _post(api, client, file=_file(), preview="false", start=preview.data["start"])

    assert done.status_code == 200, done.data
    assert done.data == {"processed": 2, "payments": 1, "remaining": 0, "finished": True, "overpaid": "0.00"}
    assert Order.objects.filter(client=client).count() == 1
    assert ClientActDocument.objects.count() == 2


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({"preview": "true"}, "act_file_required"),
        ({"preview": "yes"}, "bad_preview"),
        ({"preview": "true", "start": "13.06.2026"}, "bad_date"),
    ],
)
def test_bad_requests(api_as, client, importer, data, code):
    if code != "act_file_required":
        data = {**data, "file": _file()}

    response = _post(api_as(importer), client, **data)

    assert response.status_code == 400
    assert response.data["code"] == code


def test_broken_act_is_explained(api_as, client, importer):
    response = _post(api_as(importer), client, file=_file(turnover=(1, None)), preview="true")

    assert response.status_code == 400
    assert response.data["code"] == "act_format"
    assert "Обороты за период" in response.data["detail"]


def test_client_of_another_department_is_not_found(api_as, client, user_with_perms, departments):
    stranger = user_with_perms("stranger", codes=IMPORT_CODES, department=departments[1])

    assert _post(api_as(stranger), client, file=_file(), preview="true").status_code == 404


@pytest.mark.parametrize("codes", [["orders.view"], ["orders.edit", "payments.create"]])
def test_needs_all_import_rights(api_as, client, user_with_perms, departments, codes):
    clerk = user_with_perms("clerk", codes=codes, department=departments[0])

    assert _post(api_as(clerk), client, file=_file(), preview="true").status_code == 403
