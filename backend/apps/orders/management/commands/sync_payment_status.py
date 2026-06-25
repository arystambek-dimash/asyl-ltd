from django.core.management.base import BaseCommand
from apps.orders.models import Order
from apps.orders.services import sync_payment_status, _payment_status_for


class Command(BaseCommand):
    help = "Привести payment_status всех заказов в соответствие с фактическими оплатами."

    def handle(self, *args, **options):
        fixed = 0
        for order in Order.objects.prefetch_related("items__product", "payments"):
            want = _payment_status_for(order)
            if want != order.payment_status:
                old = order.payment_status
                sync_payment_status(order)
                fixed += 1
                self.stdout.write(f"#{order.id}: {old} → {want}")
        self.stdout.write(self.style.SUCCESS(f"Готово. Исправлено заказов: {fixed}"))
