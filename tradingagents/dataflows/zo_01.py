"""Price-action data vendor backed by 01 Exchange (https://01.xyz).

01 ("zo") is a decentralized perpetual-futures exchange powered by N1's Nord
engine. It exposes a public, key-less REST API (mirrored as a TradingView UDF
datafeed) that serves OHLCV candles for its listed markets — including crypto
perps such as ``WLDUSD`` (Worldcoin) that Yahoo Finance does not price as a
perp. This module adapts that feed to the same shape the rest of the framework
expects from a price-action vendor, so 01 can be selected for ``get_stock_data``
and ``get_indicators`` exactly like ``yfinance`` or ``alpha_vantage``.

Endpoints used (host configurable via ``ZO_BASE_URL``):
    GET /info                              -> available markets/tokens
    GET /tv/history?symbol=&resolution=&from=&to=
                                           -> UDF OHLCV candles

The UDF history response is the standard TradingView shape::

    {"s": "ok", "t": [...], "o": [...], "h": [...], "l": [...], "c": [...], "v": [...]}
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Annotated

import pandas as pd
import requests

from .config import get_config
from .stockstats_utils import _clean_dataframe, indicator_window_report
from .symbol_utils import NoMarketDataError
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)

# Public mainnet host. Overridable so tests / self-hosted gateways can point
# elsewhere (e.g. the devnet host ``https://zo-devnet.n1.xyz``).
DEFAULT_BASE_URL = "https://zo-mainnet.n1.xyz"

# UDF daily candle resolution. Indicators and the verified snapshot operate on
# daily bars, matching the yfinance vendor.
_DAILY_RESOLUTION = "1D"

# 01's UDF datafeed caps how far back a single daily-history request may reach
# (~365 days). Stay just under it so the longest indicator (200 SMA) still has
# enough bars while every request stays within the allowed window.
_DAILY_MAX_DAYS = 360
_SECONDS_PER_DAY = 86400

# Cache the market list briefly so symbol validation does not hit /info on
# every call within a single run.
_MARKETS_CACHE: dict[str, object] = {"symbols": None, "fetched_at": 0.0}
_MARKETS_TTL_SECONDS = 300


def _base_url() -> str:
    return os.environ.get("ZO_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def normalize_zo_symbol(raw: str) -> str:
    """Map a user ticker to an 01 market symbol (e.g. ``WLD`` -> ``WLDUSD``).

    01 quotes every market against USD with no separator (``WLDUSD``,
    ``BTCUSD``). Users may type ``WLD``, ``WLD-USD``, ``WLD/USD`` or
    ``WLDUSDT``; all collapse to the exchange's ``<BASE>USD`` form.
    """
    if not isinstance(raw, str) or not raw.strip():
        return raw

    s = raw.strip().upper().rstrip("+")
    for sep in ("-", "/", ":", "_", " "):
        s = s.replace(sep, "")

    if s.endswith("USDT") or s.endswith("USDC"):
        s = s[:-4] + "USD"
    elif not s.endswith("USD"):
        s = s + "USD"
    return s


def _zo_get(path: str, params: dict | None = None, max_retries: int = 3,
            base_delay: float = 1.5, timeout: float = 20.0):
    """GET an 01 REST endpoint with exponential-backoff retries on transient errors."""
    url = f"{_base_url()}{path}"
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — uniform retry for network/HTTP errors
            last_exc = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "01 request to %s failed (%s); retrying in %.0fs (attempt %d/%d)",
                    path, exc, delay, attempt + 1, max_retries,
                )
                time.sleep(delay)
    raise last_exc


def _available_symbols() -> set[str]:
    """Set of market symbols listed on 01, cached for a short TTL."""
    now = time.time()
    cached = _MARKETS_CACHE.get("symbols")
    if cached is not None and now - float(_MARKETS_CACHE["fetched_at"]) < _MARKETS_TTL_SECONDS:
        return cached  # type: ignore[return-value]

    info = _zo_get("/info")
    symbols = {m["symbol"].upper() for m in info.get("markets", []) if m.get("symbol")}
    _MARKETS_CACHE["symbols"] = symbols
    _MARKETS_CACHE["fetched_at"] = now
    return symbols


def _fetch_candles(symbol: str, start_ts: int, end_ts: int,
                   resolution: str = _DAILY_RESOLUTION) -> pd.DataFrame:
    """Fetch UDF OHLCV candles for ``symbol`` in ``[start_ts, end_ts]`` (unix seconds)."""
    payload = _zo_get(
        "/tv/history",
        params={
            "symbol": symbol,
            "resolution": resolution,
            "from": int(start_ts),
            "to": int(end_ts),
        },
    )

    status = payload.get("s")
    if status == "no_data" or not payload.get("t"):
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    if status != "ok":
        raise RuntimeError(f"01 datafeed error for {symbol}: {payload}")

    times = payload.get("t", [])
    volumes = payload.get("v") or [0] * len(times)
    frame = pd.DataFrame(
        {
            "Date": pd.to_datetime(times, unit="s"),
            "Open": payload.get("o", []),
            "High": payload.get("h", []),
            "Low": payload.get("l", []),
            "Close": payload.get("c", []),
            "Volume": volumes,
        }
    )
    return frame


def load_zo_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Daily OHLCV from 01, cached per symbol and filtered to avoid look-ahead.

    Mirrors ``stockstats_utils.load_ohlcv`` (the yfinance loader): downloads a
    wide window once, caches it per symbol/day, then trims rows after
    ``curr_date`` so backtests never see future candles.
    """
    market_symbol = normalize_zo_symbol(symbol)
    safe_symbol = safe_ticker_component(market_symbol)

    if market_symbol not in _available_symbols():
        raise NoMarketDataError(
            symbol, market_symbol, "not listed on 01 Exchange (01.xyz)"
        )

    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    today_date = pd.Timestamp.today().normalize()
    # Bounded by the datafeed's max daily-history range (see _DAILY_MAX_DAYS).
    start_date = today_date - pd.Timedelta(days=_DAILY_MAX_DAYS)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today_date.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-ZO-data-{start_str}-{end_str}.csv",
    )

    data = None
    if os.path.exists(data_file):
        cached = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
        if not cached.empty and "Close" in cached.columns:
            data = cached

    if data is None:
        downloaded = _fetch_candles(
            market_symbol,
            int(start_date.timestamp()),
            # +1 day so the most recent (possibly partial) bar is included.
            int(today_date.timestamp()) + 86400,
        )
        if downloaded.empty or "Close" not in downloaded.columns:
            raise NoMarketDataError(
                symbol, market_symbol, "01 Exchange returned no candles"
            )
        downloaded.to_csv(data_file, index=False, encoding="utf-8")
        data = downloaded

    data = _clean_dataframe(data)
    data = data[data["Date"] <= curr_date_dt]
    return data


