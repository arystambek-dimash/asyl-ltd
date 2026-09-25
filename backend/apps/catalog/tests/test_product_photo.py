import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]


def _image(fmt="PNG", size=(2400, 1600), mode="RGBA"):
    buffer = io.BytesIO()
    Image.new(mode, size, (200, 30, 30, 0) if mode == "RGBA" else (200, 30, 30)).save(buffer, fmt)
    return SimpleUploadedFile(f"photo.{fmt.lower()}", buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def _upload(api, product, upload):
    return api.post(f"/api/products/{product.pk}/photo/", {"photo": upload}, format="multipart")


def test_manager_uploads_photo_normalized_to_small_jpeg(auth_client, manager, api_client, make_product):
    product = make_product()

    response = _upload(auth_client(manager), product, _image())

    assert response.status_code == 200, response.data
    product.refresh_from_db()
    with Image.open(product.photo.path) as stored:
        assert stored.format == "JPEG"
        assert max(stored.size) == 1200
        # Прозрачный фон PNG стал белым, а не чёрным.
        assert stored.getpixel((0, 0)) == (255, 255, 255)

    photo = api_client.get(response.data["photo_url"])
    assert photo.status_code == 200
    assert photo["Content-Type"] == "image/jpeg"
    assert "immutable" in photo["Cache-Control"]
    photo.close()


def test_replacing_photo_expires_old_link_and_deletes_old_file(
    auth_client, manager, api_client, django_capture_on_commit_callbacks, make_product
):
    product = make_product()
    api = auth_client(manager)
    first = _upload(api, product, _image("JPEG", mode="RGB"))
    product.refresh_from_db()
    old_path = product.photo.path

    with django_capture_on_commit_callbacks(execute=True):
        second = _upload(api, product, _image("JPEG", size=(300, 300), mode="RGB"))

    assert second.data["photo_url"] != first.data["photo_url"]
    assert api_client.get(first.data["photo_url"]).status_code == 404
    assert not (product.photo.storage.exists(old_path))


def test_delete_removes_photo(auth_client, manager, django_capture_on_commit_callbacks, make_product):
    product = make_product()
    api = auth_client(manager)
    _upload(api, product, _image())
    product.refresh_from_db()
    name = product.photo.name

    with django_capture_on_commit_callbacks(execute=True):
        response = api.delete(f"/api/products/{product.pk}/photo/")

    assert response.status_code == 200
    assert response.data["photo_url"] is None
    assert not product.photo.storage.exists(name)


def test_rejects_non_image_and_forged_token(auth_client, manager, api_client, make_product):
    product = make_product()
    api = auth_client(manager)

    bogus = SimpleUploadedFile("photo.jpg", b"not an image", content_type="image/jpeg")
    assert _upload(api, product, bogus).status_code == 400

    _upload(api, product, _image())
    assert api_client.get(f"/api/product-photos/{product.pk}/?token=forged").status_code == 404


def test_photo_upload_requires_catalog_edit(auth_client, operator, make_product):
    product = make_product()

    assert _upload(auth_client(operator), product, _image()).status_code == 403


def test_portal_catalog_shows_photo_url(auth_client, manager, client_user, make_product):
    from apps.clients.models import Client
    from apps.warehouse.models import StockItem, Warehouse

    product = make_product()
    warehouse = Warehouse.objects.filter(is_default=True).first()
    StockItem.objects.update_or_create(product=product, warehouse=warehouse, defaults={"bags": 10})
    Client.objects.create_with_user(user=client_user, first_name="Портал", phone="x")
    _upload(auth_client(manager), product, _image())

    rows = auth_client(client_user).get("/api/portal/catalog/").data

    row = next(item for item in rows if item["id"] == product.pk)
    assert row["photo_url"].startswith(f"/api/product-photos/{product.pk}/?token=")
