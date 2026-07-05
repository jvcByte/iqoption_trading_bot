"""
Technical indicator calculations using pandas-ta.
Each function returns a scalar result (latest value) plus a signal vote.

Vote convention:
  +1  = bullish (supports CALL/BUY)
  -1  = bearish (supports PUT/SELL)
   0  = neutral / no clear signal
"""
import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import pandas_ta as ta

from config import IndicatorConfig

log = logging.getLogger(__name__)


@dataclass
class IndicatorVote:
    name: str
    value: float
    vote: int       # +1, -1, 0
    detail: str     # human-readable reason


def rsi_vote(df: pd.DataFrame, cfg: IndicatorConfig) -> Optional[IndicatorVote]:
    try:
        rsi_series = ta.rsi(df["close"], length=cfg.rsi_period)
        val = float(rsi_series.iloc[-1])
        if val <= cfg.rsi_oversold:
            return IndicatorVote("RSI", val, +1, f"RSI {val:.1f} ≤ {cfg.rsi_oversold} (oversold → BUY)")
        elif val >= cfg.rsi_overbought:
            return IndicatorVote("RSI", val, -1, f"RSI {val:.1f} ≥ {cfg.rsi_overbought} (overbought → SELL)")
        else:
            return IndicatorVote("RSI", val, 0, f"RSI {val:.1f} neutral")
    except Exception as e:
        log.debug("RSI error: %s", e)
        return None


def ema_crossover_vote(df: pd.DataFrame, cfg: IndicatorConfig) -> Optional[IndicatorVote]:
    try:
        fast = ta.ema(df["close"], length=cfg.ema_fast)
        slow = ta.ema(df["close"], length=cfg.ema_slow)
        f_now, f_prev = float(fast.iloc[-1]), float(fast.iloc[-2])
        s_now, s_prev = float(slow.iloc[-1]), float(slow.iloc[-2])

        # Golden cross: fast crosses above slow
        if f_prev <= s_prev and f_now > s_now:
            return IndicatorVote("EMA_CROSS", f_now - s_now, +1,
                                 f"EMA{cfg.ema_fast} crossed above EMA{cfg.ema_slow} (BUY)")
        # Death cross: fast crosses below slow
        elif f_prev >= s_prev and f_now < s_now:
            return IndicatorVote("EMA_CROSS", f_now - s_now, -1,
                                 f"EMA{cfg.ema_fast} crossed below EMA{cfg.ema_slow} (SELL)")
        # Trend continuation — still vote based on position
        elif f_now > s_now:
            return IndicatorVote("EMA_TREND", f_now - s_now, +1,
                                 f"EMA{cfg.ema_fast} > EMA{cfg.ema_slow} (uptrend)")
        else:
            return IndicatorVote("EMA_TREND", f_now - s_now, -1,
                                 f"EMA{cfg.ema_fast} < EMA{cfg.ema_slow} (downtrend)")
    except Exception as e:
        log.debug("EMA crossover error: %s", e)
        return None


def macd_vote(df: pd.DataFrame, cfg: IndicatorConfig) -> Optional[IndicatorVote]:
    try:
        macd_df = ta.macd(
            df["close"],
            fast=cfg.macd_fast,
            slow=cfg.macd_slow,
            signal=cfg.macd_signal,
        )
        # pandas-ta names columns: MACD_f_s_sig, MACDh_f_s_sig, MACDs_f_s_sig
        hist_col = [c for c in macd_df.columns if c.startswith("MACDh")][0]
        hist_now = float(macd_df[hist_col].iloc[-1])
        hist_prev = float(macd_df[hist_col].iloc[-2])

        # Histogram flip from negative to positive → bullish
        if hist_prev < 0 and hist_now >= 0:
            return IndicatorVote("MACD", hist_now, +1, f"MACD histogram flipped positive (BUY)")
        # Histogram flip from positive to negative → bearish
        elif hist_prev > 0 and hist_now <= 0:
            return IndicatorVote("MACD", hist_now, -1, f"MACD histogram flipped negative (SELL)")
        # Growing histogram
        elif hist_now > hist_prev and hist_now > 0:
            return IndicatorVote("MACD", hist_now, +1, f"MACD histogram growing bullish")
        elif hist_now < hist_prev and hist_now < 0:
            return IndicatorVote("MACD", hist_now, -1, f"MACD histogram growing bearish")
        else:
            return IndicatorVote("MACD", hist_now, 0, f"MACD neutral ({hist_now:.5f})")
    except Exception as e:
        log.debug("MACD error: %s", e)
        return None


def bollinger_vote(df: pd.DataFrame, cfg: IndicatorConfig) -> Optional[IndicatorVote]:
    try:
        bb = ta.bbands(df["close"], length=cfg.bb_period, std=cfg.bb_std)
        # pandas-ta columns: BBL_p_s, BBM_p_s, BBU_p_s, BBB_p_s, BBP_p_s
        lower_col = [c for c in bb.columns if c.startswith("BBL")][0]
        upper_col = [c for c in bb.columns if c.startswith("BBU")][0]

        price = float(df["close"].iloc[-1])
        lower = float(bb[lower_col].iloc[-1])
        upper = float(bb[upper_col].iloc[-1])

        if price <= lower:
            return IndicatorVote("BB", price, +1, f"Price {price:.5f} at/below lower band {lower:.5f} (BUY)")
        elif price >= upper:
            return IndicatorVote("BB", price, -1, f"Price {price:.5f} at/above upper band {upper:.5f} (SELL)")
        else:
            return IndicatorVote("BB", price, 0, f"Price inside bands — neutral")
    except Exception as e:
        log.debug("Bollinger Bands error: %s", e)
        return None


def stochastic_vote(df: pd.DataFrame, cfg: IndicatorConfig) -> Optional[IndicatorVote]:
    try:
        stoch = ta.stoch(df["high"], df["low"], df["close"], k=cfg.stoch_k, d=cfg.stoch_d)
        k_col = [c for c in stoch.columns if c.startswith("STOCHk")][0]
        d_col = [c for c in stoch.columns if c.startswith("STOCHd")][0]
        k = float(stoch[k_col].iloc[-1])
        d = float(stoch[d_col].iloc[-1])

        if k < 20 and d < 20 and k > d:
            return IndicatorVote("STOCH", k, +1, f"Stoch K={k:.1f} D={d:.1f} oversold crossover (BUY)")
        elif k > 80 and d > 80 and k < d:
            return IndicatorVote("STOCH", k, -1, f"Stoch K={k:.1f} D={d:.1f} overbought crossover (SELL)")
        else:
            return IndicatorVote("STOCH", k, 0, f"Stoch neutral K={k:.1f}")
    except Exception as e:
        log.debug("Stochastic error: %s", e)
        return None
