"""Constants of the integration.

Configuration keys (configuration.yaml → ems:) are German; they are part of the author's setup."""

DOMAIN = "ems"

# Inputs
CONF_PRICES = "preise"
CONF_PV = "pv_prognose"
CONF_LOAD_PROFILE = "lastprofil"
CONF_SOC = "soc"
CONF_CAPACITY = "kapazitaet"
CONF_RESERVE = "reserve"
CONF_WEAR = "verschleiss"
CONF_GRID_CHARGE_POWER = "netzladeleistung"
CONF_TARGET = "soll"
CONF_FEED_IN_CT = "einspeisung_ct"
CONF_RECORDING = "aufzeichnung"
CONF_MEASUREMENT = "messung"

# Measurements recorded per quarter hour (measurement.py)
CONF_LOAD_POWER = "last_leistung"
CONF_PV_POWER = "pv_leistung"
CONF_BATTERY_POWER = "akku_leistung"
CONF_GRID_POWER = "netz_leistung"
CONF_PRICE_NOW = "preis_jetzt"
CONF_EMS_MODE = "ems_modus"
CONF_FORCED = "erzwungen"
CONF_DISCHARGE_MAX = "entladen_max"

# Executor: switches the battery by the plan (mode, approvals, booking, log live in the integration)
CONF_FORCED_POWER = "ladeleistung_erzwungen"
CONF_CHARGED = "akku_ladung"
CONF_CHARGED_PV = "akku_ladung_pv"
CONF_IMPORTED = "netzbezug"
CONF_CONSUMED = "verbrauch"
CONF_NOTIFY = "benachrichtigung"
"""Notify service for approval requests and alerts, e.g. notify.my_phone. Without it no push."""
CONF_WEAR_CT = "verschleiss_ct"
CONF_GRID_KW = "netzladeleistung_kw"
"""Fixed values in configuration.yaml: then wear / grid charge power cannot be changed in the app."""

LEGACY_CONTROL_KEYS = (
    "modus",
    "freigabe",
    "freigabe_bis",
    "freigeben",
    "ablehnen",
    "ersparnis",
    "ersparnis_prognose",
    "protokoll",
)
"""Entities of the former YAML executor (up to 0.5). Still accepted in configuration.yaml, ignored."""

DEFAULTS = {
    CONF_SOC: "sensor.battery_level",
    CONF_CAPACITY: "sensor.battery_capacity_high_precision",
    CONF_RESERVE: "number.battery_reserved_soc_for_backup",
    CONF_LOAD_POWER: "sensor.load_power",
    CONF_PV_POWER: "sensor.total_dc_power",
    CONF_BATTERY_POWER: "sensor.battery_power",
    CONF_GRID_POWER: "sensor.meter_active_power",
    CONF_EMS_MODE: "select.ems_mode",
    CONF_FORCED: "select.battery_forced_charge_discharge",
    CONF_DISCHARGE_MAX: "number.battery_max_discharge_power",
    CONF_FORCED_POWER: "number.battery_forced_charge_discharge_power",
    CONF_CHARGED: "sensor.total_battery_charge",
    CONF_CHARGED_PV: "sensor.total_battery_charge_from_pv",
    CONF_IMPORTED: "sensor.total_imported_energy",
    CONF_CONSUMED: "sensor.total_consumed_energy",
}
"""Entity IDs created by the mkaiser Sungrow Modbus integration. Prices and PV forecast depend on the setup
and have no default. Load profile, wear, grid charge power and target are the integration's own entities
unless set."""

# Order of the fields in the setup dialog
PLANNER_KEYS = (
    CONF_PRICES,
    CONF_PV,
    CONF_LOAD_PROFILE,
    CONF_SOC,
    CONF_CAPACITY,
    CONF_RESERVE,
    CONF_WEAR,
    CONF_GRID_CHARGE_POWER,
    CONF_TARGET,
)
MEASUREMENT_KEYS = (
    CONF_LOAD_POWER,
    CONF_PV_POWER,
    CONF_BATTERY_POWER,
    CONF_GRID_POWER,
    CONF_PRICE_NOW,
    CONF_EMS_MODE,
    CONF_FORCED,
    CONF_DISCHARGE_MAX,
)
EXECUTOR_KEYS = (CONF_FORCED_POWER, CONF_CHARGED, CONF_CHARGED_PV, CONF_IMPORTED, CONF_CONSUMED)
"""Inverter setting and meters the executor uses (it switches via ems_modus, erzwungen, entladen_max)."""
ENTITY_KEYS = (*PLANNER_KEYS, *MEASUREMENT_KEYS, *EXECUTOR_KEYS)
REQUIRED_KEYS = (CONF_PRICES, CONF_SOC)
"""Without these the planner cannot run; all other inputs have fallbacks."""

OPTION_DEFAULTS = {**DEFAULTS, CONF_FEED_IN_CT: 7.0, CONF_RECORDING: True, CONF_MEASUREMENT: True}

SIGNAL_EXECUTOR = f"{DOMAIN}_executor_update"
"""Dispatcher signal: the executor's state changed (entities update)."""
