"""The run state the tracker works from, independent of where it came from."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    name: str
    hits: int
    pb: int
    duration_ms: int          # in-game time spent in this split so far (0 = unknown)


@dataclass(frozen=True)
class Snapshot:
    profile: str
    attempts: int
    split_active: int          # == len(splits) means the run is complete
    splits: tuple[Split, ...]
    update_time_ms: int        # when this state was received (UTC ms)
    igt_ms: int = 0            # in-game time of the run
    game: str = ""

    @property
    def finished(self) -> bool:
        return len(self.splits) > 0 and self.split_active >= len(self.splits)

    @property
    def total_hits(self) -> int:
        return sum(s.hits for s in self.splits)

    @property
    def titles(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.splits)

    @property
    def started(self) -> bool:
        return self.igt_ms > 0 or self.split_active > 0 or self.total_hits > 0

    @property
    def is_clean(self) -> bool:
        """Freshly reset: first split, no hits, no time."""
        return not self.started
