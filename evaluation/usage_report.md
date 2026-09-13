# Token Usage and Cost Report

Provider: Groq (https://groq.com). Final full-dataset run: 250 requests, 1781.6s wall time.

| Model | Purpose | Calls | Prompt tokens | Completion tokens | Total tokens | Est. cost (USD) |
|---|---|---|---|---|---|---|
| openai/gpt-oss-120b | message classification (perception) + decision_explanation (explain) | 12 | 3428 | 1013 | 4441 | $0.0011 |
| **Total** | | **12** | | | **4441** | **$0.0011** |

Average tokens per request: 17.8
Average cost per request: $0.000004

Pricing source: console.groq.com/docs/models, read at build time for the exact model ids used above. Message/image extraction results are cached to code/.cache/ across runs, so re-running main.py without deleting that cache makes zero further Groq calls.