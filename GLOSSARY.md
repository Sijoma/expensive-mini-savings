# Expensive Mini Savings (EMS)

Plans and runs a home battery against a dynamic electricity tariff and a PV forecast, so energy is bought when it is cheap and used when it is expensive.

## Language

**Plan**:
The battery action for every quarter hour from now to the end of the known prices, recomputed every 15 minutes.
_Avoid_: schedule, forecast

**Action**:
What the battery does in a quarter hour: normal operation, hold or grid charge.
_Avoid_: mode, command

**Hold**:
The action that blocks discharging so the stored energy is kept for a more expensive time; PV surplus still charges.
_Avoid_: discharge lock, pause

**Grid charge**:
The action that charges the battery from the grid at a cheap price.
_Avoid_: force charge, Netzladen (in code)

**Wear**:
The assumed cost of every kWh taken out of the battery, counted against each block's savings.
_Avoid_: degradation, Verschleiß (in code)

**Block**:
A run of consecutive quarter hours with the same hold or grid charge action, with its price, quantity and planned savings; the unit that is offered, approved, run and booked. Shown as "Aktion" in the app.
_Avoid_: action (for the run), segment

**Fresh plan**:
A plan whose last calculation succeeded less than 30 minutes ago; without one the battery runs normally.
_Avoid_: current plan, valid plan

**Foreign setting**:
A setting at the inverter that the EMS did not make, e.g. by hand in the inverter app or by the provider; the EMS never overrides it and reports it.
_Avoid_: manual mode, override

**Offer**:
The blocks of the next 24 hours that would be run; they are approved or rejected together.
_Avoid_: proposal, request

**Approval**:
The user's consent to the offered blocks, valid until the next 14:00 or the end of the last block.
_Avoid_: release, Freigabe (in code)

**Booking**:
Crediting a finished block with its planned savings, scaled by the measured quantity.
_Avoid_: accounting, billing
