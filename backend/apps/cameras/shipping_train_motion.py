"""Wagon change on a loading conveyor, seen as the train moving on the number camera.

The camera PC's arch-motion monitor watches the wagon side on the number
camera. A train taking a wagon away or pushing one in moves the whole zone for
tens of seconds; jolts, people and door alignment of the same wagon last
seconds. Such a change splits loading even when it took under the idle
timeout. It only adds boundaries: pauses still split as before, and number
identity still merges the parts of one wagon.
"""
import logging
import time
from dataclasses import dataclass

from apps.common.datetimes import parse_aware_datetime

from . import ai
from .models import ShippingTrainMove, ShippingTransportCamera

log = logging.getLogger(__name__)

# Measured on cam12 25.09.2026 (longest stretch with at least half the zone moving):
# a wagon taken away 25.8 and 56.2 s, a wagon pushed in 19.8 s, trains passing
# 39-40.5 s; the same wagon aligned at the door 3.5 and 6.5 s and shifted by a
# few metres mid-loading 8.8 and 12.0 s. A missed change only leaves the idle
# rule, while a false one splits a wagon whose shifted side may show no number,
# so the bar sits near the shortest real change. Optical-flow travel is not
# usable (the ribs of the wagon side alias it), nor is the mean fraction of an
# episode (weak activity around a real move dilutes it).
WAGON_CHANGE_MIN_HIGH_SECONDS = 18.0
WARNING_INTERVAL_SECONDS = 600
MEASURING = {"online", "awaiting_comparison"}
_last_warning = {}


def train_camera(recognition_model, number_camera):
    """Number camera whose train movements bound this conveyor's wagons, or None."""
    return number_camera if recognition_model == "wagon_number" and number_camera else None


def _parse_move(value):
    if not isinstance(value, dict):
        return None
    started, stopped = parse_aware_datetime(value.get("started_at")), parse_aware_datetime(value.get("stopped_at"))
    numbers = [value.get(name) for name in ("seconds", "high_seconds", "mean_fraction")]
    if (
        started is None or stopped is None or stopped < started
        or not all(isinstance(number, (int, float)) and not isinstance(number, bool) for number in numbers)
    ):
        return None
    seconds, high_seconds, mean_fraction = (float(number) for number in numbers)
    return {
        "started_at": started, "stopped_at": stopped, "seconds": seconds, "high_seconds": high_seconds,
        "mean_fraction": mean_fraction, "interrupted": value.get("interrupted") is True,
    }


def _warn(camera, message, *args):
    now = time.monotonic()
    if now - _last_warning.get(camera, float("-inf")) >= WARNING_INTERVAL_SECONDS:
        _last_warning[camera] = now
        log.warning(message, *args)


def poll(conveyor_camera):
    """Record the train movements of this conveyor's number camera. Never raises on I/O."""
    binding = ShippingTransportCamera.objects.filter(conveyor_camera=conveyor_camera).first()
    camera = train_camera(binding.recognition_model, binding.number_camera) if binding else None
    if camera is None:
        return 0
    try:
        status = ai.arch_motion(camera)
    except (ai.AiUnavailable, ai.AiError) as exc:
        _warn(camera, "Движение поезда на %s недоступно: %r", camera, exc)
        return 0
    moves = status.get("moves") if isinstance(status, dict) else None
    if not isinstance(moves, list):
        _warn(camera, "ПК камер не отдаёт движения поезда на %s", camera)
        return 0
    if status.get("status") not in MEASURING:
        # No zone, a zone for another stream, no frames: wagons split by pauses only.
        _warn(camera, "Движение поезда на %s не измеряется: %s", camera, status.get("status"))
    parsed = [move for move in map(_parse_move, moves) if move is not None]
    if len(parsed) < len(moves):
        _warn(camera, "Пропущены некорректные движения поезда на %s", camera)
    ShippingTrainMove.objects.bulk_create(
        [ShippingTrainMove(camera=camera, **move) for move in parsed], ignore_conflicts=True,
    )
    return len(parsed)


@dataclass(frozen=True)
class WagonChanges:
    """Wagon changes recorded on one number camera, as (started_at, stopped_at)."""

    moves: tuple

    def after(self, segment, until):
        """Whether the train changed the segment's wagon after its last bag, by ``until``.

        A change counts when it stopped then; a movement already under way when
        the segment began (a wagon still creeping in) does not end it.
        """
        return any(
            segment.last_counted_at < stopped <= until and started > segment.started_at
            for started, stopped in self.moves
        )


def load_changes(camera, since):
    """Wagon changes on ``camera`` that stopped after ``since``, or None without a train camera."""
    if camera is None:
        return None
    return WagonChanges(tuple(ShippingTrainMove.objects.filter(
        camera=camera, stopped_at__gt=since, high_seconds__gte=WAGON_CHANGE_MIN_HIGH_SECONDS,
    ).order_by("stopped_at").values_list("started_at", "stopped_at")))
