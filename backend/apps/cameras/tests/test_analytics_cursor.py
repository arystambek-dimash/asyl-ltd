"""Курсор 24/7-счётчика не должен наматывать лишние мешки.

Курсор один на камеру, а снимки приходят из двух источников: монитор
(continuous.reconcile, свежие данные) и страница аналитики. Если курсор
двигать чужим или устаревшим снимком, следующая свежая разница уходит в
дневной итог второй раз.
"""
import pytest

from apps.cameras import analytics
from apps.cameras.models import AlwaysOnCounterCursor, AlwaysOnDailyAnalytics

pytestmark = pytest.mark.django_db


def _snapshot(total, *, mode="always_on", running=True, camera="cam3", colors=None):
    return {"processors": [{
        "cam": camera, "total": total, "mode": mode, "running": running,
        "per_color": colors or {},
    }]}


def _model_total(camera="cam3"):
    row = AlwaysOnDailyAnalytics.objects.filter(camera=camera).first()
    return row.model_total if row else 0


def test_stale_snapshot_does_not_rewind_the_cursor():
    """Устаревший снимок не отматывает курсор: иначе разница считается дважды.

    Страница аналитики читает кэш (до 5 с), монитор — свежие данные. Раньше
    сценарий «GET 100 → монитор 140 → GET из кэша 100 → монитор 140» давал
    240 вместо 140.
    """
    analytics.record_snapshot(_snapshot(100))
    analytics.record_snapshot(_snapshot(140))
    assert _model_total() == 140

    # Тот же самый снимок приходит повторно (устаревший кэш).
    analytics.record_snapshot(_snapshot(100))
    analytics.record_snapshot(_snapshot(140))

    assert _model_total() == 140


def test_session_mode_does_not_clobber_the_always_on_cursor():
    """Сессионная погрузка на той же камере не сбивает 24/7-счёт.

    Счётчик сессии стартует с нуля. Если он перезапишет курсор 24/7, то
    возобновлённый фоновый счёт попадёт в итог целиком.
    """
    analytics.record_snapshot(_snapshot(100))
    assert _model_total() == 100

    # Оператор начал погрузку: режим session, счётчик пошёл с нуля.
    analytics.record_snapshot(_snapshot(40, mode="session"))
    # 24/7 возобновился и продолжает свой счёт со 130.
    analytics.record_snapshot(_snapshot(130))

    assert _model_total() == 130


def test_stopped_processor_does_not_reset_the_cursor():
    """Остановленный воркер с total=0 не обнуляет базу отсчёта."""
    analytics.record_snapshot(_snapshot(100))
    analytics.record_snapshot(_snapshot(0, running=False))
    analytics.record_snapshot(_snapshot(120))

    assert _model_total() == 120


def test_restart_from_zero_is_still_counted():
    """Честный перезапуск воркера с нуля не теряет последующие мешки."""
    analytics.record_snapshot(_snapshot(100))
    analytics.record_snapshot(_snapshot(0))
    analytics.record_snapshot(_snapshot(30))

    assert _model_total() == 130
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_total == 30
