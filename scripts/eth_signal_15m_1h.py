#!/usr/bin/env python3
"""ETH 15m entry signal with 1h trend filter."""

from datetime import datetime, timezone

import yfinance as yf


def calc_indicators(df):
    df = df.copy()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

    return df


def analyze_1h_trend(df_1h):
    last = df_1h.iloc[-1]
    signals = []

    if last["Close"] > last["EMA20"] > last["EMA50"]:
        signals.append("BULLISH: Price > EMA20 > EMA50")
    elif last["Close"] < last["EMA20"] < last["EMA50"]:
        signals.append("BEARISH: Price < EMA20 < EMA50")
    else:
        signals.append("MIXED: EMAs not aligned")

    if last["MACD"] > last["MACD_signal"]:
        signals.append("MACD bullish")
    else:
        signals.append("MACD bearish")

    if last["RSI"] > 50:
        signals.append(f"RSI bullish ({last['RSI']:.1f})")
    else:
        signals.append(f"RSI bearish ({last['RSI']:.1f})")

    bullish = sum(1 for s in signals if "bullish" in s.lower() or "BULLISH" in s)
    bearish = sum(1 for s in signals if "bearish" in s.lower() or "BEARISH" in s)

    if bullish >= 2:
        trend = "BULLISH"
    elif bearish >= 2:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    return trend, last, signals


def analyze_15m_entry(df_15m, trend_1h):
    last = df_15m.iloc[-1]
    prev = df_15m.iloc[-2]
    entry_signals = []

    if last["RSI"] < 30:
        entry_signals.append("RSI oversold")
    elif last["RSI"] > 70:
        entry_signals.append("RSI overbought")
    elif 40 < last["RSI"] < 60:
        entry_signals.append("RSI neutral zone")

    if last["MACD_hist"] > 0 and prev["MACD_hist"] <= 0:
        entry_signals.append("MACD bullish crossover")
    elif last["MACD_hist"] < 0 and prev["MACD_hist"] >= 0:
        entry_signals.append("MACD bearish crossover")

    if last["EMA20"] > last["EMA50"] and prev["EMA20"] <= prev["EMA50"]:
        entry_signals.append("EMA20/50 golden cross")
    elif last["EMA20"] < last["EMA50"] and prev["EMA20"] >= prev["EMA50"]:
        entry_signals.append("EMA20/50 death cross")

    if last["Close"] > last["EMA20"]:
        entry_signals.append("Price above EMA20")
    else:
        entry_signals.append("Price below EMA20")

    signal = "WAIT"
    reason = "No clear setup"

    if trend_1h == "BULLISH":
        if last["RSI"] < 40 and last["MACD_hist"] > prev["MACD_hist"]:
            signal, reason = "LONG", "1H bullish + 15M RSI pullback + MACD improving"
        elif last["Close"] > last["EMA20"] and last["MACD_hist"] > 0:
            signal, reason = "LONG", "1H bullish + 15M above EMA20 + MACD positive"
        elif last["RSI"] > 70:
            signal, reason = "WAIT", "1H bullish but 15M overbought - wait pullback"
        else:
            signal, reason = "WAIT", "1H bullish but no clear 15M entry"
    elif trend_1h == "BEARISH":
        if last["RSI"] > 60 and last["MACD_hist"] < prev["MACD_hist"]:
            signal, reason = "SHORT", "1H bearish + 15M RSI bounce + MACD weakening"
        elif last["Close"] < last["EMA20"] and last["MACD_hist"] < 0:
            signal, reason = "SHORT", "1H bearish + 15M below EMA20 + MACD negative"
        elif last["RSI"] < 30:
            signal, reason = "WAIT", "1H bearish but 15M oversold - wait bounce"
        else:
            signal, reason = "WAIT", "1H bearish but no clear 15M entry"
    else:
        signal, reason = "WAIT", "1H trend unclear - no trade"

    return signal, reason, last, entry_signals


def main():
    ticker = yf.Ticker("ETH-USD")
    df_1h = calc_indicators(ticker.history(period="7d", interval="1h"))
    df_15m = calc_indicators(ticker.history(period="5d", interval="15m"))

    trend_1h, last_1h, trend_signals = analyze_1h_trend(df_1h)
    signal, reason, last_15m, entry_signals = analyze_15m_entry(df_15m, trend_1h)

    recent = df_15m.tail(48)
    support = recent["Low"].min()
    resistance = recent["High"].max()

    print("=" * 50)
    print("ETH-USD SIGNAL | 15M Entry + 1H Trend")
    print("=" * 50)
    print(f"Timestamp : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Price     : ${last_15m['Close']:.2f}")
    print()
    print("--- 1H TREND ---")
    print(f"Trend     : {trend_1h}")
    print(f"EMA20     : ${last_1h['EMA20']:.2f}")
    print(f"EMA50     : ${last_1h['EMA50']:.2f}")
    print(f"RSI(14)   : {last_1h['RSI']:.1f}")
    print(f"MACD Hist : {last_1h['MACD_hist']:.4f}")
    for s in trend_signals:
        print(f"  • {s}")
    print()
    print("--- 15M ENTRY ---")
    print(f"EMA20     : ${last_15m['EMA20']:.2f}")
    print(f"EMA50     : ${last_15m['EMA50']:.2f}")
    print(f"RSI(14)   : {last_15m['RSI']:.1f}")
    print(f"MACD Hist : {last_15m['MACD_hist']:.4f}")
    for s in entry_signals:
        print(f"  • {s}")
    print()
    print("--- FINAL SIGNAL ---")
    print(f">>> {signal} <<<")
    print(f"Reason    : {reason}")
    print(f"Support   : ${support:.2f} (12h low)")
    print(f"Resistance: ${resistance:.2f} (12h high)")
    print("=" * 50)


if __name__ == "__main__":
    main()
