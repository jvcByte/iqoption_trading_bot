"""
IQ Option client — wraps iqoptionapi to fetch live candle data.
Handles connection, reconnection, and candle normalization into pandas DataFrames.
"""
import logging
import time
from typing import List, Optional

import pandas as pd
from iqoptionapi.stable_api import IQ_Option

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


class IQOptionClient:
    def __init__(self, cfg: IQOptionConfig, trading_cfg: TradingConfig):
        self._cfg = cfg
        self._trading = trading_cfg
        self._api: Optional[IQ_Option] = None
        self._connected = False

    def connect(self) -> bool:
        log.info("Connecting to IQ Option (demo=%s)...", self._cfg.demo_mode)
        self._api = IQ_Option(self._cfg.email, self._cfg.password)
        check, reason = self._api.connect()
        if not check:
            log.error("IQ Option connection failed: %s", reason)
            return False

        # Select account type
        balance_type = "PRACTICE" if self._cfg.demo_mode else "REAL"
        self._api.change_balance(balance_type)
        self._connected = True
        log.info("Connected to IQ Option (%s account)", balance_type)
        return True

    def ensure_connected(self) -> bool:
        """Reconnect if session dropped."""
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
            log.error("Failed to fetch candles for %s: %s", asset, e)
            return None

        if not candles:
            log.warning("No candles returned for %s", asset)
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

    def get_open_assets(self) -> List[str]:
        """
        Query IQ Option for every asset currently open/tradeable.
        Blitz falls back to turbo-option since they share the same asset pool.
        """
        if not self.ensure_connected():
            return []
        try:
            all_assets = self._api.get_all_open_time()
            instrument = _INSTRUMENT_MAP.get(self._trading.instrument, "turbo-option")
            assets = all_assets.get(instrument, {})
            open_list = [a for a, info in assets.items() if info.get("open", False)]

            if not open_list and instrument == "blitz":
                assets = all_assets.get("turbo-option", {})
                open_list = [a for a, info in assets.items() if info.get("open", False)]

            return sorted(open_list)
        except Exception as e:
            log.error("Failed to get open assets from IQ Option: %s", e)
            return []

    def filter_open(self, candidates: List[str]) -> List[str]:
        """
        Given a list from assets.json, return only those IQ Option has open right now.
        This is the real-time check — market hours vary per asset.
        Forex: closes weekends. Stocks: follow exchange hours. OTC: 24/7.
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
