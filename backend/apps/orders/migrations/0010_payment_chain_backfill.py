from django.db import migrations


def forward(apps, schema_editor):
    Payment = apps.get_model("orders", "Payment")
    Order = apps.get_model("orders", "Order")
    # Легаси-заявки клиентов «pending» встают в цепочку на шаг «принята»:
    # клиент заявил перевод — дальше сверка бухгалтером и касса.
    Payment.objects.filter(status="pending").update(status="received")
    # Заказы наследуют отдел клиента (до сих пор все клиенты — Отдел 1).
    for dept in ("main", "field"):
        Order.objects.filter(client__department=dept).exclude(
            department=dept).update(department=dept)


def backward(apps, schema_editor):
    Payment = apps.get_model("orders", "Payment")
    Payment.objects.filter(status="received").update(status="pending")


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0009_order_department_payment_accountant_at_and_more"),
        ("clients", "0005_client_department_client_manager"),
    ]
    operations = [migrations.RunPython(forward, backward)]
