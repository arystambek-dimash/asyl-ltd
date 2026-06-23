from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="cv_class",
            field=models.CharField(
                blank=True,
                default="",
                max_length=20,
                choices=[
                    ("Red_50", "Красный 50 кг"), ("Red_25", "Красный 25 кг"),
                    ("Blue_50", "Синий 50 кг"), ("Blue_25", "Синий 25 кг"),
                    ("Green_50", "Зелёный 50 кг"), ("Green_25", "Зелёный 25 кг"),
                ],
            ),
        ),
    ]
