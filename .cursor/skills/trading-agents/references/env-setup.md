# TradingAgents Environment Setup

This file documents **variable names only**. Users fill values in a local `.env` file. Never commit real keys.

## Create local env file

```bash
cp .env.example .env
# Edit .env on your machine only — it is gitignored
```

Enterprise providers: `cp .env.enterprise.example .env.enterprise` (also gitignored via `.env.enterprise` in `.gitignore`).

## LLM API keys (pick one or more providers)

| Provider | Env variable |
|----------|--------------|
| OpenAI | `OPENAI_API_KEY` |
| Google (Gemini) | `GOOGLE_API_KEY` |
| Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| xAI (Grok) | `XAI_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Qwen (international) | `DASHSCOPE_API_KEY` |
| Qwen (China) | `DASHSCOPE_CN_API_KEY` |
| GLM / Zhipu (international) | `ZHIPU_API_KEY` |
| GLM / Zhipu (China) | `ZHIPU_CN_API_KEY` |
| MiniMax (global) | `MINIMAX_API_KEY` |
| MiniMax (China) | `MINIMAX_CN_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` (see `.env.enterprise.example`) |
| Ollama | none — optional `OLLAMA_BASE_URL` |

## Data API keys (optional)

| Service | Env variable |
|---------|--------------|
| Alpha Vantage | `ALPHA_VANTAGE_API_KEY` |

Default data vendor is `yfinance` (no key required for basic usage).

## TRADINGAGENTS_* config overrides

These map to `DEFAULT_CONFIG` keys in `tradingagents/default_config.py`:

| Env variable | Config key | Type | Example |
|--------------|------------|------|---------|
| `TRADINGAGENTS_LLM_PROVIDER` | `llm_provider` | string | `openai` |
| `TRADINGAGENTS_DEEP_THINK_LLM` | `deep_think_llm` | string | `gpt-5.5` |
| `TRADINGAGENTS_QUICK_THINK_LLM` | `quick_think_llm` | string | `gpt-5.4-mini` |
| `TRADINGAGENTS_LLM_BACKEND_URL` | `backend_url` | string | custom OpenAI-compatible URL |
| `TRADINGAGENTS_OUTPUT_LANGUAGE` | `output_language` | string | `English` |
| `TRADINGAGENTS_MAX_DEBATE_ROUNDS` | `max_debate_rounds` | int | `2` |
| `TRADINGAGENTS_MAX_RISK_ROUNDS` | `max_risk_discuss_rounds` | int | `2` |
| `TRADINGAGENTS_CHECKPOINT_ENABLED` | `checkpoint_enabled` | bool | `true` |
| `TRADINGAGENTS_BENCHMARK_TICKER` | `benchmark_ticker` | string | `SPY` |
| `TRADINGAGENTS_TEMPERATURE` | `temperature` | float | `0.0` |

## Path overrides

| Env variable | Default | Purpose |
|--------------|---------|---------|
| `TRADINGAGENTS_CACHE_DIR` | `~/.tradingagents/cache` | Cache + checkpoints |
| `TRADINGAGENTS_RESULTS_DIR` | `~/.tradingagents/logs` | Run output logs |
| `TRADINGAGENTS_MEMORY_LOG_PATH` | `~/.tradingagents/memory/trading_memory.md` | Decision memory |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Remote Ollama server |

## Sharing the skill vs sharing secrets

- **Safe to commit:** `.cursor/skills/trading-agents/` (this skill), `.env.example`
- **Never commit:** `.env`, `.env.enterprise`, any file containing real API keys
- **Per developer:** each person creates their own `.env` after cloning

## Verify before git push

```bash
git status
# Should NOT list .env or .env.enterprise
git diff --cached
# Confirm no sk-..., real tokens, or pasted credentials
```

If a secret was committed: revoke/rotate the key immediately and remove it from git history.
