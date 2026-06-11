# AGENTS.md

## Cursor Cloud specific instructions

TradingAgents is a Python (>=3.10, the VM ships 3.12) multi-agent LLM trading
framework. It is a **terminal/CLI application** — there is no web UI or server
to start. Work happens by running the package against an LLM provider.

### Environment

- Dependencies are managed with `uv` (a committed `uv.lock` is the source of
  truth). The startup update script installs `uv`, runs `uv sync` (creating
  `.venv/`), and installs `pytest` (which is **not** a declared dependency).
- Run everything through the venv with `uv run ...` (e.g. `uv run pytest`,
  `uv run python main.py`). `uv` lives at `~/.local/bin/uv`; if it is not on
  `PATH` in a fresh shell, call it by that full path or run
  `source ~/.local/bin/env`.

### LLM provider configuration

- The provider is configured entirely through `TRADINGAGENTS_*` env vars plus
  the matching provider API key, all injected as Cloud Agent secrets
  (`TRADINGAGENTS_LLM_PROVIDER`, `TRADINGAGENTS_DEEP_THINK_LLM`,
  `TRADINGAGENTS_QUICK_THINK_LLM`, `TRADINGAGENTS_LLM_BACKEND_URL`, and the
  provider's API-key var such as `<PROVIDER>_API_KEY`). `DEFAULT_CONFIG` in
  `tradingagents/default_config.py` reads these automatically, so `main.py`
  and the CLI run unattended without code edits.
- If those secrets are absent, set an API key + `TRADINGAGENTS_LLM_PROVIDER`
  before running anything that calls an LLM, or runs will fail.

### Running / testing

- Tests: `uv run pytest` (the `conftest.py` fixture injects placeholder API
  keys so no network/LLM is required; one DeepSeek live test self-skips).
- Full app (real LLM, costs tokens, takes several minutes):
  `uv run python main.py` propagates `NVDA` for a fixed date through the whole
  analyst → researcher debate → trader → risk → portfolio-manager graph and
  prints a final BUY/HOLD/SELL decision.
- Interactive CLI: `uv run tradingagents` or `uv run python -m cli.main`. It is
  questionary-driven; the `TRADINGAGENTS_*` secrets above auto-skip the
  provider/model selection prompts.
- Data-layer only smoke (no LLM): `uv run python test.py` exercises yfinance.

### Gotchas

- There is **no linter** configured (no ruff/flake8/black config, no CI
  workflow, no pre-commit hooks) — "lint" is not part of this repo.
- Reddit JSON requests return HTTP 403 from this environment; the sentiment
  flow logs the failure and falls back to RSS, so this is expected, not a bug.
- State persists under `~/.tradingagents/` (decision log at
  `memory/trading_memory.md`, checkpoints under `cache/`). Override with
  `TRADINGAGENTS_MEMORY_LOG_PATH` / `TRADINGAGENTS_CACHE_DIR`.
