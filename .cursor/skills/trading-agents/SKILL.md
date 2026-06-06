---
name: trading-agents
description: Guides development, configuration, and usage of the TradingAgents multi-agent LLM financial trading framework. Use when working on TradingAgents code, running analyses, configuring LLM providers, tickers, checkpoints, memory logs, CLI, Docker, tests, or when the user mentions trading agents, stock analysis, portfolio decisions, or this repository.
---

# TradingAgents Skill

TradingAgents is a research framework (not financial advice). Help users install, configure, run, debug, and extend the multi-agent pipeline built on LangGraph.

## Security — never commit secrets

- **Never** commit `.env`, `.env.enterprise`, API keys, tokens, or real credentials.
- `.env` is gitignored. Users create it locally: `cp .env.example .env`
- In skills, docs, and code comments: reference **variable names only** (e.g. `OPENAI_API_KEY`), never real values.
- Do not copy the user's local `~/.cursor/skills/` if it contains embedded secrets.

See [references/env-setup.md](references/env-setup.md) for the full env-var map.

## Quick orientation

| Area | Path |
|------|------|
| Package root | `tradingagents/` |
| LangGraph orchestration | `tradingagents/graph/trading_graph.py` |
| Default config + env overrides | `tradingagents/default_config.py` |
| CLI entry | `cli/main.py` (command: `tradingagents`) |
| Analyst agents | `tradingagents/agents/analysts/` |
| Researchers / trader / risk / PM | `tradingagents/agents/` |
| LLM clients | `tradingagents/llm_clients/` |
| Data vendors | `tradingagents/dataflows/` |
| Tests | `tests/` |

### Agent pipeline (high level)

1. **Analyst team** — market, sentiment, news, fundamentals (user-selectable in CLI)
2. **Research team** — bull/bear debate → research manager
3. **Trader** — investment plan from reports
4. **Risk team** — aggressive / neutral / conservative debate
5. **Portfolio manager** — final trade decision (structured output)

## Setup checklist

1. Python 3.13+ virtual environment
2. `pip install .` from repo root
3. `cp .env.example .env` and fill API keys locally (not in git)
4. Optional: set `TRADINGAGENTS_*` vars to skip CLI prompts (see env reference)

```bash
pip install .
cp .env.example .env   # user fills keys locally
tradingagents            # interactive CLI
# or
tradingagents analyze --checkpoint
python -m cli.main analyze
```

Docker: `docker compose run --rm tradingagents` (requires `.env` with keys).

## Running analyses

### CLI (interactive)

```bash
tradingagents
```

User selects: tickers, analysis date, analysts, LLM provider/models, debate rounds, output language.

### CLI (non-interactive hints)

Pre-set env vars to skip prompts:

```bash
export TRADINGAGENTS_LLM_PROVIDER=openai
export TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.5
export TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.4-mini
export TRADINGAGENTS_OUTPUT_LANGUAGE=English
tradingagents analyze
```

Checkpoint options:

```bash
tradingagents analyze --checkpoint           # resume on crash
tradingagents analyze --clear-checkpoints    # wipe saved state first
```

### Python API

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

`DEFAULT_CONFIG` already applies `TRADINGAGENTS_*` env overrides at import time.

## Tickers and markets

Use Yahoo Finance exchange-suffixed tickers:

- US: `AAPL`, `SPY`
- HK: `0700.HK` · JP: `7203.T` · UK: `AZN.L`
- India: `RELIANCE.NS`, `.BO` · CA: `.TO` · AU: `.AX`
- China: `600519.SS`, `.SZ`
- Crypto: `BTC-USD`, `ETH-USD`

Benchmark for alpha reflection auto-resolves per market (`benchmark_map` in `default_config.py`); override with `TRADINGAGENTS_BENCHMARK_TICKER`.

## LLM providers

Supported `llm_provider` values: `openai`, `google`, `anthropic`, `xai`, `deepseek`, `qwen`, `qwen-cn`, `glm`, `glm-cn`, `minimax`, `minimax-cn`, `openrouter`, `ollama`, `azure`.

- API key env mapping: `tradingagents/llm_clients/api_key_env.py`
- Model catalog: `tradingagents/llm_clients/model_catalog.py`
- Ollama: no API key; optional `OLLAMA_BASE_URL` (default `http://localhost:11434/v1`)

When adding a provider: register env var in `api_key_env.py`, add client in `llm_clients/`, wire factory, update CLI model options.

## Persistence

| Feature | Location | Config |
|---------|----------|--------|
| Decision memory log | `~/.tradingagents/memory/trading_memory.md` | `TRADINGAGENTS_MEMORY_LOG_PATH` |
| Data/cache | `~/.tradingagents/cache/` | `TRADINGAGENTS_CACHE_DIR` |
| Checkpoints | `~/.tradingagents/cache/checkpoints/<TICKER>.db` | `checkpoint_enabled` or `--checkpoint` |
| Run logs | `~/.tradingagents/logs/` | `TRADINGAGENTS_RESULTS_DIR` |

Memory log is always on; checkpoints are opt-in.

## Reproducibility

LLM output is non-deterministic. To reduce variance:

```python
config["temperature"] = 0.0  # or TRADINGAGENTS_TEMPERATURE=0.0
config["deep_think_llm"] = "gpt-4.1"   # non-reasoning models honor temperature better
config["quick_think_llm"] = "gpt-4.1"
```

News/social data still changes over wall-clock time even with a fixed analysis date.

## Development guidelines

1. **Minimal diffs** — match existing patterns in `tradingagents/` and `cli/`.
2. **Config** — prefer `DEFAULT_CONFIG` keys; expose new overrides via `_ENV_OVERRIDES` in `default_config.py`.
3. **Structured outputs** — portfolio manager, research manager, trader use schemas in `tradingagents/agents/schemas.py`.
4. **Tests** — run `pytest` from repo root; add tests in `tests/` for behavior changes.
5. **Data vendors** — configure via `data_vendors` / `tool_vendors` in config (`yfinance` or `alpha_vantage`).

```bash
pytest tests/ -q
pytest tests/test_checkpoint_resume.py -v
```

## Common tasks

### Change debate depth

```python
config["max_debate_rounds"] = 2
config["max_risk_discuss_rounds"] = 2
```

Or: `TRADINGAGENTS_MAX_DEBATE_ROUNDS=2`, `TRADINGAGENTS_MAX_RISK_ROUNDS=2`

### Switch data vendor to Alpha Vantage

Requires `ALPHA_VANTAGE_API_KEY` in `.env`.

```python
config["data_vendors"]["core_stock_apis"] = "alpha_vantage"
```

### Debug a failing run

1. Check API key env for selected provider
2. Try `TradingAgentsGraph(debug=True, ...)`
3. Use `--checkpoint` to avoid restarting long runs
4. Inspect `~/.tradingagents/logs/` and CLI report sections
5. Run targeted tests matching the changed subsystem

### Extend an analyst

- Implement agent in `tradingagents/agents/analysts/`
- Register in graph setup (`tradingagents/graph/setup.py`)
- Add CLI option in `cli/models.py` if user-selectable
- Add tools in `tradingagents/agents/utils/` if new data access needed

## What not to do

- Do not present outputs as guaranteed trading profits or investment advice
- Do not commit `.env` or paste user API keys into the repo
- Do not hardcode provider URLs that leak across providers (see `backend_url` comment in `default_config.py`)
- Do not skip ticker validation — path traversal protections exist; preserve them

## Additional reference

- Env vars and API keys: [references/env-setup.md](references/env-setup.md)
- Upstream docs: `README.md`, `CHANGELOG.md`
