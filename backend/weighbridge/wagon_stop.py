"""One wagon stop under the unloading arch, decided from motion and weight.

Arrival: the arch zone has been still for ``still_seconds`` and the wagon
scale shows a stable full weight for ``stable_seconds``. Departure: the zone
moves again; the exit weight is the last stable reading seen while standing
(``None`` if the zone never re-settled with a stable read before leaving).
Unknown motion (camera PC silent or stale) never moves the machine; once it
has persisted longer than ``motion_max_age`` it marks a later departure as a
motion gap: the previous wagon may have left unseen, so a heavier wagon
appearing in its place also departs it as a gap.
A stalled poll loop is treated the same way: if the collector itself was not
called for longer than ``motion_max_age`` while a wagon stood, the eventual
departure is flagged as a gap too.
"""

from collections.abc import Mapping

from apps.grain.scale import VALID_SCALE_STATES


class StopTracker:
    def __init__(self, *, still_seconds=10.0, stable_seconds=2.0, tolerance=100,
                 empty_max=1000, rise_kg=5000, motion_max_age=5.0):
        self.still_seconds, self.stable_seconds = float(still_seconds), float(stable_seconds)
        self.tolerance, self.empty_max, self.rise_kg = tolerance, empty_max, rise_kg
        self.motion_max_age = float(motion_max_age)
        self.standing = None
        self.last_stable = None
        self.last_exit = None
        self.since = self.weight = None
        self.last_token = None
        self.last_fresh_at = None
        self.last_observed_at = None
        self.motion_state = "unknown"
        self.motion_gap = False
        self.unknown_since = None

    def _motion_state(self, motion):
        """Return ``(state, still_seconds)``; unknown motion never moves the machine."""
        if not isinstance(motion, Mapping) or motion.get("state") not in {"moving", "still"}:
            return "unknown", 0.0
        age_raw = motion.get("sample_age_seconds")
        if age_raw is None:
            # The camera PC reports null before its first sample; that is
            # not evidence of freshness, so treat it as unknown motion.
            return "unknown", 0.0
        try:
            age = float(age_raw)
            still_seconds = float(motion.get("still_seconds") or 0.0)
        except (TypeError, ValueError):
            return "unknown", 0.0
        if age > self.motion_max_age:
            return "unknown", 0.0
        return motion["state"], still_seconds

    def observe(self, observation, motion, now):
        previously_observed_at = self.last_observed_at
        self.last_observed_at = now
        if self.standing is not None and previously_observed_at is not None \
                and now - previously_observed_at > self.motion_max_age:
            # The poll loop itself stalled while a wagon stood here: it may
            # have left unseen during the blind interval.
            self.motion_gap = True
        state, still_seconds = self._motion_state(motion)
        self.motion_state = state
        valid = observation.state in VALID_SCALE_STATES and observation.weight_kg is not None
        token = observation.updated_at if valid else None
        fresh = bool(valid and token and token != self.last_token)
        previously_fresh_at = self.last_fresh_at
        if fresh:
            self.last_token = token
            self.last_fresh_at = now
        weight = float(observation.weight_kg) if valid else None
        if state == "unknown":
            if self.unknown_since is None:
                self.unknown_since = now
        else:
            self.unknown_since = None
        if self.standing is not None:
            if self.unknown_since is not None and now - self.unknown_since > self.motion_max_age:
                # A single silent poll proves nothing; motion unreadable for
                # longer than a sample's lifetime means the wagon could have
                # left unseen in between.
                self.motion_gap = True
            if fresh and observation.stable and weight > self.empty_max:
                if weight > float(self.standing["weight_kg"]) + self.rise_kg or (
                    self.last_stable is not None and weight > self.last_stable[0] + self.rise_kg
                ):
                    # A heavier wagon stands here: the previous one left unseen.
                    return self._depart(gap=True)
                self.last_stable = (int(round(weight)), observation.updated_at, now)
            if state == "moving":
                return self._depart(gap=self.motion_gap)
            return None
        if state != "still":
            self.since = self.weight = None
            return None
        if still_seconds < self.still_seconds:
            # The still counter itself restarted: the zone has moved, so any
            # open stability window is stale and must restart too.
            self.since = self.weight = None
            return None
        if not fresh:
            # A repeated token carries no new evidence of stillness: it earns
            # no time credit, but it must not erase progress already made,
            # or a slow-refreshing scale under a 1Hz poll would never arrive.
            return None
        if not observation.stable or weight <= self.empty_max:
            self.since = self.weight = None
            return None
        if previously_fresh_at is not None and \
                now - previously_fresh_at > max(self.stable_seconds, self.motion_max_age):
            # Fresh evidence itself had a gap wider than we tolerate: it
            # cannot vouch for what happened during the gap. The threshold is
            # never below motion_max_age, or a scale whose token refreshes
            # slower than stable_seconds would restart the window forever.
            self.since, self.weight = now, weight
            return None
        if self.since is None or abs(weight - self.weight) > self.tolerance:
            self.since, self.weight = now, weight
            return None
        if now - self.since >= self.stable_seconds:
            return ("arrival", weight)
        return None

    def _depart(self, *, gap):
        stop, exit_weight = self.standing, self.last_stable[0] if self.last_stable else None
        # The collector timestamps the exit weight from the reading it came
        # from, so keep that reading after the machine forgets it.
        self.last_exit = self.last_stable
        self.standing = self.last_stable = None
        self.since = self.weight = None
        self.motion_gap = False
        return ("departure", stop, exit_weight, bool(gap))

    def arrived(self, stop, now):
        # The exit weight is whatever stable reading is seen after this one;
        # the arrival (full) weight itself is never carried forward as an
        # exit candidate. `now` is also not used to seed last_observed_at:
        # a scale poll can legitimately lag the arrival decision by more
        # than motion_max_age, which would otherwise flag a false stall on
        # the very next observe() call (arrived() does not count as a poll).
        self.standing = stop
        self.last_stable = self.last_exit = None
        self.since = self.weight = None
        self.motion_gap = False

    def arrival_rejected(self):
        self.since = self.weight = None
