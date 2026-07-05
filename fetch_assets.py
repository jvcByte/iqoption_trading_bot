"""
Utility script — connects to IQ Option and prints all available assets
for both binary (turbo-option) and blitz instruments.

Usage:
    make fetch-assets
    # or
    .venv/bin/python fetch_assets.py
"""
import time
import sys
import logging

from iqoptionapi.stable_api import IQ_Option
from config import load_config

logging.basicConfig(level=logging.WARNING, format="%(message)s")

INSTRUMENTS = {
    "turbo-option": "BINARY",
    "blitz":        "BLITZ",
    "digital":      "DIGITAL",
}


def main():
    cfg = load_config("configs/config.yaml")
    iq = cfg.iqoption

    print(f"\nConnecting to IQ Option ({iq.email})...")
    api = IQ_Option(iq.email, iq.password)
    check, reason = api.connect()
    if not check:
        print(f"Connection failed: {reason}")
        sys.exit(1)

    balance_type = "PRACTICE" if iq.demo_mode else "REAL"
    api.change_balance(balance_type)
    print(f"Connected ({balance_type} account)\n")

    # Small wait for websocket data to populate
    time.sleep(2)

    all_open = api.get_all_open_time()

    for instrument_key, label in INSTRUMENTS.items():
        assets = all_open.get(instrument_key, {})
        if not assets:
            continue

        open_assets   = sorted([a for a, v in assets.items() if v.get("open", False)])
        closed_assets = sorted([a for a, v in assets.items() if not v.get("open", False)])

        print(f"{'═'*50}")
        print(f"  {label} ({instrument_key})")
        print(f"{'═'*50}")
        print(f"  ✅ OPEN  ({len(open_assets)}):")
        for a in open_assets:
            print(f"      - \"{a}\"")

        print(f"\n  🔒 CLOSED ({len(closed_assets)}):")
        for a in closed_assets:
            print(f"      - \"{a}\"")
        print()

    print("─" * 50)
    print("Tip: copy the OPEN assets you want into configs/config.yaml → trading.assets")
    print("     Or set trading.assets: [] to auto-use all open assets at runtime.\n")

    api.close()


if __name__ == "__main__":
    main()
