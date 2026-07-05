import yaml
import logging
import os
from dataclasses import dataclass, field
from typing import List

log = logging.getLogger(__name__)


@dataclass
class IQOptionConfig:
    email: str
    password: str
    demo_mode: bool = True


@dataclass
class TelegramConfig:
    api_id: int
    api_hash: str
    phone: str
    channel_id: int
    session_file: str = "session/telegram.session"


@dataclass
class IndicatorConfig:
    rsi_period: int = 14
    rsi_oversold: int = 30
    rsi_overbought: int = 70
    ema_fast: int = 9
    ema_slow: int = 21
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    stoch_k: int = 14
    stoch_d: int = 3


@dataclass
class AnalysisConfig:
    min_confidence: float = 0.75
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)


@dataclass
class TradingConfig:
    instrument: str = "binary"          # "binary" or "blitz"
    expiry_seconds: int = 120
    assets: List[str] = field(default_factory=list)
    scan_interval_seconds: int = 30
    candle_interval_seconds: int = 60
    candle_count: int = 100


@dataclass
class MartingaleConfig:
    enabled: bool = True
    levels: int = 2


@dataclass
class SignalConfig:
    cooldown_seconds: int = 300
    source_name: str = "SIGNAL BOT 🤖"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/signal_generator.log"


@dataclass
class AppConfig:
    iqoption: IQOptionConfig = None
    telegram: TelegramConfig = None
    trading: TradingConfig = field(default_factory=TradingConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    martingale: MartingaleConfig = field(default_factory=MartingaleConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load_config(path: str = "configs/config.yaml") -> AppConfig:
    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    iq = raw["iqoption"]
    tg = raw["telegram"]
    tr = raw.get("trading", {})
    an = raw.get("analysis", {})
    ind = an.get("indicators", {})
    mg = raw.get("martingale", {})
    sg = raw.get("signal", {})
    lg = raw.get("logging", {})

    return AppConfig(
        iqoption=IQOptionConfig(
            email=iq["email"],
            password=iq["password"],
            demo_mode=iq.get("demo_mode", True),
        ),
        telegram=TelegramConfig(
            api_id=int(tg["api_id"]),
            api_hash=tg["api_hash"],
            phone=tg["phone"],
            channel_id=int(tg["channel_id"]),
            session_file=tg.get("session_file", "session/telegram.session"),
        ),
        trading=TradingConfig(
            instrument=tr.get("instrument", "binary"),
            expiry_seconds=tr.get("expiry_seconds", 120),
            assets=tr.get("assets", ["EURUSD"]),
            scan_interval_seconds=tr.get("scan_interval_seconds", 30),
            candle_interval_seconds=tr.get("candle_interval_seconds", 60),
            candle_count=tr.get("candle_count", 100),
        ),
        analysis=AnalysisConfig(
            min_confidence=an.get("min_confidence", 0.75),
            indicators=IndicatorConfig(
                rsi_period=ind.get("rsi_period", 14),
                rsi_oversold=ind.get("rsi_oversold", 30),
                rsi_overbought=ind.get("rsi_overbought", 70),
                ema_fast=ind.get("ema_fast", 9),
                ema_slow=ind.get("ema_slow", 21),
                macd_fast=ind.get("macd_fast", 12),
                macd_slow=ind.get("macd_slow", 26),
                macd_signal=ind.get("macd_signal", 9),
                bb_period=ind.get("bb_period", 20),
                bb_std=ind.get("bb_std", 2.0),
                stoch_k=ind.get("stoch_k", 14),
                stoch_d=ind.get("stoch_d", 3),
            ),
        ),
        martingale=MartingaleConfig(
            enabled=mg.get("enabled", True),
            levels=mg.get("levels", 2),
        ),
        signal=SignalConfig(
            cooldown_seconds=sg.get("cooldown_seconds", 300),
            source_name=sg.get("source_name", "SIGNAL BOT 🤖"),
        ),
        logging=LoggingConfig(
            level=lg.get("level", "INFO"),
            file=lg.get("file", "logs/signal_generator.log"),
        ),
    )


def setup_logging(cfg: LoggingConfig) -> None:
    os.makedirs(os.path.dirname(cfg.file), exist_ok=True)
    level = getattr(logging, cfg.level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(cfg.file),
    ]
    logging.basicConfig(level=level, format=fmt, handlers=handlers)
