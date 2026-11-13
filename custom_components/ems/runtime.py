"""What a loaded config entry holds (entry.runtime_data)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .coordinator import EmsCoordinator
    from .executor import Executor
    from .settings import PlannerSettings


@dataclass
class EmsRuntime:
    conf: dict[str, Any]
    coordinator: EmsCoordinator
    executor: Executor
    settings: PlannerSettings
