"""
Telegram sender — posts signals to a channel using MTProto (Telethon).

Uses a persistent session file (same pattern as the Go signal_bot consumer),
so you only need to authenticate once. The session is reused on restarts.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError

from config import TelegramConfig, TradingConfig, MartingaleConfig, SignalConfig
from analysis.engine import SignalResult

log = logging.getLogger(__name__)

# Flag emojis for common currency pairs
_CURRENCY_FLAGS = {
    "EUR": "🇪🇺", "USD": "🇺🇸", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "AUD": "🇦🇺", "NZD": "🇳🇿", "CAD": "🇨🇦", "CHF": "🇨🇭",
    "BTC": "₿",   "ETH": "Ξ",
}

_DIRECTION_EMOJI = {"CALL": "📈", "PUT": "📉"}
_DIRECTION_LABEL = {"CALL": "🟢 BUY", "PUT": "🔴 SELL"}


def _flags(asset: str) -> str:
    base, quote = asset[:3].upper(), asset[3:6].upper()
    return f"{_CURRENCY_FLAGS.get(base, '')}  {_CURRENCY_FLAGS.get(quote, '')}"


def _format_asset(asset: str) -> str:
    return f"{asset[:3]}/{asset[3:]} (OTC)" if len(asset) == 6 else asset


def _expiry_minutes(expiry_seconds: int) -> int:
    return max(1, expiry_seconds // 60)


def format_signal(
    result: SignalResult,
    trading: TradingConfig,
    martingale: MartingaleConfig,
    signal_cfg: SignalConfig,
    entry_time: Optional[datetime] = None,
) -> str:
    """
    Build Mexy-compatible signal text.
    Parsed correctly by the Go signal_bot consumer (websocket-api branch).
    """
    if entry_time is None:
        entry_time = datetime.now()

    expiry_min = _expiry_minutes(trading.expiry_seconds)
    entry_str = entry_time.strftime("%I:%M %p").lstrip("0")
    confidence_pct = int(result.confidence * 100)

    lines = [
        signal_cfg.source_name,
        "",
        "🚨 TRADE NOW!!",
        "",
        f"{_DIRECTION_EMOJI.get(result.direction, '')}  {_flags(result.asset)} {_format_asset(result.asset)}",
        f"🕒  Timeframe: {expiry_min}-min expiry",
        f"🤖  AI Confidence: {confidence_pct}%",
        f"🕰️  Entry Window: {entry_str}",
        f"Direction: {_DIRECTION_LABEL.get(result.direction, result.direction)}",
        f"📊  Instrument: {trading.instrument.upper()}",
    ]

    if martingale.enabled and martingale.levels > 0 and expiry_min >= 1:
        lines += ["", "📊  Martingale Levels:"]
        for lvl in range(1, martingale.levels + 1):
            t = entry_time + timedelta(minutes=expiry_min * lvl)
            lines.append(f"• Level {lvl}  →  {t.strftime('%I:%M %p').lstrip('0')}")

    if result.details:
        lines += [f"", f"📡  Signals ({result.votes_for}/{result.votes_total}):"]
        for d in result.details:
            lines.append(f"  ▸ {d}")

    return "\n".join(lines)


class TelegramSender:
    def __init__(self, cfg: TelegramConfig):
        self._cfg = cfg
        self._client: Optional[TelegramClient] = None

    def connect(self) -> bool:
        """
        Connect and authenticate. On first run this prompts for the OTP
        sent to your phone. Session is saved to session_file for reuse.
        """
        os.makedirs(os.path.dirname(self._cfg.session_file), exist_ok=True)
        # Strip .session extension — Telethon adds it automatically
        session_path = self._cfg.session_file.replace(".session", "")

        self._client = TelegramClient(
            session_path,
            self._cfg.api_id,
            self._cfg.api_hash,
        )
        self._client.connect()

        if not self._client.is_user_authorized():
            log.info("First run — sending OTP to %s", self._cfg.phone)
            self._client.send_code_request(self._cfg.phone)
            code = input("Enter the Telegram verification code: ").strip()
            try:
                self._client.sign_in(self._cfg.phone, code)
            except SessionPasswordNeededError:
                # 2FA enabled
                password = input("Enter your 2FA password: ").strip()
                self._client.sign_in(password=password)

        log.info("Telegram MTProto session established")
        return True

    def send(self, text: str) -> bool:
        """Send a message to the configured channel."""
        if self._client is None:
            log.error("Telegram client not connected")
            return False
        try:
            self._client.send_message(self._cfg.channel_id, text)
            log.info("Signal posted to channel %s", self._cfg.channel_id)
            return True
        except Exception as e:
            log.error("Failed to send Telegram message: %s", e)
            return False

    def send_signal(
        self,
        result: SignalResult,
        trading: TradingConfig,
        martingale: MartingaleConfig,
        signal_cfg: SignalConfig,
    ) -> bool:
        text = format_signal(result, trading, martingale, signal_cfg, datetime.now())
        log.debug("Formatted signal:\n%s", text)
        return self.send(text)

    def send_status(self, message: str) -> bool:
        return self.send(f"ℹ️ {message}")

    def disconnect(self) -> None:
        if self._client:
            self._client.disconnect()
            log.info("Telegram client disconnected")
