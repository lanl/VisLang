# VisLang

> [!WARNING]
> This is a 99% vibecoded experiment, not production code!


A pipeline for evaluating whether [Draco 2](https://github.com/cmudig/draco2)'s constraint-based visualization recommendations improve LLM-generated [Vega-Lite](https://vega.github.io/vega-lite/) specs.

Given a natural language prompt and a dataset, the pipeline generates two Vega-Lite visualizations:
1. **Draco-informed** — the LLM designs a spec guided by Draco's perceptually-optimized structural recommendations (mark type, channel assignments, scales, aggregation)
2. **Baseline** — the LLM designs a spec using only its own knowledge

A comparison LLM call then evaluates both specs side-by-side (including rendered images) and judges which is better and whether any quality difference is attributable to Draco's guidance.

## Setup

Requires Python 3.11+.

```bash
git clone --recurse-submodules <repo-url>
cd VisLang
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e ./draco2
```

Create a `.env` file with your API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

### CLI

```bash
python pipeline.py "Show horsepower vs MPG" cars.json
python pipeline.py --model openai/gpt-4o "Compare stock trends" stocks.csv
python pipeline.py --notebook --open "Weather distribution" seattle-weather.csv
```

Options:
- `--model MODEL` — [LiteLLM model string](https://docs.litellm.ai/docs/providers) (default: `anthropic/claude-opus-4-6`, override with `VISLANG_MODEL` env var)
- `--no-log` — skip writing artifacts to `logs/`
- `--notebook` — generate a Jupyter notebook in `output/`
- `--open` — open the notebook in VS Code (implies `--notebook`)

### Python / Jupyter

```python
from tools import run_vislang

result = run_vislang("Show horsepower vs MPG", "cars.json")
result  # displays side-by-side interactive charts in Jupyter

result.draco_spec          # Vega-Lite spec (Draco-guided)
result.baseline_spec       # Vega-Lite spec (baseline)
result.side_by_side        # Altair HConcatChart
result.comparison_markdown # Detailed comparison analysis
result.recommendations     # Draco's structural recommendations
```

### Benchmark

Run 10 predefined tasks (5 general + 5 targeting [known LLM weaknesses](https://arxiv.org/abs/2408.06845)) and print a summary table:

```bash
python benchmark.py                                         # default model
python benchmark.py anthropic/claude-haiku-4-5-20251001     # specific model
python benchmark.py anthropic/claude-opus-4-6 --sequential  # one at a time
```

## How it works

```
User prompt + data
       │
       ▼
 [Load data, compute schema]
       │
       ▼
 [LLM: Field Selection]        → picks relevant fields from the schema
       │
       ▼
 [Draco solver]                 → recommends mark type, channels, scales
       │
       ├── (if recommendations)─── [LLM: Draco-informed spec] ──┐
       │                                                         ├─ parallel
       └── [LLM: Baseline spec] ────────────────────────────────┘
                      │
                      ▼
              [LLM: Comparison]   → evaluates both with rendered images
                      │
                      ▼
               VislangResult
```

If Draco's solver fails or produces no recommendations, the pipeline skips the Draco-informed path entirely and only generates the baseline.

All LLM calls use [LiteLLM](https://github.com/BerriAI/litellm) for provider-agnostic access with JSON mode. Spec generation includes a retry loop that catches structural validation errors, Altair construction failures, and PNG rendering failures.

## Logging

Each run creates a timestamped directory under `logs/` with full artifacts:

```
logs/2026-02-27_220736_haiku45_show-how-life-expectancy/
├── meta.json                      # prompt, data, model, timestamp
├── 2_draco_solver_input.json      # ASP facts sent to Draco
├── 2_draco_solver_output.json     # solver result (spec, cost, violations)
├── 2_draco_recommendations.json   # extracted recommendations
├── 3a_draco_spec.vl.json          # Draco-informed Vega-Lite spec
├── 3a_draco_reasoning.md          # LLM's design reasoning
├── 3a_draco_spec.png              # rendered chart
├── 3b_baseline_spec.vl.json       # baseline Vega-Lite spec
├── 3b_baseline_reasoning.md
├── 3b_baseline_spec.png
├── 4_comparison.md                # side-by-side analysis + verdict
└── llm_calls/                     # complete LLM inputs/outputs
    ├── 1_field_selection_input.md
    ├── 1_field_selection_output.json
    ├── 2a_draco_spec_input.md
    ├── 2a_draco_spec_output.json
    ├── 2b_baseline_spec_input.md
    ├── 2b_baseline_spec_output.json
    ├── 3_comparison_input.md
    └── 3_comparison_output.json
```

## Project structure

```
pipeline.py            CLI entry point
benchmark.py           Benchmark runner (10 tasks)
tools/
├── __init__.py        Exports: run_vislang, VislangResult
├── pipeline.py        Core pipeline (LLM calls, Draco, orchestration)
├── prompts.py         Prompt templates for all LLM calls
└── vislang.py         Helper functions (load_data, run_draco, render, etc.)
draco2/                Draco 2 (git submodule)
vega-datasets/data/    Dataset collection
```

## Switching LLM providers

Set `VISLANG_MODEL` in `.env` or pass `--model`:

```bash
# Anthropic
python pipeline.py --model anthropic/claude-sonnet-4-5-20250929 "..." data.json

# OpenAI
python pipeline.py --model openai/gpt-4o "..." data.json

# Local (Ollama)
python pipeline.py --model ollama_chat/llama3.1 "..." data.json
```
