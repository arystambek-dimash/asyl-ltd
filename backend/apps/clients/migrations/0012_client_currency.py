from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("clients", "0011_client_company_name")]
    operations = [
        migrations.AddField(
            model_name="client",
            name="currency",
            field=models.CharField(
                choices=[("KZT", "KZT (тенге)"), ("USD", "USD (доллар)")],
                default="KZT",
                max_length=3,
            ),
        ),
    ]