def get_stock(
    symbol: Annotated[str, "ticker symbol, e.g. WLD or WLDUSD"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Daily OHLCV CSV for ``symbol`` from 01 Exchange, filtered to the date range."""
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    market_symbol = normalize_zo_symbol(symbol)

    if market_symbol not in _available_symbols():
        raise NoMarketDataError(
            symbol, market_symbol, "not listed on 01 Exchange (01.xyz)"
        )

    # +1 day on each side so the inclusive [start, end] window is fully covered
    # regardless of bar timestamp alignment, then clamp the lower bound to the
    # datafeed's max daily-history range so over-wide requests don't 400.
    end_ts = int(end_dt.timestamp()) + _SECONDS_PER_DAY
    start_ts = int(start_dt.timestamp()) - _SECONDS_PER_DAY
    start_ts = max(start_ts, end_ts - _DAILY_MAX_DAYS * _SECONDS_PER_DAY)
    candles = _fetch_candles(market_symbol, start_ts, end_ts)
    if candles.empty:
        raise NoMarketDataError(
            symbol, market_symbol, f"no candles between {start_date} and {end_date}"
        )

    candles = _clean_dataframe(candles)
    mask = (candles["Date"] >= start_dt) & (candles["Date"] <= end_dt)
    candles = candles.loc[mask].sort_values("Date")
    if candles.empty:
        raise NoMarketDataError(
            symbol, market_symbol, f"no candles between {start_date} and {end_date}"
        )

    out = candles.copy()
    out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
    for col in ("Open", "High", "Low", "Close"):
        out[col] = out[col].round(6)
    csv_string = out.to_csv(index=False)

    label = market_symbol if market_symbol == symbol.upper() else f"{market_symbol} (from {symbol})"
    header = f"# 01 Exchange (01.xyz) price data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(out)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + csv_string


def get_indicator(
    symbol: Annotated[str, "ticker symbol, e.g. WLD or WLDUSD"],
    indicator: Annotated[str, "technical indicator to compute"],
    curr_date: Annotated[str, "The current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """Technical-indicator window for ``symbol`` computed from 01 Exchange candles."""
    data = load_zo_ohlcv(symbol, curr_date)
    return indicator_window_report(data, indicator, curr_date, look_back_days)
