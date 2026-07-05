"""
Signal Generator — async main entry point.

Uses asyncio so Telethon runs natively without sync wrappers conflicting
with iqoptionapi's internal websocket threads.
"""
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict

from config import load_config, load_assets, setup_logging, AppConfig
from iqoption.client import IQOptionClient
from analysis.engine import analyze
from telegram.sender import TelegramSender

log = logging.getLogger(__name__)

_PID_FILE = "session/signal_generator.pid"

# Per-asset cooldown: asset → last signal time
_last_signal: Dict[str, datetime] = {}


def _acquire_pid_lock() -> bool:
    """Write PID file. Returns False if another instance is already running."""
    os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            # Check if that process is actually still alive
            os.kill(old_pid, 0)
            log.error("Another instance is already running (PID %d). Exiting.", old_pid)
            return False
        except (ProcessLookupError, ValueError):
            # Process is gone — stale PID file, safe to overwrite
            log.warning("Stale PID file found (dead process) — overwriting")
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_pid_lock() -> None:
    try:
        os.remove(_PID_FILE)
    except Exception:
        pass


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

    loop = asyncio.get_event_loop()
    try:
        assets = await asyncio.wait_for(
            loop.run_in_executor(None, iq_client.filter_open, all_configured),
            timeout=15.0
        )
    except asyncio.TimeoutError:
        log.warning("filter_open timed out — using full configured list")
        assets = all_configured

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

        # Run blocking IQ Option calls in executor with a timeout
        loop = asyncio.get_event_loop()
        try:
            df = await asyncio.wait_for(
                loop.run_in_executor(None, iq_client.get_candles, asset, cfg.trading.candle_count),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            log.warning("%s: candle fetch timed out — skipping", asset)
            continue

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

    if not _acquire_pid_lock():
        sys.exit(1)

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

    def _shutdown():
        if not shutdown.is_set():
            log.info("Shutdown signal received — stopping...")
            shutdown.set()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, _shutdown)
    loop.add_signal_handler(signal.SIGTERM, _shutdown)

    # IQ Option client (sync, runs in executor to avoid blocking event loop)
    iq_client = IQOptionClient(cfg.iqoption, cfg.trading)
    loop = asyncio.get_event_loop()
    connected = await loop.run_in_executor(None, iq_client.connect)
    if not connected:
        log.critical("Cannot connect to IQ Option — aborting")
        sys.exit(1)

    balance = await loop.run_in_executor(None, iq_client.get_balance)
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
            scan_task = asyncio.create_task(run_scan(cfg, iq_client, tg_sender))
            # Wait for scan or shutdown — whichever comes first
            done, _ = await asyncio.wait(
                [scan_task, asyncio.create_task(shutdown.wait())],
                return_when=asyncio.FIRST_COMPLETED
            )
            if shutdown.is_set():
                scan_task.cancel()
                break
        except Exception as e:
            log.exception("Unexpected error in scan loop: %s", e)

        try:
            await asyncio.wait_for(shutdown.wait(), timeout=cfg.trading.scan_interval_seconds)
        except asyncio.TimeoutError:
            pass  # interval elapsed, keep scanning

    log.info("Shutting down...")
    _release_pid_lock()
    iq_client.disconnect()
    await tg_sender.send_status("Signal Generator stopped.")
    await tg_sender.disconnect()
    log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
