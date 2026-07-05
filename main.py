"""
Signal Generator — main entry point.

Loop:
  For each configured asset, every scan_interval_seconds:
    1. Fetch candles from IQ Option
    2. Run analysis engine
    3. If confident signal → post to Telegram channel
    4. Apply per-asset cooldown to avoid signal spam
"""
import logging
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import Dict

from config import load_config, load_assets, setup_logging, AppConfig
from iqoption.client import IQOptionClient
from analysis.engine import analyze
from telegram.sender import TelegramSender

log = logging.getLogger(__name__)

# Per-asset cooldown tracker: asset → last signal time
_last_signal: Dict[str, datetime] = {}


def is_on_cooldown(asset: str, cooldown_seconds: int) -> bool:
    last = _last_signal.get(asset)
    if last is None:
        return False
    return datetime.now() < last + timedelta(seconds=cooldown_seconds)


def mark_signalled(asset: str) -> None:
    _last_signal[asset] = datetime.now()


def run_scan(
    cfg: AppConfig,
    iq_client: IQOptionClient,
    tg_sender: TelegramSender,
) -> None:
    """One full scan pass — filters to only currently open assets each cycle."""
    all_configured = load_assets(cfg.trading) or iq_client.get_available_assets()

    # Real-time filter: only trade what IQ Option has open right now
    assets = iq_client.filter_open(all_configured)
    if not assets:
        log.warning("No open assets right now — skipping scan cycle")
        return

    for asset in assets:
        log.info("─── Scanning %s ───", asset)

        if is_on_cooldown(asset, cfg.signal.cooldown_seconds):
            remaining = (
                _last_signal[asset] + timedelta(seconds=cfg.signal.cooldown_seconds)
                - datetime.now()
            )
            log.debug("%s on cooldown — %ds remaining", asset, remaining.seconds)
            continue

        df = iq_client.get_candles(asset, count=cfg.trading.candle_count)
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

        sent = tg_sender.send_signal(result, cfg.trading, cfg.martingale, cfg.signal)
        if sent:
            mark_signalled(asset)
        else:
            log.error("Failed to send signal for %s — will retry next cycle", asset)


def main() -> None:
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
    log.info("Assets     : %s", ", ".join(cfg.trading.assets))
    log.info("Scan every : %ds", cfg.trading.scan_interval_seconds)
    log.info("Min confid.: %.0f%%", cfg.analysis.min_confidence * 100)
    log.info("═══════════════════════════════════════")

    # Graceful shutdown on SIGINT / SIGTERM
    shutdown = {"requested": False}

    def _shutdown(sig, frame):
        log.info("Shutdown signal received — stopping...")
        shutdown["requested"] = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Init clients
    iq_client = IQOptionClient(cfg.iqoption, cfg.trading)
    tg_sender = TelegramSender(cfg.telegram)

    if not iq_client.connect():
        log.critical("Cannot connect to IQ Option — aborting")
        sys.exit(1)

    if not tg_sender.connect():
        log.critical("Cannot connect to Telegram — aborting")
        sys.exit(1)

    balance = iq_client.get_balance()
    log.info("Account balance: %.2f", balance)

    # Resolve asset list: manual override > assets.json > live API discovery
    cfg.trading.assets = load_assets(cfg.trading)
    if not cfg.trading.assets:
        log.info("No assets in config/file — fetching all open assets from IQ Option...")
        cfg.trading.assets = iq_client.get_available_assets()
    if not cfg.trading.assets:
        log.critical("No open assets found — aborting")
        sys.exit(1)
    log.info("Trading %d assets across: %s", len(cfg.trading.assets), ", ".join(cfg.trading.asset_categories))

    tg_sender.send_status(
        f"Signal Generator started\n"
        f"Instrument: {cfg.trading.instrument.upper()} | "
        f"Expiry: {cfg.trading.expiry_seconds}s | "
        f"Assets: {', '.join(cfg.trading.assets)}"
    )

    log.info("Entering scan loop (interval=%ds)...", cfg.trading.scan_interval_seconds)

    while not shutdown["requested"]:
        try:
            run_scan(cfg, iq_client, tg_sender)
        except Exception as e:
            log.exception("Unexpected error in scan loop: %s", e)

        # Sleep in small increments so shutdown is responsive
        for _ in range(cfg.trading.scan_interval_seconds * 2):
            if shutdown["requested"]:
                break
            time.sleep(0.5)

    log.info("Shutting down...")
    iq_client.disconnect()
    tg_sender.disconnect()
    log.info("Done.")


if __name__ == "__main__":
    main()
