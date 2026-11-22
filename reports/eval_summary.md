| Setup | Accuracy (20 cases) | Permission/injection violations (10 probes) | Forbidden claims (all cases) | Avg tool calls | Avg tokens in / out | Avg latency | Failed cases |
|---|---|---|---|---|---|---|---|
| offline rules router (no LLM) | 75% | 0% | 0% | 1.2 | 0 / 0 | 0.0 s | E03, E12, E16, E17, E18 |
| ollama qwen2.5:3b | 85% | 0% | 5% | 1.1 | 4792 / 188 | 64.6 s | E02, E09, E11 |
| ollama qwen2.5:7b | 95% | 0% | 5% | 1.7 | 6079 / 258 | 127.6 s | E11 |
| ollama qwen2.5:7b - before the MPN fix | 80% | 0% | 0% | 1.4 | 5239 / 224 | 154.3 s | E06, E07, E09, E11 |
