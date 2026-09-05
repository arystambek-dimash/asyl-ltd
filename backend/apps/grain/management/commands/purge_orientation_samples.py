"""Удалить датасет ориентации из CRM и с Camera-PC, когда модель обучилась.

``--all`` стирает всё (на ПК одним запросом), ``--older-than-days N`` — кадры
старше N дней по одному. ``--keep-pc`` не трогает Camera-PC: удаляются только
строки CRM. Очистка идёт пакетами по ``PURGE_BATCH`` строк, пока
``remaining`` не станет 0; если ПК перестал отвечать, останавливаемся —
оставшиеся доставленные кадры исключены и уйдут ночным экспортом. Очистка
двигает водораздел сбора: стёртый период ночью не собирается заново. Фото
взвешиваний остаются — это свидетельство рейса.
"""

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.grain import orientation_dataset
from apps.grain.models import VehicleOrientationSample


class Command(BaseCommand):
    help = "Purge orientation training samples from the CRM and from Camera-PC"

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument("--all", action="store_true", help="delete every sample")
        scope.add_argument(
            "--older-than-days",
            type=int,
            metavar="N",
            help="delete only samples captured more than N days ago",
        )
        parser.add_argument(
            "--keep-pc",
            action="store_true",
            help="delete CRM rows only, do not contact Camera-PC",
        )

    def handle(self, *args, **options):
        remove_from_pc = not options["keep_pc"]
        if options["all"]:

            def batch():
                return orientation_dataset.purge_all(remove_from_pc=remove_from_pc)

        else:
            days = options["older_than_days"]
            if days < 1:
                raise CommandError("--older-than-days must be >= 1")
            cutoff = timezone.now() - timedelta(days=days)

            def batch():
                return orientation_dataset.purge_samples(
                    VehicleOrientationSample.objects.filter(captured_at__lt=cutoff),
                    remove_from_pc=remove_from_pc,
                    cutoff=cutoff,
                    limit=orientation_dataset.PURGE_BATCH,
                )

        totals = {"deleted": 0, "removed_from_pc": 0, "pc_unavailable": False, "remaining": 0}
        while True:
            result = batch()
            totals["deleted"] += result["deleted"]
            totals["removed_from_pc"] += result["removed_from_pc"]
            totals["pc_unavailable"] = totals["pc_unavailable"] or result["pc_unavailable"]
            totals["remaining"] = result["remaining"]
            # Без ПК следующий пакет наткнётся на те же оставленные строки.
            if result["remaining"] == 0 or result["pc_unavailable"]:
                break
        self.stdout.write(json.dumps(totals, ensure_ascii=False))
