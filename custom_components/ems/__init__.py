"""EMS – energy management for a home battery (and heat pump PV boost) as a Home Assistant integration.

The core lives in `core/` and does not depend on Home Assistant. The HA parts are only imported
when Home Assistant is installed, so the core tests run without HA.
"""

try:
    from .integration import CONFIG_SCHEMA, async_setup, async_setup_entry, async_unload_entry
except ModuleNotFoundError as err:  # only without Home Assistant (core tests)
    if not (err.name or "").startswith(("homeassistant", "voluptuous")):
        raise
else:
    __all__ = ["CONFIG_SCHEMA", "async_setup", "async_setup_entry", "async_unload_entry"]
