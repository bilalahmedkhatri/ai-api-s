# Model Roster & Upgrade Policy

This file documents the current 30B+ model assignments and the process
for updating them. **Review this file whenever a new model leaderboard
ranking is published.**

## Current Routing Matrix

| Intent Category | Primary Model | Fallbacks |
|---|---|---|
| `coding` | `groq/qwen-2.5-coder-32b` | Llama-3.3-70B → Llama-3.1-8B |
| `mathematics` | `groq/qwen-2.5-coder-32b` | Llama-3.3-70B → Llama-3.1-8B |
| `creative_writing` | `groq/llama-3.3-70b-versatile` | Llama-3.1-8B → Command-R-Plus |
| `general_knowledge` | `groq/llama-3.3-70b-versatile` | Command-R-Plus → Llama-3.1-8B |
| `general` (fallback) | `groq/llama-3.3-70b-versatile` | Llama-3.1-8B → Nemotron-3-Ultra (OpenRouter free) |

## Upgrade Process

1. **Monthly**: Check [LMSYS Chatbot Arena](https://chat.lmsys.org/?leaderboard)
   and [HuggingFace Open LLM Leaderboard](https://huggingface.co/spaces/HuggingFaceH4/open_llm_leaderboard).
2. If a new open model outperforms the current primary in its category:
   - Update the model string in `app/services/model_router.py` → `ROUTING_MATRIX`.
   - Verify the new model ID is available on Groq / OpenRouter.
   - Run `pytest tests/` to confirm nothing breaks.
   - Commit with message: `chore(models): upgrade <category> to <new-model>`.
3. Keep the old model as position-1 in the fallbacks list for one sprint
   before removing it entirely.

## Provider Priority

| Provider | Best For | Env Var |
|---|---|---|
| **Groq** | Ultra-fast inference (Llama, Qwen, Mixtral) | `GROQ_API_KEY` |
| **Cohere** | RAG + Command-R family | `COHERE_API_KEY` |
| **OpenRouter** | Universal fallback for any model | `OPENROUTER_API_KEY` |

> All keys are set in `.env` and read by `app/core/config.py`.
