"""
Signal Generator — async main entry point.

Uses asyncio so Telethon runs natively without sync wrappers conflicting
with iqoptionapi's internal websocket threads.
"""
import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict

from config import load_config, load_assets, setup_logging, AppConfig
from iqoption.client import IQOptionClient
from analysis.engine import analyze
from telegram.sender import TelegramSender

log = logging.getLogger(__name__)

# Per-asset cooldown: asset → last signal time
_last_signal: Dict[str, datetime] = {}


def is_on_cooldown(asset: str, cooldown_seconds: int) -> bool:
    last = _last_signal.get(asset)
    if last is None:
        return False
    return datetime.now() < last + timedelta(seconds=cooldown_seconds)


def mark_signalled(asset: str) -> None:
    _last_signal[asset] = datetime.now()


async def run_scan(
    cfg: AppConfig,
    iq_client: IQOptionClient,
    tg_sender: TelegramSender,
) -> None:
    """One full scan pass — filters to only currently open assets each cycle."""
    all_configured = load_assets(cfg.trading) or iq_client.get_available_assets()
    assets = iq_client.filter_open(all_configured)

    if not assets:
        log.warning("No open assets right now — skipping scan cycle")
        return

    for asset in assets:
        log.info("─── Scanning %s ───", asset)

        if is_on_cooldown(asset, cfg.signal.cooldown_seconds):
            remaining = (
                _last_signal[asset]
                + timedelta(seconds=cfg.signal.cooldown_seconds)
                - datetime.now()
            )
            log.debug("%s on cooldown — %ds remaining", asset, int(remaining.total_seconds()))
            continue

        # Run candle fetch + analysis in executor so it doesn't block the event loop
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None, iq_client.get_candles, asset, cfg.trading.candle_count
        )

        if df is None or df.empty:
            log.warning("No candle data for %s — skipping", asset)
            continue

        result = analyze(asset, df, cfg.analysis)
        if result is None:
            log.info("%s: no signal (confidence below threshold or neutral)", asset)
            continue

        log.info(
            "✅ SIGNAL: %s %s | confidence=%.0f%% (%d/%d)",
            asset, result.direction, result.confidence * 100,
            result.votes_for, result.votes_total,
        )

        sent = await tg_sender.send_signal(result, cfg.trading, cfg.martingale, cfg.signal)
        if sent:
            mark_signalled(asset)
        else:
            log.error("Failed to send signal for %s — will retry next cycle", asset)


async def main() -> None:
    cfg = load_config("configs/config.yaml")
    setup_logging(cfg.logging)

    log.info("═══════════════════════════════════════")
    log.info("     SIGNAL GENERATOR STARTING")
    log.info("═══════════════════════════════════════")
    log.info("Instrument : %s", cfg.trading.instrument.upper())
    log.info("Expiry     : %ds (%s)",
             cfg.trading.expiry_seconds,
             f"{cfg.trading.expiry_seconds // 60}min" if cfg.trading.expiry_seconds >= 60
             else f"{cfg.trading.expiry_seconds}sec")
    log.info("Scan every : %ds", cfg.trading.scan_interval_seconds)
    log.info("Min confid.: %.0f%%", cfg.analysis.min_confidence * 100)
    log.info("═══════════════════════════════════════")

    shutdown = asyncio.Event()

    def _shutdown(sig, frame):
        log.info("Shutdown signal received — stopping...")
        shutdown.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # IQ Option client (sync, runs in executor)
    iq_client = IQOptionClient(cfg.iqoption, cfg.trading)
    if not iq_client.connect():
        log.critical("Cannot connect to IQ Option — aborting")
        sys.exit(1)

    balance = iq_client.get_balance()
    log.info("Account balance: %.2f", balance)

    # Telegram client (async)
    tg_sender = TelegramSender(cfg.telegram)
    if not await tg_sender.connect():
        log.critical("Cannot connect to Telegram — aborting")
        sys.exit(1)

    # Log asset categories being monitored
    asset_categories = ", ".join(cfg.trading.asset_categories) if cfg.trading.asset_categories else "all"
    log.info("Asset categories: %s", asset_categories)

    await tg_sender.send_status(
        f"Signal Generator started\n"
        f"Instrument: {cfg.trading.instrument.upper()} | "
        f"Expiry: {cfg.trading.expiry_seconds}s | "
        f"Categories: {asset_categories}"
    )

    log.info("Entering scan loop (interval=%ds)...", cfg.trading.scan_interval_seconds)

    while not shutdown.is_set():
        try:
            await run_scan(cfg, iq_client, tg_sender)
        except Exception as e:
            log.exception("Unexpected error in scan loop: %s", e)

        try:
            await asyncio.wait_for(
                shutdown.wait(),
                timeout=cfg.trading.scan_interval_seconds
            )
        except asyncio.TimeoutError:
            pass  # normal — just means interval elapsed, keep scanning

    log.info("Shutting down...")
    iq_client.disconnect()
    await tg_sender.send_status("Signal Generator stopped.")
    await tg_sender.disconnect()
    log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
