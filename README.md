# Signal Generator

Analyzes IQ Option market data and posts profitable signals to a Telegram channel.
Signals are compatible with the `signal_bot` (websocket-api branch) consumer.

## How it works

```
IQ Option WebSocket → Candle Data → Technical Analysis → Signal → Telegram Channel
                                          ↑
                           RSI + EMA + MACD + BB + Stochastic
                           (configurable confluence threshold)
```

## Quick Start

```bash
# 1. Install
make install

# 2. Configure
cp configs/config.example.yaml configs/config.yaml
# Edit configs/config.yaml with your credentials

# 3. Run
make run
```

## Configuration

Key settings in `configs/config.yaml`:

| Setting | Description |
|---|---|
| `trading.instrument` | `binary` or `blitz` |
| `trading.expiry_seconds` | `30`, `60`, `120`, `300` |
| `trading.assets` | List of currency pairs to monitor |
| `analysis.min_confidence` | Min % of indicators that must agree (0.0–1.0) |
| `signal.cooldown_seconds` | Min gap between signals per asset |
| `martingale.levels` | How many re-entry levels to include |

## Signal format (Mexy-compatible)

```
SIGNAL BOT 🤖

🚨 TRADE NOW!!

📈  🇪🇺 EUR/USD 🇺🇸 (OTC)
🕒  Timeframe: 2-min expiry
🤖  AI Confidence: 87%
🕰️  Entry Window: 02:32 PM
Direction: 🟢 BUY
📊  Instrument: BLITZ

📊  Martingale Levels:
• Level 1  →  02:34 PM
• Level 2  →  02:36 PM

📡  Signals (4/5):
  ▸ EMA9 crossed above EMA21 (BUY)
  ▸ RSI 28.3 ≤ 30 (oversold → BUY)
  ▸ MACD histogram flipped positive (BUY)
  ▸ Price at/below lower band (BUY)
```

## Docker

```bash
docker build -t signal-generator .
docker run -v $(pwd)/configs:/app/configs -v $(pwd)/logs:/app/logs signal-generator
```
