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

DEFAULTS = {
    CONF_PRICES: "sensor.strompreis_15_min",
    CONF_PV: "sensor.dach_ost_west_power_now",
    CONF_LOAD_PROFILE: "sensor.ems_lastprofil",
    CONF_SOC: "sensor.battery_level",
    CONF_CAPACITY: "sensor.battery_capacity_high_precision",
    CONF_RESERVE: "number.battery_reserved_soc_for_backup",
    CONF_WEAR: "input_number.ems_akku_verschleiss",
    CONF_GRID_CHARGE_POWER: "input_number.ems_netzladeleistung",
    CONF_TARGET: "sensor.ems_akku_soll",
    CONF_LOAD_POWER: "sensor.load_power",
    CONF_PV_POWER: "sensor.total_dc_power",
    CONF_BATTERY_POWER: "sensor.battery_power",
    CONF_GRID_POWER: "sensor.meter_active_power",
    CONF_PRICE_NOW: "sensor.electricity_price",
    CONF_EMS_MODE: "select.ems_mode",
    CONF_FORCED: "select.battery_forced_charge_discharge",
    CONF_DISCHARGE_MAX: "number.battery_max_discharge_power",
}
