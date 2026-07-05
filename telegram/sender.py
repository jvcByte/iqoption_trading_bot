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


def _expiry_minutes(expiry_seconds: int) -> int:
    return max(1, expiry_seconds // 60)


def format_signal(
    result: SignalResult,
    trading: TradingConfig,
    martingale: MartingaleConfig,
    signal_cfg: SignalConfig,
    sent_at: Optional[datetime] = None,
) -> str:
    if sent_at is None:
        sent_at = datetime.now()

    # Entry window is always in the future — subscribers need time to act
    entry_time = sent_at + timedelta(seconds=signal_cfg.entry_lead_seconds)

    expiry_min = _expiry_minutes(trading.expiry_seconds)
    sent_str = sent_at.strftime("%I:%M %p").lstrip("0")
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
        f"📤  Signal Sent: {sent_str}",
        f"🕰️  Entry Window: {entry_str}",
        f"Direction: {_DIRECTION_LABEL.get(result.direction, result.direction)}",
        f"📊  Instrument: {trading.instrument.upper()}",
    ]

    # Martingale levels are offset from entry time by expiry duration each
    if martingale.enabled and martingale.levels > 0 and expiry_min >= 1:
        lines += ["", "📊  Martingale Levels:"]
        for lvl in range(1, martingale.levels + 1):
            t = entry_time + timedelta(minutes=expiry_min * lvl)
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

        self._client = TelegramClient(
            session_path,
            self._cfg.api_id,
            self._cfg.api_hash,
        )

        # Retry connect — stale processes may still be releasing the session lock
        for attempt in range(5):
            try:
                await self._client.connect()
                break
            except Exception as e:
                if "database is locked" in str(e).lower() or "locked" in str(e).lower():
                    log.warning(
                        "Telegram session locked (attempt %d/5) — "
                        "stale process still releasing. Retrying in 3s...", attempt + 1
                    )
                    await asyncio.sleep(3)
                    # Re-create client to get a fresh SQLite handle
                    self._client = TelegramClient(
                        session_path,
                        self._cfg.api_id,
                        self._cfg.api_hash,
                    )
                else:
                    log.error("Telegram connect error: %s", e)
                    return False
        else:
            log.error("Telegram session still locked after 5 attempts — is another instance running?")
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
