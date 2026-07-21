from django.db import migrations, models


def bind_existing_prices_to_client_currency(apps, schema_editor):
    ClientPrice = apps.get_model("catalog", "ClientPrice")
    Client = apps.get_model("clients", "Client")
    currencies = dict(Client.objects.values_list("id", "currency"))
    for price in ClientPrice.objects.all().iterator():
        price.currency = currencies.get(price.client_id, "KZT")
        price.save(update_fields=["currency"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0009_product_price_optional"),
        ("clients", "0012_client_currency"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientprice",
            name="currency",
            field=models.CharField(
                choices=[("KZT", "KZT (тенге)"), ("USD", "USD (доллар)")],
                default="KZT", max_length=3,
            ),
        ),
        migrations.RunPython(
            bind_existing_prices_to_client_currency,
            migrations.RunPython.noop,
        ),
        migrations.AlterUniqueTogether(
            name="clientprice",
            unique_together={("client", "product", "currency")},
        ),
    ]
