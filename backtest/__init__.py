"""Backtest of the EMS planner with real measurements. Not part of the integration."""

import sys
from pathlib import Path

# the core lives in custom_components/ems/core (that is how HA loads it)
_CORE = str(Path(__file__).resolve().parent.parent / "custom_components")
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
