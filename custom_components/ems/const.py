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

# Executor shown in the app (optional; today the author's YAML package does the switching)
CONF_MODE = "modus"
CONF_APPROVAL = "freigabe"
CONF_APPROVED_UNTIL = "freigabe_bis"
CONF_APPROVE = "freigeben"
CONF_REJECT = "ablehnen"
CONF_SAVINGS = "ersparnis"
CONF_SAVINGS_FORECAST = "ersparnis_prognose"
CONF_LOG = "protokoll"

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
}
"""Entity IDs created by the mkaiser Sungrow Modbus integration. Everything else (prices, PV forecast,
load profile, wear, grid charge power, target) depends on the setup and has no default."""

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
CONTROL_KEYS = (
    CONF_MODE,
    CONF_APPROVAL,
    CONF_APPROVED_UNTIL,
    CONF_APPROVE,
    CONF_REJECT,
    CONF_SAVINGS,
    CONF_SAVINGS_FORECAST,
    CONF_LOG,
)
"""Entities of the executor (mode, approvals, accounting, log). The app shows a section only if its
entity is set. Formats see README (App)."""
ENTITY_KEYS = (*PLANNER_KEYS, *MEASUREMENT_KEYS, *CONTROL_KEYS)
REQUIRED_KEYS = (CONF_PRICES, CONF_SOC)
"""Without these the planner cannot run; all other inputs have fallbacks."""

OPTION_DEFAULTS = {**DEFAULTS, CONF_FEED_IN_CT: 7.0, CONF_RECORDING: True, CONF_MEASUREMENT: True}
