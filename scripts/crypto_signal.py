#!/usr/bin/env python3
"""Multi-timeframe crypto trading signal generator.

A self-contained, dependency-free helper that produces an intraday trading
signal for a crypto pair by aligning a fast signal timeframe (default 15m)
with a higher-timeframe trend filter (default 1h).

It is intentionally separate from the LLM-driven TradingAgents pipeline: it
needs no API keys and only the Python standard library, so it can be run as a
quick technical screen, e.g.::

    python scripts/crypto_signal.py WLD --signal-tf 15m --trend-tf 1h
    python scripts/crypto_signal.py WLD --preset scalp   # 5m entry / 15m trend
    python scripts/crypto_signal.py WLD --heatmap        # + order-book liquidity heatmap
    python scripts/crypto_signal.py WLD --entry fade     # limit entry at the wall, stop beyond it

Data is pulled from public, key-less exchange endpoints (OKX, with Kraken as a
fallback). This is a research/technical tool, not financial advice.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #
@dataclass
class Candle:
    ts: int  # epoch seconds (open time)
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Indicators:
    ema_fast: list[Optional[float]] = field(default_factory=list)
    ema_slow: list[Optional[float]] = field(default_factory=list)
    rsi: list[Optional[float]] = field(default_factory=list)
    macd: list[Optional[float]] = field(default_factory=list)
    macd_signal: list[Optional[float]] = field(default_factory=list)
    macd_hist: list[Optional[float]] = field(default_factory=list)
    atr: list[Optional[float]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Timeframe handling
# --------------------------------------------------------------------------- #
# Canonical timeframe -> (okx bar, kraken interval in minutes)
_TF_MAP = {
    "1m": ("1m", 1),
    "3m": ("3m", 3),
    "5m": ("5m", 5),
    "15m": ("15m", 15),
    "30m": ("30m", 30),
    "1h": ("1H", 60),
    "2h": ("2H", 120),
    "4h": ("4H", 240),
    "6h": ("6H", 360),
    "12h": ("12H", 720),
    "1d": ("1D", 1440),
}


def normalize_tf(tf: str) -> str:
    key = tf.strip().lower().replace("min", "m").replace("hour", "h")
    if key not in _TF_MAP:
        raise ValueError(
            f"Unsupported timeframe '{tf}'. Choose from: {', '.join(_TF_MAP)}"
        )
    return key


# --------------------------------------------------------------------------- #
# Data fetching (key-less public endpoints)
# --------------------------------------------------------------------------- #
def _http_get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "tradingagents-signal/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_okx(symbol: str, tf: str, limit: int) -> list[Candle]:
    bar = _TF_MAP[tf][0]
    inst = f"{symbol.upper()}-USDT"
    url = (
        "https://www.okx.com/api/v5/market/candles"
        f"?instId={inst}&bar={bar}&limit={min(limit, 300)}"
    )
    data = _http_get_json(url)
    if str(data.get("code")) != "0" or not data.get("data"):
        raise RuntimeError(f"OKX returned no data for {inst} ({bar}): {data.get('msg')}")
    candles: list[Candle] = []
    for row in data["data"]:
        # row: [ts(ms), o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        confirm = row[8] if len(row) > 8 else "1"
        if confirm == "0":  # drop the still-forming candle
            continue
        candles.append(
            Candle(
                ts=int(int(row[0]) / 1000),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        )
    candles.sort(key=lambda c: c.ts)  # oldest -> newest
    return candles


def _fetch_kraken(symbol: str, tf: str, limit: int) -> list[Candle]:
    interval = _TF_MAP[tf][1]
    pair = f"{symbol.upper()}USD"
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    data = _http_get_json(url)
    if data.get("error"):
        raise RuntimeError(f"Kraken error for {pair}: {data['error']}")
    result = data.get("result", {})
    series_key = next((k for k in result if k != "last"), None)
    if not series_key:
        raise RuntimeError(f"Kraken returned no series for {pair}")
    candles: list[Candle] = []
    for row in result[series_key]:
        # row: [time, open, high, low, close, vwap, volume, count]
        candles.append(
            Candle(
                ts=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[6]),
            )
        )
    candles.sort(key=lambda c: c.ts)
    return candles[-limit:]


def _fetch_orderbook_okx(symbol: str, depth: int = 5000) -> tuple[list, list]:
    inst = f"{symbol.upper()}-USDT"
    url = f"https://www.okx.com/api/v5/market/books-full?instId={inst}&sz={depth}"
    data = _http_get_json(url)
    if str(data.get("code")) != "0" or not data.get("data"):
        # fall back to the shallower endpoint (max 400 levels)
        url = f"https://www.okx.com/api/v5/market/books?instId={inst}&sz=400"
        data = _http_get_json(url)
        if str(data.get("code")) != "0" or not data.get("data"):
            raise RuntimeError(f"OKX order book unavailable for {inst}: {data.get('msg')}")
    book = data["data"][0]
    bids = [(float(p), float(q)) for p, q, *_ in book["bids"]]
    asks = [(float(p), float(q)) for p, q, *_ in book["asks"]]
    return bids, asks


def fetch_orderbook(symbol: str) -> tuple[list, list, str]:
    """Return (bids, asks, source); each side is a list of (price, base_qty)."""
    bids, asks = _fetch_orderbook_okx(symbol)
    return bids, asks, "OKX"


def fetch_candles(symbol: str, tf: str, limit: int = 300) -> tuple[list[Candle], str]:
    """Fetch candles, returning (candles, source). Tries OKX then Kraken."""
    errors = []
    for name, fn in (("OKX", _fetch_okx), ("Kraken", _fetch_kraken)):
        try:
            candles = fn(symbol, tf, limit)
            if candles:
                return candles, name
        except (urllib.error.URLError, RuntimeError, ValueError, KeyError) as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError(
        f"Could not fetch {symbol} {tf} candles from any source. " + " | ".join(errors)
    )


# --------------------------------------------------------------------------- #
# Indicators (pure Python)
# --------------------------------------------------------------------------- #
def ema(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], period: int = 14) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line: list[Optional[float]] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    # signal line is EMA of the defined macd values
    defined = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line: list[Optional[float]] = [None] * len(closes)
    hist: list[Optional[float]] = [None] * len(closes)
    if len(defined) >= signal:
        vals = [v for _, v in defined]
        sig_vals = ema(vals, signal)
        for (idx, _), s in zip(defined, sig_vals):
            signal_line[idx] = s
        for i in range(len(closes)):
            if macd_line[i] is not None and signal_line[i] is not None:
                hist[i] = macd_line[i] - signal_line[i]
    return macd_line, signal_line, hist


def atr(candles: list[Candle], period: int = 14) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(candles)
    if len(candles) <= period:
        return out
    trs: list[float] = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev_close = candles[i - 1].close
        tr = max(
            c.high - c.low,
            abs(c.high - prev_close),
            abs(c.low - prev_close),
        )
        trs.append(tr)
    # trs is offset by 1 relative to candles
    first = sum(trs[:period]) / period
    out[period] = first
    prev = first
    for i in range(period + 1, len(candles)):
        prev = (prev * (period - 1) + trs[i - 1]) / period
        out[i] = prev
    return out


def compute_indicators(
    candles: list[Candle], ema_fast: int, ema_slow: int
) -> Indicators:
    closes = [c.close for c in candles]
    macd_line, macd_sig, macd_hist = macd(closes)
    return Indicators(
        ema_fast=ema(closes, ema_fast),
        ema_slow=ema(closes, ema_slow),
        rsi=rsi(closes, 14),
        macd=macd_line,
        macd_signal=macd_sig,
        macd_hist=macd_hist,
        atr=atr(candles, 14),
    )


# --------------------------------------------------------------------------- #
# Signal logic
# --------------------------------------------------------------------------- #
@dataclass
class TrendRead:
    direction: str  # "UP", "DOWN", "SIDEWAYS"
    score: int
    reasons: list[str]


def read_trend(candles: list[Candle], ind: Indicators) -> TrendRead:
    i = len(candles) - 1
    reasons: list[str] = []
    score = 0
    close = candles[i].close
    ef, es = ind.ema_fast[i], ind.ema_slow[i]
    if ef is not None and es is not None:
        if ef > es:
            score += 1
            reasons.append(f"EMA fast {ef:.4f} > EMA slow {es:.4f} (bullish)")
        else:
            score -= 1
            reasons.append(f"EMA fast {ef:.4f} < EMA slow {es:.4f} (bearish)")
        if close > es:
            score += 1
            reasons.append("price above slow EMA")
        else:
            score -= 1
            reasons.append("price below slow EMA")
    mh = ind.macd_hist[i]
    if mh is not None:
        if mh > 0:
            score += 1
            reasons.append("MACD histogram positive")
        else:
            score -= 1
            reasons.append("MACD histogram negative")
    r = ind.rsi[i]
    if r is not None:
        if r >= 55:
            score += 1
            reasons.append(f"RSI {r:.1f} >= 55 (momentum up)")
        elif r <= 45:
            score -= 1
            reasons.append(f"RSI {r:.1f} <= 45 (momentum down)")
        else:
            reasons.append(f"RSI {r:.1f} neutral")

    if score >= 2:
        direction = "UP"
    elif score <= -2:
        direction = "DOWN"
    else:
        direction = "SIDEWAYS"
    return TrendRead(direction=direction, score=score, reasons=reasons)


def swing_levels(candles: list[Candle], lookback: int = 20) -> tuple[float, float]:
    """Return (recent_high, recent_low) over the last `lookback` candles."""
    window = candles[-lookback:] if len(candles) >= lookback else candles
    return max(c.high for c in window), min(c.low for c in window)


@dataclass
class Signal:
    symbol: str
    side: str  # LONG / SHORT
    status: str  # ACTIVE / PENDING
    price: float
    bias_reason: str = ""
    entry: Optional[float] = None
    trigger: str = ""
    trigger_price: Optional[float] = None
    invalidation: Optional[float] = None
    invalidation_note: str = ""
    stop_loss: Optional[float] = None
    take_profit: list[float] = field(default_factory=list)
    risk_reward: Optional[float] = None
    confidence: str = "low"
    trend: Optional[TrendRead] = None
    entry_reasons: list[str] = field(default_factory=list)
    signal_tf: str = ""
    trend_tf: str = ""
    source: str = ""
    atr: Optional[float] = None
    recent_high: Optional[float] = None
    recent_low: Optional[float] = None


def build_signal(
    symbol: str,
    signal_tf: str,
    trend_tf: str,
    sig_candles: list[Candle],
    sig_ind: Indicators,
    trend: TrendRead,
    source: str,
    sl_atr_mult: float = 1.5,
    tp_mults: tuple[float, float] = (1.5, 3.0),
    lookback: int = 20,
    entry_style: str = "momentum",
    heatmap: Optional[Heatmap] = None,
) -> Signal:
    i = len(sig_candles) - 1
    price = sig_candles[i].close
    a = sig_ind.atr[i] or 0.0
    r = sig_ind.rsi[i]
    ef, es = sig_ind.ema_fast[i], sig_ind.ema_slow[i]
    mh = sig_ind.macd_hist[i]
    mh_prev = sig_ind.macd_hist[i - 1] if i > 0 else None
    hi, lo = swing_levels(sig_candles, lookback)

    # ----- 1. Directional bias (always pick a side) ----------------------- #
    if trend.direction == "UP":
        side, bias_reason = "LONG", f"{trend_tf} trend is UP (score {trend.score:+d})"
    elif trend.direction == "DOWN":
        side, bias_reason = "SHORT", f"{trend_tf} trend is DOWN (score {trend.score:+d})"
    else:  # SIDEWAYS -> lean on score sign, then price vs slow EMA
        if trend.score > 0 or (trend.score == 0 and es is not None and price >= es):
            side = "LONG"
        else:
            side = "SHORT"
        bias_reason = f"{trend_tf} trend is SIDEWAYS; leaning {side} on net bias"

    # ----- 2. Are momentum conditions already aligned now? ---------------- #
    reasons: list[str] = []
    if side == "LONG":
        cond_ema = ef is not None and es is not None and ef > es
        cond_macd = mh is not None and mh > 0
        cond_rsi = r is not None and 45 <= r <= 75
        if cond_ema:
            reasons.append(f"{signal_tf} EMA fast > slow")
        if cond_macd:
            reasons.append(f"{signal_tf} MACD histogram positive")
        if mh is not None and mh_prev is not None and mh > mh_prev:
            reasons.append(f"{signal_tf} MACD momentum rising")
        if cond_rsi and r is not None:
            reasons.append(f"{signal_tf} RSI {r:.1f} healthy (not overbought)")
        aligned = cond_ema and cond_macd and cond_rsi
    else:
        cond_ema = ef is not None and es is not None and ef < es
        cond_macd = mh is not None and mh < 0
        cond_rsi = r is not None and 25 <= r <= 55
        if cond_ema:
            reasons.append(f"{signal_tf} EMA fast < slow")
        if cond_macd:
            reasons.append(f"{signal_tf} MACD histogram negative")
        if mh is not None and mh_prev is not None and mh < mh_prev:
            reasons.append(f"{signal_tf} MACD momentum falling")
        if cond_rsi and r is not None:
            reasons.append(f"{signal_tf} RSI {r:.1f} healthy (not oversold)")
        aligned = cond_ema and cond_macd and cond_rsi

    status = "ACTIVE" if aligned else "PENDING"

    sig = Signal(
        symbol=symbol.upper(),
        side=side,
        status=status,
        price=price,
        bias_reason=bias_reason,
        trend=trend,
        signal_tf=signal_tf,
        trend_tf=trend_tf,
        source=source,
        atr=a or None,
        recent_high=hi,
        recent_low=lo,
        entry_reasons=reasons or [f"{signal_tf} momentum not yet aligned with bias"],
    )

    # ----- 3. Trigger, invalidation, entry, stop, targets ----------------- #
    buf = 0.3 * a  # small buffer beyond structure

    # Fade / mean-reversion: enter AT the opposing liquidity wall with the stop
    # placed BEYOND that wall, so a stop-hunt wick into the wall does not eject
    # the position. Requires a heatmap with the relevant wall.
    fade_wall = None
    if entry_style == "fade" and heatmap is not None:
        fade_wall = heatmap.resistance if side == "SHORT" else heatmap.support
    if entry_style == "fade" and fade_wall is not None:
        wall = fade_wall
        # stop sits beyond the far edge of the wall, padded by ~1 ATR
        wall_pad = max(a, buf)
        if side == "SHORT":
            sig.entry = wall.mid
            sig.trigger = f"Limit SHORT into resistance wall ~{fmt(wall.mid)} (fade)"
            sig.trigger_price = wall.mid
            sig.stop_loss = wall.high + wall_pad
            sig.invalidation = wall.high
            sig.invalidation_note = (
                f"acceptance/close ABOVE resistance wall {fmt(wall.high)} cancels the short"
            )
            risk = sig.stop_loss - sig.entry
            if risk > 0:
                sig.take_profit = [sig.entry - tp_mults[0] * risk, sig.entry - tp_mults[1] * risk]
                sig.risk_reward = tp_mults[0]
        else:
            sig.entry = wall.mid
            sig.trigger = f"Limit LONG into support wall ~{fmt(wall.mid)} (fade)"
            sig.trigger_price = wall.mid
            sig.stop_loss = wall.low - wall_pad
            sig.invalidation = wall.low
            sig.invalidation_note = (
                f"acceptance/close BELOW support wall {fmt(wall.low)} cancels the long"
            )
            risk = sig.entry - sig.stop_loss
            if risk > 0:
                sig.take_profit = [sig.entry + tp_mults[0] * risk, sig.entry + tp_mults[1] * risk]
                sig.risk_reward = tp_mults[0]
        sig.status = "PENDING"  # resting limit order at the wall
        sig.bias_reason += " | fade entry at order-book wall"
        strength = abs(trend.score) + len(reasons)
        sig.confidence = "high" if strength >= 6 else "medium" if strength >= 4 else "low"
        return sig

    if side == "LONG":
        if status == "ACTIVE":
            sig.entry = price
            sig.trigger = f"Market / immediate at ~{fmt(price)} (momentum already aligned)"
            sig.trigger_price = price
        else:
            sig.entry = hi
            sig.trigger = f"{signal_tf} close ABOVE recent high {fmt(hi)}"
            sig.trigger_price = hi
        sig.invalidation = lo
        sig.invalidation_note = f"{signal_tf} close BELOW recent low {fmt(lo)} cancels the long"
        struct_sl = lo - buf
        atr_sl = sig.entry - sl_atr_mult * a if a else struct_sl
        sig.stop_loss = min(struct_sl, atr_sl) if a else struct_sl
        risk = sig.entry - sig.stop_loss
        if risk > 0:
            sig.take_profit = [sig.entry + tp_mults[0] * risk, sig.entry + tp_mults[1] * risk]
            sig.risk_reward = tp_mults[0]
    else:
        if status == "ACTIVE":
            sig.entry = price
            sig.trigger = f"Market / immediate at ~{fmt(price)} (momentum already aligned)"
            sig.trigger_price = price
        else:
            sig.entry = lo
            sig.trigger = f"{signal_tf} close BELOW recent low {fmt(lo)}"
            sig.trigger_price = lo
        sig.invalidation = hi
        sig.invalidation_note = f"{signal_tf} close ABOVE recent high {fmt(hi)} cancels the short"
        struct_sl = hi + buf
        atr_sl = sig.entry + sl_atr_mult * a if a else struct_sl
        sig.stop_loss = max(struct_sl, atr_sl) if a else struct_sl
        risk = sig.stop_loss - sig.entry
        if risk > 0:
            sig.take_profit = [sig.entry - tp_mults[0] * risk, sig.entry - tp_mults[1] * risk]
            sig.risk_reward = tp_mults[0]

    strength = abs(trend.score) + len(reasons)
    sig.confidence = "high" if strength >= 6 else "medium" if strength >= 4 else "low"
    return sig


# --------------------------------------------------------------------------- #
# Order-book liquidity heatmap
# --------------------------------------------------------------------------- #
@dataclass
class HeatBucket:
    low: float
    high: float
    bid: float  # bid notional (quote)
    ask: float  # ask notional (quote)

    @property
    def total(self) -> float:
        return self.bid + self.ask

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


@dataclass
class Heatmap:
    symbol: str
    source: str
    mid: float
    range_pct: float
    buckets: list[HeatBucket]
    bid_notional: float
    ask_notional: float
    support: Optional[HeatBucket]  # biggest bid wall below price
    resistance: Optional[HeatBucket]  # biggest ask wall above price

    @property
    def imbalance(self) -> float:
        total = self.bid_notional + self.ask_notional
        return 0.0 if total == 0 else (self.bid_notional - self.ask_notional) / total


def build_heatmap(
    symbol: str,
    bids: list,
    asks: list,
    source: str,
    range_pct: float = 0.03,
    bins: int = 24,
) -> Heatmap:
    best_bid = bids[0][0] if bids else 0.0
    best_ask = asks[0][0] if asks else 0.0
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else (best_bid or best_ask)
    lo, hi = mid * (1 - range_pct), mid * (1 + range_pct)
    width = (hi - lo) / bins
    bid_n = [0.0] * bins
    ask_n = [0.0] * bins

    def idx_of(p: float) -> Optional[int]:
        if p < lo or p >= hi:
            return None
        return min(int((p - lo) / width), bins - 1)

    for price, qty in bids:
        j = idx_of(price)
        if j is not None:
            bid_n[j] += price * qty
    for price, qty in asks:
        j = idx_of(price)
        if j is not None:
            ask_n[j] += price * qty

    buckets = [
        HeatBucket(low=lo + k * width, high=lo + (k + 1) * width, bid=bid_n[k], ask=ask_n[k])
        for k in range(bins)
    ]
    support = max(
        (b for b in buckets if b.high <= mid and b.bid > 0), key=lambda b: b.bid, default=None
    )
    resistance = max(
        (b for b in buckets if b.low >= mid and b.ask > 0), key=lambda b: b.ask, default=None
    )
    return Heatmap(
        symbol=symbol.upper(),
        source=source,
        mid=mid,
        range_pct=range_pct,
        buckets=buckets,
        bid_notional=sum(bid_n),
        ask_notional=sum(ask_n),
        support=support,
        resistance=resistance,
    )


def _short_notional(x: float) -> str:
    if x >= 1_000_000:
        return f"{x / 1_000_000:.1f}M"
    if x >= 1_000:
        return f"{x / 1_000:.0f}k"
    return f"{x:.0f}"


def render_heatmap(hm: Heatmap) -> str:
    peak = max((b.total for b in hm.buckets), default=0.0) or 1.0
    width_chars = 30
    lines = [
        "=" * 60,
        f" LIQUIDITY HEATMAP  {hm.symbol}/USDT  (order book, {hm.source})",
        f" mid {fmt(hm.mid)}  |  +/-{hm.range_pct * 100:.1f}%  |  bar = bid/ask notional",
        "=" * 60,
    ]
    for b in reversed(hm.buckets):  # high price at top
        side = "A" if b.ask >= b.bid else "B"  # ask wall (resistance) vs bid wall (support)
        fill = int(round(b.total / peak * width_chars))
        bar = ("#" if side == "A" else "=") * fill
        mark = ""
        if hm.resistance and b is hm.resistance:
            mark = " <== resistance wall"
        elif hm.support and b is hm.support:
            mark = " <== support wall"
        lines.append(
            f" {fmt(b.mid):>10} {side} |{bar:<{width_chars}} {_short_notional(b.total):>6}{mark}"
        )
    imb = hm.imbalance
    lean = "BID-heavy (support/bullish)" if imb > 0.08 else (
        "ASK-heavy (resistance/bearish)" if imb < -0.08 else "balanced"
    )
    lines.append("-" * 60)
    lines.append(
        f" Bid liq: {_short_notional(hm.bid_notional)}   "
        f"Ask liq: {_short_notional(hm.ask_notional)}   "
        f"imbalance {imb:+.0%} -> {lean}"
    )
    if hm.support:
        lines.append(f" Nearest support wall : ~{fmt(hm.support.mid)} ({_short_notional(hm.support.bid)})")
    if hm.resistance:
        lines.append(f" Nearest resistance   : ~{fmt(hm.resistance.mid)} ({_short_notional(hm.resistance.ask)})")
    lines.append("=" * 60)
    lines.append(" Order-book snapshot - resting liquidity can be pulled/spoofed.")
    return "\n".join(lines)


def heatmap_to_dict(hm: Heatmap) -> dict:
    return {
        "symbol": hm.symbol,
        "source": hm.source,
        "mid": hm.mid,
        "range_pct": hm.range_pct,
        "bid_notional": hm.bid_notional,
        "ask_notional": hm.ask_notional,
        "imbalance": hm.imbalance,
        "support_wall": None if not hm.support else {"price": hm.support.mid, "notional": hm.support.bid},
        "resistance_wall": None if not hm.resistance else {"price": hm.resistance.mid, "notional": hm.resistance.ask},
        "buckets": [
            {"low": b.low, "high": b.high, "bid": b.bid, "ask": b.ask} for b in hm.buckets
        ],
    }


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def fmt(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    if abs(x) >= 100:
        return f"{x:,.2f}"
    return f"{x:.4f}"


def render_text(sig: Signal) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    arrow = {"UP": "^", "DOWN": "v", "SIDEWAYS": "-"}[sig.trend.direction]
    lines = [
        "=" * 60,
        f" SIGNAL  {sig.symbol}/USDT   ({sig.signal_tf} entry / {sig.trend_tf} trend)",
        "=" * 60,
        f" Time        : {now}",
        f" Data source : {sig.source}",
        f" Last price  : {fmt(sig.price)}",
        f" {sig.trend_tf} trend    : {sig.trend.direction} [{arrow}] (score {sig.trend.score:+d})",
        "-" * 60,
        f" DECISION    : {sig.side}  [{sig.status}]  ({sig.confidence} confidence)",
        f" Bias        : {sig.bias_reason}",
        "-" * 60,
        f" TRIGGER     : {sig.trigger}",
        f" Entry       : {fmt(sig.entry)}",
        f" INVALIDATION: {sig.invalidation_note}",
        f" Stop loss   : {fmt(sig.stop_loss)}",
    ]
    if sig.take_profit:
        lines.append(f" Take profit : TP1 {fmt(sig.take_profit[0])}  |  TP2 {fmt(sig.take_profit[1])}")
    lines.append(
        f" ATR({sig.signal_tf}): {fmt(sig.atr)}   R:R(TP1) ~ {sig.risk_reward}   "
        f"range[{fmt(sig.recent_low)} - {fmt(sig.recent_high)}]"
    )
    lines.append("-" * 60)
    lines.append(f" {sig.trend_tf} trend rationale:")
    for rsn in sig.trend.reasons:
        lines.append(f"   - {rsn}")
    lines.append(f" {sig.signal_tf} momentum:")
    for rsn in sig.entry_reasons:
        lines.append(f"   - {rsn}")
    if sig.status == "PENDING":
        lines.append(" NOTE: PENDING setup - wait for the TRIGGER before entering.")
    lines.append("=" * 60)
    lines.append(" Research/technical screen only - not financial advice.")
    return "\n".join(lines)


def to_dict(sig: Signal) -> dict:
    return {
        "symbol": sig.symbol,
        "signal_timeframe": sig.signal_tf,
        "trend_timeframe": sig.trend_tf,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": sig.source,
        "price": sig.price,
        "trend": {
            "direction": sig.trend.direction,
            "score": sig.trend.score,
            "reasons": sig.trend.reasons,
        },
        "side": sig.side,
        "status": sig.status,
        "confidence": sig.confidence,
        "bias_reason": sig.bias_reason,
        "trigger": sig.trigger,
        "trigger_price": sig.trigger_price,
        "entry": sig.entry,
        "invalidation": sig.invalidation,
        "invalidation_note": sig.invalidation_note,
        "stop_loss": sig.stop_loss,
        "take_profit": sig.take_profit,
        "atr": sig.atr,
        "risk_reward_tp1": sig.risk_reward,
        "recent_high": sig.recent_high,
        "recent_low": sig.recent_low,
        "momentum_reasons": sig.entry_reasons,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
PRESETS = {
    # name -> tuned defaults for a trading style
    "scalp": {
        "signal_tf": "5m", "trend_tf": "15m", "ema_fast": 5, "ema_slow": 13,
        "sl_atr_mult": 1.0, "tp_mults": (1.0, 2.0), "lookback": 10,
    },
    "intraday": {
        "signal_tf": "15m", "trend_tf": "1h", "ema_fast": 9, "ema_slow": 21,
        "sl_atr_mult": 1.5, "tp_mults": (1.5, 3.0), "lookback": 20,
    },
    "swing": {
        "signal_tf": "1h", "trend_tf": "4h", "ema_fast": 21, "ema_slow": 55,
        "sl_atr_mult": 2.0, "tp_mults": (2.0, 4.0), "lookback": 30,
    },
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Multi-timeframe crypto trading signal generator (key-less).",
    )
    parser.add_argument("symbol", help="Base symbol, e.g. WLD, BTC, ETH")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default="intraday",
        help="Trading style preset (default intraday). 'scalp' uses 5m/15m and tight stops.",
    )
    parser.add_argument("--signal-tf", default=None, help="Override entry timeframe")
    parser.add_argument("--trend-tf", default=None, help="Override trend filter timeframe")
    parser.add_argument("--ema-fast", type=int, default=None, help="Override fast EMA period")
    parser.add_argument("--ema-slow", type=int, default=None, help="Override slow EMA period")
    parser.add_argument(
        "--entry",
        choices=["momentum", "fade"],
        default="momentum",
        help=(
            "Entry style: 'momentum' = breakout/breakdown beyond the swing "
            "(default); 'fade' = limit entry AT the order-book wall with the "
            "stop placed BEYOND it (needs heatmap, auto-enabled)."
        ),
    )
    parser.add_argument(
        "--heatmap",
        action="store_true",
        help="Add an order-book liquidity heatmap (support/resistance walls)",
    )
    parser.add_argument(
        "--hm-range", type=float, default=3.0,
        help="Heatmap price range each side in percent (default 3.0)",
    )
    parser.add_argument(
        "--hm-bins", type=int, default=24, help="Heatmap price buckets (default 24)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args(argv)

    preset = PRESETS[args.preset]
    signal_tf_raw = args.signal_tf or preset["signal_tf"]
    trend_tf_raw = args.trend_tf or preset["trend_tf"]
    ema_fast = args.ema_fast or preset["ema_fast"]
    ema_slow = args.ema_slow or preset["ema_slow"]
    sl_atr_mult = preset["sl_atr_mult"]
    tp_mults = preset["tp_mults"]
    lookback = preset["lookback"]

    try:
        signal_tf = normalize_tf(signal_tf_raw)
        trend_tf = normalize_tf(trend_tf_raw)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        trend_candles, trend_src = fetch_candles(args.symbol, trend_tf)
        sig_candles, sig_src = fetch_candles(args.symbol, signal_tf)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    trend_ind = compute_indicators(trend_candles, ema_fast, ema_slow)
    sig_ind = compute_indicators(sig_candles, ema_fast, ema_slow)
    trend = read_trend(trend_candles, trend_ind)

    # Fade entries need the order-book walls, so fetch the heatmap up front.
    heatmap = None
    want_heatmap = args.heatmap or args.entry == "fade"
    if want_heatmap:
        try:
            bids, asks, ob_src = fetch_orderbook(args.symbol)
            heatmap = build_heatmap(
                args.symbol, bids, asks, ob_src,
                range_pct=args.hm_range / 100.0, bins=args.hm_bins,
            )
        except (urllib.error.URLError, RuntimeError, ValueError, KeyError) as exc:
            print(f"Warning: heatmap unavailable: {exc}", file=sys.stderr)

    if args.entry == "fade" and heatmap is None:
        print(
            "Warning: fade entry needs order-book data; falling back to momentum.",
            file=sys.stderr,
        )

    signal = build_signal(
        args.symbol, signal_tf, trend_tf, sig_candles, sig_ind, trend, sig_src,
        sl_atr_mult=sl_atr_mult, tp_mults=tp_mults, lookback=lookback,
        entry_style=args.entry, heatmap=heatmap,
    )

    if args.json:
        out = to_dict(signal)
        if heatmap is not None:
            out["heatmap"] = heatmap_to_dict(heatmap)
        print(json.dumps(out, indent=2))
    else:
        print(render_text(signal))
        if heatmap is not None:
            print()
            print(render_heatmap(heatmap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
