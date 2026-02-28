#!/usr/bin/env python3
"""Bootstrap session_cost_history into the integration's store.

Injects reconstructed charging session cost data from Feb 21-26, 2026
into the .storage file so the weekly/monthly cost and savings sensors
show real values instead of 0.

Usage (run on the HA machine):
    python3 bootstrap_session_costs.py

Or pipe via SSH:
    cat bootstrap_session_costs.py | ssh hassio@homeassistant 'sudo python3 - '
"""

import json
import glob
import sys

STORE_PATTERN = "/config/.storage/smart_battery_charging.*"
SESSION_COST_HISTORY = [
    {"date": "2026-02-26", "kwh": 2.76, "avg_price": 1.86, "cost": 5.13},
    {"date": "2026-02-24", "kwh": 5.7, "avg_price": 2.11, "cost": 12.03},
    {"date": "2026-02-23", "kwh": 6.91, "avg_price": 1.12, "cost": 7.74},
    {"date": "2026-02-22", "kwh": 4.67, "avg_price": 1.3, "cost": 6.07},
    {"date": "2026-02-21", "kwh": 12.45, "avg_price": 1.3875, "cost": 17.27},
]


def main():
    files = glob.glob(STORE_PATTERN)
    if not files:
        print("ERROR: No store file found matching %s" % STORE_PATTERN)
        sys.exit(1)

    for path in files:
        print("Updating: %s" % path)
        with open(path) as f:
            store = json.load(f)

        data = store.get("data", {})
        existing = data.get("session_cost_history", [])
        existing_dates = {e.get("date") for e in existing}

        # Merge: keep existing entries, add missing ones
        added = 0
        for entry in SESSION_COST_HISTORY:
            if entry["date"] not in existing_dates:
                existing.append(entry)
                added += 1

        # Sort newest first
        existing.sort(key=lambda x: x.get("date", ""), reverse=True)
        data["session_cost_history"] = existing
        store["data"] = data

        with open(path, "w") as f:
            json.dump(store, f, indent=2, ensure_ascii=False)

        print("  Added %d entries, total now %d" % (added, len(existing)))

    print("\nDone. Restart HA to pick up the changes:")
    print("  ha core restart")


if __name__ == "__main__":
    main()
