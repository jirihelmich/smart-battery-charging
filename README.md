# Smart Battery Charging

A Home Assistant custom integration for automated battery charging during cheapest electricity hours, based on solar forecast and consumption patterns.

**Inverter-agnostic** — works with any inverter that exposes mode select and charge command entities in Home Assistant (Solax, Sungrow, Huawei, etc.).

## Features

- Dynamic charging window calculation (1-6 hours based on energy deficit)
- Cheapest price window selection within configurable night hours
- Solar forecast integration with error correction (7-day sliding window)
- Consumption tracking with sliding window average (7-day)
- Price threshold checking with configurable maximum
- All settings exposed as number entities (controllable from dashboard/automations)
- JSON-based persistence (replaces fragile YAML input_text hacks)
- Pure logic modules with full unit test coverage

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository
2. Search for "Smart Battery Charging" and install
3. Restart Home Assistant
4. Go to Settings > Devices & Services > Add Integration > Smart Battery Charging

### Manual

1. Copy `custom_components/smart_battery_charging/` to your HA `custom_components/` directory
2. Restart Home Assistant
3. Add the integration via Settings > Devices & Services

## Configuration

The integration uses an 8-step config flow:

1. **Name** — Instance name
2. **Inverter Template** — Pick your inverter integration (Solax, GoodWe, SolarEdge, Huawei, or Custom) to pre-fill mode strings and entity hints
3. **Inverter Entities** — SOC sensor, capacity sensor, mode select, charge command, etc.
4. **Inverter Values** — Option strings for each mode (pre-filled from template, or auto-populated from entity)
5. **Price Sensor** — Spot electricity price sensor with hourly attributes
6. **Solar Forecast** — Today/tomorrow forecast sensors (supports multiple orientations)
7. **Consumption** — Daily consumption sensor (resets at midnight)
8. **Settings** — Battery capacity, SOC limits, charge power, price threshold, etc.

All settings from step 7 can be changed at runtime via the options flow or directly through the number entities.

## Entities Created

### Sensors (17)
| Entity | Description |
|--------|-------------|
| Average Daily Consumption | 7-day sliding window average |
| Today/Tomorrow Solar Forecast | Combined forecast (all orientations) |
| Solar Forecast Error Average | 7-day error tracking (%) |
| Today Solar Forecast Error | Live forecast vs actual |
| Tomorrow Energy Forecast | Adjusted solar minus consumption |
| Battery Charge kWh | Current charge in kWh |
| Battery Usable Charge | Charge above minimum SOC |
| Battery Capacity to Max | Remaining to configured max |
| Night Charging Status | Idle/Scheduled/Charging/Complete/Disabled |
| Last Night Charge kWh | SOC delta converted to kWh |
| Last Charge Battery Range | Start% → End% |
| Last Charge Time Range | HH:MM–HH:MM |
| Last Charge Total Cost | kWh × avg price |
| Electricity Price Status | Very Cheap/Cheap/Normal/Expensive |
| Today/Tomorrow Cheapest Hours | Top 3 cheapest hours |

### Binary Sensors (2)
| Entity | Description |
|--------|-------------|
| Charging Active | Currently force-charging |
| Charging Recommended | Price below threshold and SOC below max |

### Number Entities (6)
| Entity | Description |
|--------|-------------|
| Battery Capacity | kWh |
| Max Charge Level | % |
| Min SOC | % |
| Max Charge Power | kW |
| Max Charge Price | Currency/kWh |
| Fallback Consumption | kWh (used when no history) |

### Switch (1)
| Entity | Description |
|--------|-------------|
| Enabled | Master on/off |

## Architecture

```
coordinator.py          ← DataUpdateCoordinator (30s refresh)
├── price_analyzer.py   ← Pure logic: price extraction, cheapest window
├── forecast_corrector.py ← Pure logic: 7-day forecast error tracking
├── consumption_tracker.py ← Pure logic: 7-day consumption average
└── storage.py          ← JSON persistence via HA Store
```

Pure logic modules have zero Home Assistant dependencies and are fully unit-tested.

## Development

```bash
# Run tests (no HA installation needed)
python3 -m pytest tests/ -v
```

## Phases

- **Phase 1** ✅: Read-only sensors — computes and displays values, no inverter control
- **Phase 2** ✅: Charging control — state machine, planner, inverter abstraction
- **Phase 3** ✅ (current): Notifications, diagnostics, inverter templates, HACS validation, CI
- **Phase 4**: Community features — multiple price formats, non-contiguous hours, multi-battery

## License

MIT
