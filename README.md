# expensive-mini-savings

A home battery planner for Home Assistant: it decides every 15 minutes whether the battery should run
normally, **hold** its charge for later, or **charge from the grid**, based on a dynamic electricity tariff
(15-minute prices), a PV forecast and the expected consumption. It also plans a heat pump **PV boost**
with surplus that would otherwise be exported.

The name is honest: on a 10 kWh battery with a dynamic tariff the savings are real but small
(in the author's house about 13 % of the energy cost over the first ten days in the backtest).

> **Status: early, built for one house.** Tested with a Sungrow SH15T hybrid inverter and SBH100
> battery (via the [mkaiser Modbus integration](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant)),
> Tibber 15-minute prices and the Helios PV forecast. Other setups work if they provide the [inputs](#inputs)
> in the same format.

- **It only plans.** It creates sensors with the plan. Switching the inverter is up to you (an automation
  that follows `sensor.ems_plan_akku_neu`); nothing is ever written to your devices.
- **German names.** Configuration keys (`preise`, `soc`, …), attribute names and the states and reasons
  shown in Home Assistant are German, because the author's automations and dashboard depend on them.
  The setup dialog is translated (English, German).
- **Comes with an app**: an *EMS* page in the sidebar, see [App](#app).
- Issues and pull requests for other inverters, price sources or forecasts are welcome.

## How it works

1. **Slots.** One quarter hour each, from now to the end of the known prices: price, PV forecast
   (85 % mean + 15 % P10, converted DC → AC), expected consumption (learned hourly profile × 1.1) and a
   **spike**: power that flows back and forth within the quarter hour (heat pump starting, cooking,
   clouds, forecast errors).
2. **Simulation** (`core/simulation.py`). Battery physics with gross flows, measured efficiencies
   (PV → battery 98 %, grid → battery 96.1 %, battery → house 97.5 %) and the inverter's standby draw
   (98 W while discharging without PV). *Hold* is implemented as discharge limit ≈ 0, so PV keeps charging.
3. **Optimizer** (`core/optimizer.py`). Dynamic programming over battery content × time: per quarter hour
   the cheapest of *normal*, *hold*, *grid charge*, including wear (ct per kWh taken out), a residual value
   for the energy left at the end, a 30-minute minimum hold and a small fee per switch against toggling.
   Runs in ~10–20 ms on a laptop, ~0.2 s on a Raspberry Pi 4.
4. **Explanation** (`core/explanation.py`). Action blocks with savings (recomputed against the same plan
   without the block), the time the stored energy is used, and the **value of one kWh in the battery**
   (slope of the value function). The battery holds when that value is higher than what the kWh yields now.
5. **Heat pump** (`core/heat_pump.py`). PV boost only with surplus that the battery plan would export.

The integration only **plans**. Switching the inverter, approvals and accounting live in the author's
YAML package, which reads the plan sensor.

## Layout

```
custom_components/ems/        the Home Assistant integration (copy this folder to /config/custom_components/)
  core/                       plain Python, no Home Assistant
    model.py                  Battery, Slot, Tariff, Action, minimum hold
    simulation.py             physics and cost per quarter hour and per plan, energy flows
    optimizer.py              dynamic programming
    explanation.py            blocks, savings, reasons
    forecast.py               DC → AC, spikes
    heat_pump.py              PV boost
  adapter.py                  HA states → slots, plan → attributes (no HA import, testable)
  integration.py              setup from configuration.yaml, triggers
  coordinator.py              reads inputs, plans in a worker thread, hourly recording of inputs
  recorder.py, measurement.py measurements per quarter hour → /config/ems/messung/
  sensor.py                   plan, value of one kWh, heat pump plan
  panel.py, history.py        the app: sidebar panel, websocket commands, plan against reality
backtest/                     not part of the integration
  export.py                   fetch raw history from HA and aggregate per quarter hour
  fetch.py                    fetch the integration's recordings
  run.py                      backtest: replan every 15 min, execute against measurements
examples/inputs.yaml          template sensors for prices and load profile
frontend/                     the app (TypeScript, Lit, ECharts) → custom_components/ems/frontend/
tests/                        pytest + hypothesis; tests/ha runs in a test Home Assistant
  example/                    made-up example inputs (generate.py)
```

## Installation

In Home Assistant: **Settings → Marketplace → Add from link** (HACS: *Custom repositories*, type
*Integration*) with `https://github.com/Sijoma/expensive-mini-savings`, install *Expensive Mini Savings*,
add the configuration below and restart. New releases then show up as an update.

Manually: copy `custom_components/ems` to `/config/custom_components/ems`.

Releases: bump `version` in `custom_components/ems/manifest.json` and push a tag `vX.Y.Z`
(`vX.Y.Z-beta.N` for a pre-release); the release workflow runs the tests and creates the release.

## Setup

Either in the UI: **Settings → Devices & services → Add integration → Expensive Mini Savings**. The dialog
asks for the input entities (only prices and state of charge are required) and the recording options;
change them later with *Configure*.

Or in `configuration.yaml` (imported into the same entry on every start; YAML then takes precedence and
*Configure* only shows the settings):

```yaml
ems:
  preise: sensor.electricity_prices       # required
  pv_prognose: sensor.solar_forecast_power_now
  lastprofil: sensor.load_profile
  einspeisung_ct: 7                       # feed-in compensation in ct/kWh
  # the Sungrow entities below have defaults and can be overridden the same way
```

### Inputs

Only `preise` and `soc` are required; every other input falls back to the value in the last column.
Defaults exist only for the entities the mkaiser Sungrow integration creates.

| Key | What | Format | Default entity | Without it |
|---|---|---|---|---|
| `preise` | prices per quarter hour, today and (from ~13:00) tomorrow; the plan reaches as far as the prices | attribute `preise` = `[[start in ms since epoch, ct/kWh], …]`, gross price incl. all fees | – | no plan |
| `soc` | battery state of charge | state in %, **usable range** (0 % = the inverter's min SoC) | `sensor.battery_level` | no plan |
| `pv_prognose` | PV forecast | attribute `forecast` = `[{"datetime": ISO time, "watts": W, "p10": W}, …]` per 15 min, DC (as Helios provides it); `p10` optional | – | no PV |
| `lastprofil` | expected house consumption per hour of day | attribute `profil` = 24 values in W, index = local hour | – | 500 W flat |
| `kapazitaet` | usable battery capacity | state in kWh | `sensor.battery_capacity_high_precision` | 10.13 kWh |
| `reserve` | backup reserve, lower bound for the plan | state in % (same scale as `soc`) | `number.battery_reserved_soc_for_backup` | 0 % |
| `verschleiss` | battery wear | state in ct per kWh taken out | – | 10 ct |
| `netzladeleistung` | power when charging from the grid | state in kW (max. 5) | – | 5 kW |
| `soll` | what your automation currently applies, so a running hold is not toggled | state `Eigenverbrauch`, `Entladung gesperrt` or `Laden aus dem Netz` | – | assumes normal |
| `einspeisung_ct` | feed-in compensation | number in ct/kWh | – | 7 |

[`examples/inputs.yaml`](examples/inputs.yaml) has template sensors for `preise` (from the Tibber integration)
and `lastprofil` (learned from a total energy meter).

Measurements (`messung: true`, written per quarter hour to `/config/ems/messung/`, used for backtests):
`last_leistung` (`sensor.load_power`), `pv_leistung` (`sensor.total_dc_power`), `akku_leistung`
(`sensor.battery_power`, + discharging), `netz_leistung` (`sensor.meter_active_power`, + import), all in W;
`preis_jetzt` (current price, no default); `ems_modus`, `erzwungen`, `entladen_max` (inverter mode,
forced mode, discharge limit; Sungrow defaults). `aufzeichnung: true` additionally stores the inputs and the
plan once per hour in `/config/ems/aufzeichnung/`.

### Entities

| Entity | State | Main attributes |
|---|---|---|
| `sensor.ems_plan_akku_neu` | what the battery should do now (`Eigenverbrauch`, `Entladung gesperrt`, `Laden aus dem Netz`) | `grund` (reason), `aktionen` (blocks with time, kWh, price, savings, reason), `slots` (table per quarter hour, see `adapter.plan_attributes`), `ersparnis_eur`, `berechnet`, `rechenzeit_ms` |
| `sensor.ems_wert_akku` | value of one kWh in the battery now, ct | – |
| `sensor.ems_plan_warmepumpe_neu` | `Normal` or `PV-Boost` | `aktionen` (boost periods) |

### App

The integration adds an **EMS** page to the Home Assistant sidebar (also in the companion app). Nothing to
install or configure; it updates with the integration.

| Tab | Shows |
|---|---|
| Plan | what the battery does now and why, value of 1 kWh in the battery, one chart with price (colored by action), SoC with and without plan, PV, load and heat pump boost; planned actions with savings and reasons |
| Ersparnis | measured savings today / week / month, per day against the forecast, booked actions |
| Verlauf | plan against reality per day: planned and executed action, measured SoC, grid import and its cost |
| Protokoll | decisions of the executor; logbook of mode, target, inverter and approvals with who caused each change (inverter changes without an EMS action shortly before are marked as external, e.g. VPP); state of every configured entity |
| Einstellungen | wear and grid charge power, load profile, how the planner calculates, the configuration |

Mode, approvals, savings and log come from the automation that switches the battery (the author's YAML
package). Set their entities in the setup dialog (step *App*) or in YAML; sections without an entity are
hidden:

| Key | Entity | Format |
|---|---|---|
| `modus` | `input_select` | options `Nur planen`, `Mit Freigabe`, `Automatisch` |
| `freigabe` | `binary_sensor` | `on` while an approval is pending; attribute `aktionen` = plan blocks (`von`, `bis`, `was`, `kwh`, `ct`, `spart`, `grund`) |
| `freigabe_bis`, `freigeben`, `ablehnen` | `input_datetime`, `script`, `script` | approved until; the buttons run the scripts |
| `ersparnis`, `ersparnis_prognose` | `sensor` (EUR, `state_class: total`) | attribute `aktionen` = `[{start, ende, was, kwh, betrag}]` |
| `protokoll` | `sensor` | attribute `eintraege` = `[{zeit, art, text, modus, soc, preis}]`, newest first |

The app is TypeScript with [Lit](https://lit.dev) and [ECharts](https://echarts.apache.org) in `frontend/`,
built into `custom_components/ems/frontend/ems-panel.js` (`npm ci && npm run build`; CI checks that the
shipped file matches the source).

## Tests

```bash
uv sync
uv run pytest                                    # core, adapter, measurement, properties
uv run --group ha pytest tests/ha -o addopts=""  # the integration inside a test Home Assistant
uv run pytest --hypothesis-profile=thorough      # property tests with 2000 examples each
```

The test pyramid:

| Level | What | Where |
|---|---|---|
| physics | single quarter hours with hand-computed numbers | `test_simulation.py` |
| properties | hypothesis: energy balance, SoC bounds, hold never discharges, vector = scalar | `test_simulation.py` |
| scenarios | small days with an obvious right answer (hold or not, charge or not) | `test_optimizer.py` |
| optimality | for ≤ 6 quarter hours compare with brute force over all 3ⁿ plans; never worse than no control | `test_optimizer.py` |
| contract | attribute keys and `slots` format the dashboard and YAML depend on | `test_adapter.py` |
| integration | setup, sensors, replanning on new prices, recording, missing inputs | `tests/ha/` |
| reality, backtest | replay measured days, compare simulated and measured SoC; savings per day | `test_reality.py`, `test_backtest.py` |

The reality and backtest tests and a few real-case tests need measurement data from a house. That data
is private and not in this repository, so those tests are skipped here.

## Calibrating to your own system

`backtest/export.py` aggregates the raw HA history per quarter hour (gross import and surplus, battery
and grid flows, SoC, price, inverter mode). With a few days of data, `tests/test_reality.py` shows how
well the simulation follows the measured SoC, and `backtest/run.py` how much the planner would have saved.
The efficiencies in `core/model.py` were fitted this way; yours will differ.

## Licence

MIT, see [LICENSE](LICENSE).
