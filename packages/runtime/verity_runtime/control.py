"""Stopping a run that is already going: the kill switch, and budgets.

The other controls in this package answer "should this be allowed to start".
These two answer "stop, now", and they exist because the first kind is never
complete. A policy is written before anyone knows what the run will meet, and
the moment you need to stop something is the moment you have discovered
something the policy did not anticipate.

Both are checked between steps rather than inside them. That bounds how long a
stop takes by the length of one step, which is honest and testable, and it
avoids the alternative -- interrupting a connector mid-call and having no idea
whether the write landed. A run that is stopped must know what it did.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class Clock(Protocol):
    """A source of monotonically increasing seconds."""

    def __call__(self) -> float: ...


@runtime_checkable
class KillSwitch(Protocol):
    """Asked between steps whether the run should stop.

    Returns the reason as well as the decision, because a run that stops
    without saying why is indistinguishable from one that crashed.
    """

    def pulled(self) -> tuple[bool, str]: ...


class NeverPulled:
    """The default. Nothing is asking this run to stop."""

    def pulled(self) -> tuple[bool, str]:
        return False, ""


@dataclass
class Stoppable:
    """A kill switch another part of the process can pull.

    Used by a supervising process, by tests, and by anything that has a handle
    on the run object itself.
    """

    reason: str = ""
    _pulled: bool = False

    def pull(self, reason: str = "stopped by an operator") -> None:
        self._pulled = True
        self.reason = reason

    def pulled(self) -> tuple[bool, str]:
        return self._pulled, self.reason


@dataclass
class FileKillSwitch:
    """Stops the run when a file appears.

    The crudest possible mechanism, chosen deliberately: an operator with a
    shell can stop a run without needing the process, a client, a network
    path, or any part of Verity to still be working. Controls that depend on
    the healthy operation of the thing they are meant to stop are not
    controls.
    """

    path: Path
    reason: str = "a stop file was created"

    def pulled(self) -> tuple[bool, str]:
        return (True, f"{self.reason}: {self.path}") if self.path.exists() else (False, "")


@dataclass(frozen=True)
class Budget:
    """Limits a run may not exceed. Exceeding one stops the run; it never truncates.

    ``0`` means no limit, and is the default for everything except steps.
    Being explicit about that matters: a budget silently defaulting to some
    small number would make runs fail for reasons nobody chose.
    """

    max_steps: int = 200
    max_seconds: float = 0.0
    max_writes: int = 0

    def exceeded_by_steps(self, planned: int) -> str:
        if self.max_steps and planned > self.max_steps:
            return f"the plan has {planned} steps, above the limit of {self.max_steps}"
        return ""

    def exceeded_by_time(self, elapsed: float) -> str:
        if self.max_seconds and elapsed > self.max_seconds:
            return f"the run has taken {elapsed:.1f}s, above the limit of {self.max_seconds:.1f}s"
        return ""

    def exceeded_by_writes(self, performed: int) -> str:
        if self.max_writes and performed >= self.max_writes:
            return f"the run has written {performed} times, at the limit of {self.max_writes}"
        return ""


@dataclass
class Stopwatch:
    """Elapsed wall time for one run, from a monotonic source."""

    started: float = 0.0
    now: Clock = time.monotonic

    def start(self) -> None:
        self.started = self.now()

    @property
    def elapsed(self) -> float:
        return self.now() - self.started


__all__ = [
    "Budget",
    "FileKillSwitch",
    "KillSwitch",
    "NeverPulled",
    "Stoppable",
    "Stopwatch",
]
