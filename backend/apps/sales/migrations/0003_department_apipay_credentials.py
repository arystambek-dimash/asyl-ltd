from django.db import migrations, models


class Migration(migrations.Migration):
    """Ключ ApiPay и секрет вебхука у отдела, зашифрованные (Fernet).

    ``db_default``: таблица ``clients_department`` общая с историческими
    миграциями ``clients``, и старые состояния модели вставляют строки без
    этих колонок.
    """

    dependencies = [
        ("sales", "0002_department_move_complete"),
    ]

    operations = [
        migrations.AddField(
            model_name="department",
            name="apipay_api_key_encrypted",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
        migrations.AddField(
            model_name="department",
            name="apipay_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="department",
            name="apipay_webhook_secret_encrypted",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
    ]
