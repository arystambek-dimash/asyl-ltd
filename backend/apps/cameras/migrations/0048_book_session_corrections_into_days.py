"""Put wagon/truck colour corrections made before this release into their days.

Until now a session correction (for example 495 → 500) changed only the
session: the day tiles, periods and totals kept the camera count. An edit now
moves its day as well; this books the corrections that already exist, once.
"""

from django.db import migrations


def book_existing_corrections(apps, schema_editor):
    Session = apps.get_model("cameras", "ShippingLoadingSession")
    if not Session.objects.exclude(status="merged").exclude(colors_adjustment={}).exists():
        return
    # The shared code uses today's models, so it runs only when there is
    # something to book (never on a fresh database).
    from apps.cameras.shipping_segments import book_session_corrections_into_days

    book_session_corrections_into_days()


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0047_manual_colour_adjustments"),
    ]

    operations = [
        migrations.RunPython(book_existing_corrections, migrations.RunPython.noop),
    ]
