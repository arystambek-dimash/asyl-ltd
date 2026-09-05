"""Админка датасета ориентации: посмотреть кадры, разметить, стереть.

Единственный пользователь — владелец: остальным сотрудникам раздел не виден.
Строки не редактируются формой — только действиями списка, которые ходят
через сервисы ``orientation_dataset`` (Camera-PC узнаёт об изменениях).
"""

from django.contrib import admin
from django.contrib.admin.views.main import ChangeList
from django.utils.html import format_html

from . import orientation_dataset
from .models import VEHICLE_ORIENTATION_FRONT, VEHICLE_ORIENTATION_REAR, VehicleOrientationSample
from .photos import photo_url

_UNLOADED = object()


class _SampleChangeList(ChangeList):
    """Страница списка грузит исходные взвешивания одним запросом на вид."""

    def get_results(self, request):
        super().get_results(request)
        rows = list(self.result_list)
        records = orientation_dataset.load_records(rows)
        for row in rows:
            row.source_record = records.get((row.record_kind, row.record_id))
        self.result_list = rows


@admin.register(VehicleOrientationSample)
class VehicleOrientationSampleAdmin(admin.ModelAdmin):
    list_display = (
        "thumbnail",
        "sample_id",
        "label",
        "label_source",
        "weight_kg",
        "captured_at",
        "conflict",
        "excluded",
        "sent_at",
        "last_error",
    )
    # Ссылка миниатюры открывает фото; в карточку ведёт идентификатор.
    list_display_links = ("sample_id",)
    list_filter = ("label", "label_source", "conflict", "excluded", "record_kind")
    search_fields = ("record_id",)
    ordering = ("-captured_at",)
    list_select_related = ("reviewed_by",)
    readonly_fields = (
        "thumbnail",
        "sample_id",
        "record_kind",
        "record_id",
        "label",
        "label_source",
        "weight_kg",
        "captured_at",
        "model_orientation",
        "conflict",
        "excluded",
        "removal_pending",
        "reviewed_by",
        "reviewed_at",
        "sent_at",
        "delivered_at",
        "last_error",
        "created_at",
        "updated_at",
    )
    actions = ("mark_front", "mark_rear", "exclude_samples", "exclude_and_remove")

    # Раздел только для владельца; строки меняются лишь действиями списка.
    def has_module_permission(self, request):
        return bool(request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Обычное удаление обошло бы Camera-PC: только через «purge».
        return False

    def get_changelist(self, request, **kwargs):
        return _SampleChangeList

    @admin.display(description="Кадр")
    def thumbnail(self, obj):
        record = getattr(obj, "source_record", _UNLOADED)
        if record is _UNLOADED:
            record = orientation_dataset.load_records([obj]).get((obj.record_kind, obj.record_id))
        url = photo_url(obj.record_kind, record)
        if not url:
            return "—"
        return format_html(
            '<a href="{0}" target="_blank" rel="noopener">'
            '<img src="{0}" alt="" style="height:60px"></a>',
            url,
        )

    def _relabel(self, request, queryset, label):
        count = 0
        for sample in queryset:
            orientation_dataset.set_manual_label(sample, label, request.user)
            count += 1
        self.message_user(request, f"Размечено вручную: {count}.")

    @admin.action(description="Метка: передом")
    def mark_front(self, request, queryset):
        self._relabel(request, queryset, VEHICLE_ORIENTATION_FRONT)

    @admin.action(description="Метка: задом")
    def mark_rear(self, request, queryset):
        self._relabel(request, queryset, VEHICLE_ORIENTATION_REAR)

    @admin.action(description="Исключить")
    def exclude_samples(self, request, queryset):
        count = 0
        for sample in queryset:
            orientation_dataset.exclude_sample(sample, request.user)
            count += 1
        self.message_user(request, f"Исключено из датасета: {count}.")

    @admin.action(description="Исключить и удалить с ПК")
    def exclude_and_remove(self, request, queryset):
        """Исключить выбранное и сразу попросить ПК забыть доставленные копии.

        Удалять строки здесь нельзя: ночной ``collect()`` воссоздал бы их из
        тех же фото. Исключённая строка остаётся и держит кадр вне датасета;
        что ПК не успел забыть сейчас, доберёт ночной ``export_removals``.
        """
        samples = list(queryset)
        for sample in samples:
            orientation_dataset.exclude_sample(sample, request.user)
        result = orientation_dataset.export_removals(limit=len(samples))
        message = (
            f"Исключено из датасета: {len(samples)}, "
            f"стёрто с Camera-PC: {result['removed']}."
        )
        if result["remove_failed"]:
            message += (
                f" Не удалось стереть: {result['remove_failed']} — "
                "кадры остаются исключёнными, ночной экспорт повторит."
            )
        self.message_user(request, message)
