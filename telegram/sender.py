"""
Telegram sender — posts signals to a channel using MTProto (Telethon).
Runs fully async to avoid conflicts with iqoptionapi's internal threads.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from config import TelegramConfig, TradingConfig, MartingaleConfig, SignalConfig
from analysis.engine import SignalResult

log = logging.getLogger(__name__)

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


def _expiry_label(expiry_seconds: int) -> str:
    """Human-readable expiry: 30s → '30-sec', 60s → '1-min', 120s → '2-min'."""
    if expiry_seconds < 60:
        return f"{expiry_seconds}-sec"
    return f"{expiry_seconds // 60}-min"


def _parser_expiry_min(expiry_seconds: int) -> int:
    """
    Expiry in whole minutes for Go parser compatibility.
    The Mexy parser reads 'X-min expiry' — minimum 1 for sub-minute blitz.
    """
    return max(1, expiry_seconds // 60)


def format_signal(
    result: SignalResult,
    trading: TradingConfig,
    martingale: MartingaleConfig,
    signal_cfg: SignalConfig,
    sent_at: Optional[datetime] = None,
) -> str:
    """
    Build Mexy-compatible signal text.

    Entry window is always `entry_lead_seconds` ahead of sent_at so
    subscribers have time to act before the trade opens.
    Martingale levels are spaced by expiry_seconds from entry time.
    """
    if sent_at is None:
        sent_at = datetime.now()

    # Entry is always in the future
    entry_time = sent_at + timedelta(seconds=signal_cfg.entry_lead_seconds)

    expiry_lbl   = _expiry_label(trading.expiry_seconds)
    parser_min   = _parser_expiry_min(trading.expiry_seconds)
    confidence   = int(result.confidence * 100)
    sent_str     = sent_at.strftime("%I:%M %p").lstrip("0")
    entry_str    = entry_time.strftime("%I:%M %p").lstrip("0")

    lines = [
        signal_cfg.source_name,
        "",
        "🚨 TRADE NOW!!",
        "",
        f"{_DIRECTION_EMOJI.get(result.direction, '')}  "
        f"{_flags(result.asset)} {_format_asset(result.asset)}",
        # Keep Mexy parser format "X-min expiry" for Go consumer compatibility
        f"🕒  Timeframe: {parser_min}-min expiry ({expiry_lbl})",
        f"🤖  AI Confidence: {confidence}%",
        f"📤  Signal Sent: {sent_str}",
        f"🕰️  Entry Window: {entry_str}",
        f"Direction: {_DIRECTION_LABEL.get(result.direction, result.direction)}",
        f"📊  Instrument: {trading.instrument.upper()}",
    ]

    # Martingale levels — each spaced by expiry_seconds from entry
    if martingale.enabled and martingale.levels > 0:
        lines += ["", "📊  Martingale Levels:"]
        for lvl in range(1, martingale.levels + 1):
            t = entry_time + timedelta(seconds=trading.expiry_seconds * lvl)
            lines.append(f"• Level {lvl}  →  {t.strftime('%I:%M %p').lstrip('0')}")

    if result.details:
        lines += ["", f"📡  Signals ({result.votes_for}/{result.votes_total}):"]
        for d in result.details:
            lines.append(f"  ▸ {d}")

    return "\n".join(lines)


class TelegramSender:
    def __init__(self, cfg: TelegramConfig):
        self._cfg = cfg
        self._client: Optional[TelegramClient] = None

    async def connect(self) -> bool:
        os.makedirs(os.path.dirname(self._cfg.session_file), exist_ok=True)
        session_path = self._cfg.session_file.replace(".session", "")

        self._client = TelegramClient(session_path, self._cfg.api_id, self._cfg.api_hash)

        # Retry on session lock — stale process may still be releasing it
        for attempt in range(5):
            try:
                await self._client.connect()
                break
            except Exception as e:
                if "locked" in str(e).lower():
                    log.warning(
                        "Telegram session locked (attempt %d/5) — retrying in 3s...",
                        attempt + 1,
                    )
                    await asyncio.sleep(3)
                    self._client = TelegramClient(
                        session_path, self._cfg.api_id, self._cfg.api_hash
                    )
                else:
                    log.error("Telegram connect error: %s", e)
                    return False
        else:
            log.error("Telegram session still locked after 5 attempts")
            return False

        if not await self._client.is_user_authorized():
            log.info("First run — sending OTP to %s", self._cfg.phone)
            await self._client.send_code_request(self._cfg.phone)
            code = input("Enter the Telegram verification code: ").strip()
            try:
                await self._client.sign_in(self._cfg.phone, code)
            except SessionPasswordNeededError:
                password = input("Enter your 2FA password: ").strip()
                await self._client.sign_in(password=password)

        log.info("Telegram MTProto session established")
        return True

    async def send(self, text: str) -> bool:
        if self._client is None:
            log.error("Telegram client not connected")
            return False
        try:
            await self._client.send_message(self._cfg.channel_id, text)
            log.info("Signal posted to channel %s", self._cfg.channel_id)
            return True
        except Exception as e:
            log.error("Failed to send Telegram message: %s", e)
            return False

    async def send_signal(
        self,
        result: SignalResult,
        trading: TradingConfig,
        martingale: MartingaleConfig,
        signal_cfg: SignalConfig,
    ) -> bool:
        text = format_signal(result, trading, martingale, signal_cfg, sent_at=datetime.now())
        log.debug("Formatted signal:\n%s", text)
        return await self.send(text)

    async def send_status(self, message: str) -> bool:
        return await self.send(f"ℹ️ {message}")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.disconnect()
            log.info("Telegram client disconnected")
