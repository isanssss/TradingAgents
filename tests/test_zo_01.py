"""Tests for the 01 Exchange (01.xyz) price-action vendor.

Network access is mocked at ``zo_01._zo_get`` so the suite is hermetic: the
``/info`` market list and ``/tv/history`` UDF candle responses are synthesized
in-process.
"""

import copy
import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

import pytest

import tradingagents.default_config as default_config
from tradingagents.dataflows import interface, zo_01
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.symbol_utils import NoMarketDataError


def _fake_info(symbols=("BTCUSD", "ETHUSD", "WLDUSD")):
    return {"markets": [{"symbol": s} for s in symbols], "tokens": []}


def _fake_history(days=40, end=None, start_price=0.30):
    """Synthesize a TradingView UDF daily-candle payload."""
    end = end or datetime.utcnow().date()
    t, o, h, l, c, v = [], [], [], [], [], []
    price = start_price
    for i in range(days):
        day = datetime(end.year, end.month, end.day) - timedelta(days=days - 1 - i)
        ts = int(day.timestamp())
        price = price * (1.0 + (0.01 if i % 2 == 0 else -0.005))
        t.append(ts)
        o.append(round(price, 5))
        h.append(round(price * 1.03, 5))
        l.append(round(price * 0.97, 5))
        c.append(round(price * 1.005, 5))
        v.append(round(1000 + i * 10.0, 4))
    return {"s": "ok", "t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _router(info=None, history=None):
    """Return a fake ``_zo_get`` that dispatches by request path."""
    info = info or _fake_info()
    history = history if history is not None else _fake_history()

    def _impl(path, params=None, **kwargs):
        if path == "/info":
            return info
        if path == "/tv/history":
            return history
        raise AssertionError(f"unexpected path {path}")

    return _impl


@pytest.mark.unit
class NormalizeSymbolTests(unittest.TestCase):
    def test_variants_collapse_to_market_symbol(self):
        self.assertEqual(zo_01.normalize_zo_symbol("WLD"), "WLDUSD")
        self.assertEqual(zo_01.normalize_zo_symbol("wld"), "WLDUSD")
        self.assertEqual(zo_01.normalize_zo_symbol("WLD-USD"), "WLDUSD")
        self.assertEqual(zo_01.normalize_zo_symbol("WLD/USD"), "WLDUSD")
        self.assertEqual(zo_01.normalize_zo_symbol("WLDUSDT"), "WLDUSD")
        self.assertEqual(zo_01.normalize_zo_symbol("WLDUSDC"), "WLDUSD")
        self.assertEqual(zo_01.normalize_zo_symbol("WLDUSD"), "WLDUSD")
        self.assertEqual(zo_01.normalize_zo_symbol("BTC/USD"), "BTCUSD")


@pytest.mark.unit
class GetStockTests(unittest.TestCase):
    def setUp(self):
        zo_01._MARKETS_CACHE.update({"symbols": None, "fetched_at": 0.0})

    def test_returns_filtered_csv_with_header(self):
        with mock.patch.object(zo_01, "_zo_get", _router()):
            out = zo_01.get_stock("WLD", "2026-05-20", "2026-06-01")
        self.assertIn("01 Exchange (01.xyz)", out)
        self.assertIn("WLDUSD (from WLD)", out)
        self.assertIn("Date,Open,High,Low,Close,Volume", out)
        # Every data row must fall within the requested range.
        rows = [r for r in out.splitlines() if r and r[0].isdigit()]
        self.assertTrue(rows)
        for row in rows:
            day = row.split(",")[0]
            self.assertGreaterEqual(day, "2026-05-20")
            self.assertLessEqual(day, "2026-06-01")

    def test_unlisted_symbol_raises_no_data(self):
        with mock.patch.object(zo_01, "_zo_get", _router(info=_fake_info(("BTCUSD",)))):
            with self.assertRaises(NoMarketDataError):
                zo_01.get_stock("WLD", "2026-05-20", "2026-06-01")

    def test_empty_history_raises_no_data(self):
        with mock.patch.object(
            zo_01, "_zo_get", _router(history={"s": "no_data"})
        ):
            with self.assertRaises(NoMarketDataError):
                zo_01.get_stock("WLD", "2026-05-20", "2026-06-01")


@pytest.mark.unit
class GetIndicatorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = os.path.join(os.path.dirname(__file__), "_tmp_zo_cache")
        os.makedirs(self._tmp, exist_ok=True)
        set_config({"data_cache_dir": self._tmp})
        zo_01._MARKETS_CACHE.update({"symbols": None, "fetched_at": 0.0})

    def tearDown(self):
        for f in os.listdir(self._tmp):
            os.remove(os.path.join(self._tmp, f))
        os.rmdir(self._tmp)

    def test_rsi_window_reports_numeric_values(self):
        today = datetime.utcnow().date().strftime("%Y-%m-%d")
        with mock.patch.object(zo_01, "_zo_get", _router()):
            out = zo_01.get_indicator("WLD", "rsi", today, 5)
        self.assertIn("rsi values", out)
        self.assertIn("RSI: Measures momentum", out)
        # At least one concrete RSI value (not all N/A) should be present.
        self.assertRegex(out, r":\s*\d+\.\d+")

    def test_unsupported_indicator_raises_value_error(self):
        today = datetime.utcnow().date().strftime("%Y-%m-%d")
        with mock.patch.object(zo_01, "_zo_get", _router()):
            with self.assertRaises(ValueError):
                zo_01.get_indicator("WLD", "not_an_indicator", today, 5)


@pytest.mark.unit
class RoutingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = os.path.join(os.path.dirname(__file__), "_tmp_zo_route")
        os.makedirs(self._tmp, exist_ok=True)
        # Pin routing at the tool level: it takes precedence over category
        # config, so leftover tool_vendors from other test modules (set_config
        # merges dicts and cannot clear them) can't reroute these calls.
        set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))
        set_config(
            {
                "data_cache_dir": self._tmp,
                "data_vendors": {
                    "core_stock_apis": "zo_01",
                    "technical_indicators": "zo_01",
                },
                "tool_vendors": {
                    "get_stock_data": "zo_01",
                    "get_indicators": "zo_01",
                },
            }
        )
        zo_01._MARKETS_CACHE.update({"symbols": None, "fetched_at": 0.0})

    def tearDown(self):
        for f in os.listdir(self._tmp):
            os.remove(os.path.join(self._tmp, f))
        os.rmdir(self._tmp)
        set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))

    def test_zo_01_registered_for_price_methods(self):
        self.assertIn("zo_01", interface.VENDOR_METHODS["get_stock_data"])
        self.assertIn("zo_01", interface.VENDOR_METHODS["get_indicators"])
        self.assertIn("zo_01", interface.VENDOR_LIST)

    def test_route_to_vendor_uses_zo_for_stock_data(self):
        with mock.patch.object(zo_01, "_zo_get", _router()):
            out = interface.route_to_vendor(
                "get_stock_data", "WLD", "2026-05-20", "2026-06-01"
            )
        self.assertIn("01 Exchange (01.xyz)", out)

    def test_active_vendor_loader_selects_zo(self):
        today = datetime.utcnow().date().strftime("%Y-%m-%d")
        with mock.patch.object(zo_01, "_zo_get", _router()):
            df = interface.load_ohlcv_for_active_vendor("WLD", today)
        self.assertFalse(df.empty)
        self.assertIn("Close", df.columns)


if __name__ == "__main__":
    unittest.main()
