# expensive-mini-savings

A home battery planner for Home Assistant: it decides every 15 minutes whether the battery should run
normally, **hold** its charge for later, or **charge from the grid**, based on a dynamic electricity tariff
(15-minute prices), a PV forecast and the expected consumption. It also plans a heat pump **PV boost**
with surplus that would otherwise be exported.

The name is honest: on a 10 kWh battery with a dynamic tariff the savings are real but small
(in the author's house about 13 % of the energy cost over the first ten days in the backtest).

> **Status: personal project.** It runs in one house (Sungrow SH15T + SBH100 battery via the
> [mkaiser Modbus integration](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant),
> Tibber prices, Helios PV forecast). Default entity IDs, configuration keys, attribute names and the texts
> shown in Home Assistant are German. It is shared as a reference, not as a ready-made integration.
> Pull requests that make it more general are welcome.

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
backtest/                     not part of the integration
  export.py                   fetch raw history from HA and aggregate per quarter hour
  fetch.py                    fetch the integration's recordings
  run.py                      backtest: replan every 15 min, execute against measurements
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

```yaml
# configuration.yaml
ems:
  einspeisung_ct: 7          # feed-in compensation in ct/kWh
  # every input entity can be overridden, see custom_components/ems/const.py (DEFAULTS)
```

Inputs it expects (attribute names as in the author's setup):

| Input | Default entity | Format |
|---|---|---|
| prices | `sensor.strompreis_15_min` | attribute `preise` = `[[ms, ct], …]` |
| PV forecast | `sensor.dach_ost_west_power_now` | attribute `forecast` = `[{datetime, watts, p10}, …]` per 15 min (Helios) |
| load profile | `sensor.ems_lastprofil` | attribute `profil` = 24 values in W |
| SoC, capacity, reserve | Sungrow entities | `sensor.battery_level` (usable range 0–100 %) |

Entities it creates: `sensor.ems_plan_akku_neu` (plan; attributes `aktionen`, `grund`, `slots`, …),
`sensor.ems_wert_akku` (value of one kWh in the battery, ct), `sensor.ems_plan_warmepumpe_neu`.
The `slots` attribute is a table per quarter hour, see `adapter.plan_attributes`.

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
