"""
Signal engine — runs all indicators, scores confluence, decides direction.

Scoring:
  Each indicator that agrees on direction contributes 1/N to the confidence.
  Signal is emitted only when confidence >= cfg.min_confidence.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from config import AnalysisConfig
from analysis.indicators import (
    IndicatorVote,
    rsi_vote,
    ema_crossover_vote,
    macd_vote,
    bollinger_vote,
    stochastic_vote,
)

log = logging.getLogger(__name__)


@dataclass
class SignalResult:
    asset: str
    direction: str          # "CALL" or "PUT"
    confidence: float       # 0.0 – 1.0
    votes_for: int          # indicators agreeing
    votes_total: int        # total valid indicator votes
    details: List[str] = field(default_factory=list)


def analyze(asset: str, df: pd.DataFrame, cfg: AnalysisConfig) -> Optional[SignalResult]:
    """
    Run all indicators on df, compute confluence, return SignalResult or None.
    Returns None if confidence < min_confidence or no clear direction.
    """
    if df is None or len(df) < 30:
        log.warning("%s: not enough candles (%s)", asset, len(df) if df is not None else 0)
        return None

    ind = cfg.indicators
    indicator_fns = [
        rsi_vote,
        ema_crossover_vote,
        macd_vote,
        bollinger_vote,
        stochastic_vote,
    ]

    votes: List[IndicatorVote] = []
    for fn in indicator_fns:
        result = fn(df, ind)
        if result is not None:
            votes.append(result)
            log.debug("%s | %s: vote=%+d | %s", asset, result.name, result.vote, result.detail)

    if not votes:
        log.warning("%s: no indicator votes produced", asset)
        return None

    bull_votes = [v for v in votes if v.vote == +1]
    bear_votes = [v for v in votes if v.vote == -1]

    # Discard if votes are split — must have a clear majority
    if len(bull_votes) == len(bear_votes):
        log.debug("%s: tied votes (%d bull / %d bear) — skip", asset, len(bull_votes), len(bear_votes))
        return None

    if len(bull_votes) > len(bear_votes):
        direction = "CALL"
        votes_for = len(bull_votes)
        winning_votes = bull_votes
    else:
        direction = "PUT"
        votes_for = len(bear_votes)
        winning_votes = bear_votes

    confidence = votes_for / len(votes)
    details = [v.detail for v in winning_votes]

    log.info(
        "%s: direction=%s confidence=%.0f%% (%d/%d indicators)",
        asset, direction, confidence * 100, votes_for, len(votes),
    )

    if confidence < cfg.min_confidence:
        log.debug(
            "%s: confidence %.0f%% below threshold %.0f%% — skip",
            asset, confidence * 100, cfg.min_confidence * 100,
        )
        return None

    return SignalResult(
        asset=asset,
        direction=direction,
        confidence=confidence,
        votes_for=votes_for,
        votes_total=len(votes),
        details=details,
    )
