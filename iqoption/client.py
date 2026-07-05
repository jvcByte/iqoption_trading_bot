"""
IQ Option client — wraps iqoptionapi to fetch live candle data.
Handles connection, reconnection, candle normalization, and SSID persistence
to avoid repeated logins and reduce rate-limiting risk.
"""
import json
import logging
import os
import time
from typing import List, Optional

import pandas as pd
from iqoptionapi.stable_api import IQ_Option
from iqoptionapi import global_value

from config import IQOptionConfig, TradingConfig

log = logging.getLogger(__name__)

# Map expiry seconds to IQ Option instrument type
_INSTRUMENT_MAP = {
    "binary": "turbo-option",   # binary options (1m+)
    "blitz": "blitz",            # blitz options (30s–5min)
}

# Candle duration in seconds → IQ Option duration integer
_CANDLE_DURATION_MAP = {
    5: 5,
    10: 10,
    15: 15,
    30: 30,
    60: 60,
    120: 120,
    300: 300,
    600: 600,
    900: 900,
    1800: 1800,
    3600: 3600,
    14400: 14400,
    86400: 86400,
}


_SSID_FILE = "session/iqoption_ssid.json"


def _load_ssid() -> Optional[str]:
    try:
        with open(_SSID_FILE) as f:
            data = json.load(f)
            return data.get("ssid")
    except Exception:
        return None


def _save_ssid(ssid: str) -> None:
    os.makedirs(os.path.dirname(_SSID_FILE), exist_ok=True)
    with open(_SSID_FILE, "w") as f:
        json.dump({"ssid": ssid}, f)
    log.debug("SSID saved to %s", _SSID_FILE)


class IQOptionClient:
    def __init__(self, cfg: IQOptionConfig, trading_cfg: TradingConfig):
        self._cfg = cfg
        self._trading = trading_cfg
        self._api: Optional[IQ_Option] = None
        self._connected = False

    def connect(self) -> bool:
        log.info("Connecting to IQ Option (demo=%s)...", self._cfg.demo_mode)

        # Restore saved SSID — library will try it first, skipping the login call
        saved_ssid = _load_ssid()
        if saved_ssid:
            global_value.SSID = saved_ssid
            log.info("Restored saved SSID — attempting session reuse...")
        else:
            log.info("No saved SSID — will perform fresh login")

        for attempt in range(3):
            try:
                self._api = IQ_Option(self._cfg.email, self._cfg.password)
                check, reason = self._api.connect()
                if not check:
                    log.error("IQ Option connection failed: %s (attempt %d/3)", reason, attempt + 1)
                    # Saved SSID may be expired — clear it and retry with fresh login
                    if saved_ssid and attempt == 0:
                        log.info("Clearing stale SSID, retrying with fresh login...")
                        global_value.SSID = None
                        saved_ssid = None
                        try:
                            os.remove(_SSID_FILE)
                        except Exception:
                            pass
                    time.sleep(3)
                    continue

                # Save the fresh SSID for next run
                current_ssid = global_value.SSID
                if current_ssid:
                    _save_ssid(current_ssid)
                    log.info("SSID saved for session reuse")

                balance_type = "PRACTICE" if self._cfg.demo_mode else "REAL"
                self._api.change_balance(balance_type)
                self._connected = True

                log.info("Connected (%s) — waiting for websocket data to settle...", balance_type)
                time.sleep(3)
                log.info("IQ Option ready")
                return True
            except Exception as e:
                log.error("IQ Option connect exception (attempt %d/3): %s", attempt + 1, e)
                global_value.SSID = None
                time.sleep(3)

        log.error("All IQ Option connection attempts failed")
        return False

    def ensure_connected(self) -> bool:
        """Reconnect if session dropped, saving fresh SSID on success."""
        if not self._connected:
            return self.connect()
        if not self._api.check_connect():
            log.warning("IQ Option connection lost — reconnecting...")
            self._connected = False
            return self.connect()
        return True

    def get_candles(self, asset: str, count: int = 100) -> Optional[pd.DataFrame]:
        """
        Fetch the last `count` candles for asset.
        Returns a DataFrame with columns: open, high, low, close, volume, time
        """
        if not self.ensure_connected():
            return None

        duration = self._trading.candle_interval_seconds
        iq_duration = _CANDLE_DURATION_MAP.get(duration, 60)
        end_time = time.time()

        try:
            candles = self._api.get_candles(asset, iq_duration, count, end_time)
        except Exception as e:
            log.debug("Failed to fetch candles for %s: %s", asset, e)
            return None

        if not candles:
            log.debug("No candles returned for %s (may not be available)", asset)
            return None

        rows = []
        for c in candles:
            rows.append({
                "time":   pd.Timestamp(c["from"], unit="s"),
                "open":   float(c["open"]),
                "high":   float(c["max"]),
                "low":    float(c["min"]),
                "close":  float(c["close"]),
                "volume": float(c.get("volume", 0)),
            })

        df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
        log.debug("Fetched %d candles for %s", len(df), asset)
        return df

    def _get_open_assets_sync(self) -> List[str]:
        """
        Fetch open assets without calling get_all_open_time() — that method
        blocks 30s waiting for digital options we don't use.
        We call only the binary/turbo init directly and parse it ourselves.
        """
        try:
            # Directly use the fast binary init (no digital/other threads)
            binary_data = self._api.get_all_init_v2()
            if not binary_data:
                return []

            open_list = []
            for option_type in ["binary", "turbo"]:
                if option_type not in binary_data:
                    continue
                for active_id, active in binary_data[option_type]["actives"].items():
                    if not active.get("enabled", False):
                        continue
                    if active.get("is_suspended", False):
                        continue
                    name = str(active["name"]).split(".")[1]
                    open_list.append(name)

            return sorted(set(open_list))
        except Exception as e:
            log.error("_get_open_assets_sync failed: %s", e)
            return []

    def get_open_assets(self) -> List[str]:
        if not self.ensure_connected():
            return []
        return self._get_open_assets_sync()

    def filter_open(self, candidates: List[str]) -> List[str]:
        """
        Return only assets from candidates that IQ Option has open right now.
        """
        open_set = set(self.get_open_assets())
        open_candidates = [a for a in candidates if a in open_set]
        closed = [a for a in candidates if a not in open_set]
        if closed:
            log.debug("Skipping %d closed assets: %s", len(closed), ", ".join(closed))
        log.info("Open: %d / %d assets from configured list", len(open_candidates), len(candidates))
        return open_candidates

    def get_available_assets(self) -> List[str]:
        """Alias — all currently open assets with no pre-filter."""
        return self.get_open_assets()

    def get_balance(self) -> float:
        if not self.ensure_connected():
            return 0.0
        try:
            return self._api.get_balance()
        except Exception:
            return 0.0

    def disconnect(self) -> None:
        if self._api:
            try:
                self._api.close()
            except Exception:
                pass
        self._connected = False
        log.info("Disconnected from IQ Option")
