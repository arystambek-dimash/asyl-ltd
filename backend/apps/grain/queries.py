"""Read projections for silo API responses; balances in commands stay live."""

from django.db.models import BigIntegerField, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import GrainMovement, Silo, SiloReservation, Wagon
from .statuses import EXITED, TERMINAL_STATUSES


def silo_overview():
    balance = GrainMovement.objects.filter(silo_id=OuterRef("pk")).order_by("-id")
    reserved = (SiloReservation.objects.filter(silo_id=OuterRef("pk"), active=True)
                .order_by().values("silo_id").annotate(total=Sum("amount_kg")))
    active_wagons = Wagon.objects.exclude(status__in=TERMINAL_STATUSES | {EXITED}).only(
        "id", "number", "status", "assigned_silo_id"
    )
    return (Silo.objects.select_related("silo_type").annotate(
        _balance_kg=Coalesce(Subquery(balance.values("balance_after_kg")[:1]),
                             Value(0), output_field=BigIntegerField()),
        _reserved_kg=Coalesce(Subquery(reserved.values("total")[:1]),
                              Value(0), output_field=BigIntegerField()),
    ).prefetch_related(
        "default_for_types",
        Prefetch("assigned_wagons", queryset=active_wagons, to_attr="_active_wagons"),
    ))
