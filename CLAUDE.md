# VisLang — Draco-Guided Visualization Pipeline

A Python pipeline that generates two Vega-Lite visualizations for any data + prompt: one guided by Draco 2's constraint-based recommendations, one from the LLM's own design knowledge. Uses LiteLLM for provider-agnostic LLM calls.

## Project Layout

```
pipeline.py               # CLI entry point
tools/
├── __init__.py            # Exports: run_vislang, VislangResult
├── pipeline.py            # Core pipeline (LLM calls, Draco, orchestration)
├── prompts.py             # Prompt templates for all LLM calls
└── vislang.py             # Helper functions (load_data, run_draco, render, etc.)
draco2/                    # Draco 2 git submodule
vega-datasets/data/        # Dataset collection
output/                    # Generated notebooks
logs/                      # Timestamped log directories per task
docs/DESIGN.md             # Design document
```

## Quick Start

### Setup

```bash
pip install -r requirements.txt
pip install -e ./draco2
```

### API Keys

Create a `.env` file at the project root (gitignored):

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
# Default model (override with --model or model= parameter):
# VISLANG_MODEL=openai/gpt-4o
```

### CLI Usage

```bash
python pipeline.py "Show horsepower vs MPG" cars.json
python pipeline.py --model openai/gpt-4o "Compare stock trends" stocks.csv
python pipeline.py --notebook --open "Weather distribution" seattle-weather.csv
```

Options:
- `--model MODEL` — LiteLLM model string (default: `VISLANG_MODEL` env or `anthropic/claude-sonnet-4-5-20250929`)
- `--no-log` — skip writing artifacts to `logs/`
- `--notebook` — create and execute a Jupyter notebook in `output/`
- `--open` — open the notebook in VS Code (implies `--notebook`)

### Python / Notebook Usage

```python
from tools import run_vislang

result = run_vislang("Show horsepower vs MPG", "cars.json")
result  # displays side-by-side interactive charts in Jupyter

# Access individual parts
result.draco_spec          # Vega-Lite spec (Draco-guided)
result.baseline_spec       # Vega-Lite spec (baseline)
result.side_by_side        # Altair HConcatChart
result.comparison_markdown # Detailed comparison analysis
result.recommendations     # Draco's structural recommendations
```

## Pipeline Architecture

```
User prompt + data_path
        │
        ▼
  [Load data, get schema]
        │
        ▼
  [LLM Call 1: Field Selection]  ← LLM picks relevant fields
        │
        ▼
  [Build partial spec facts]     ← deterministic
        │
        ▼
  [Draco solver → recommendations]  ← deterministic
        │
        ▼
  ┌─────┴──────┐
  │ asyncio.gather()
  │            │
  [LLM 2a]  [LLM 2b]   ← parallel: Draco-informed + baseline
  │            │           each with validation retry loop
  └─────┬──────┘
        │
        ▼
  [LLM Call 3: Comparison]
        │
        ▼
  VislangResult
```

## Log Directory Structure

Each run creates `logs/<timestamp>_<model>_<slug>/` containing:

```
meta.json                          # prompt, data, model, timestamp
2_draco_solver_input.json          # partial spec ASP facts
2_draco_solver_output.json         # raw solver result (spec_dict, cost, violations)
2_draco_recommendations.json       # extracted recommendations
2_draco_solver_attempts.json       # (if solver failed or retried)
3a_draco_spec.vl.json              # Draco-informed Vega-Lite spec
3a_draco_reasoning.md              # Draco-informed LLM reasoning
3a_draco_spec.png                  # rendered chart
3b_baseline_spec.vl.json           # baseline Vega-Lite spec
3b_baseline_reasoning.md           # baseline LLM reasoning
3b_baseline_spec.png               # rendered chart
4_comparison.md                    # comparison analysis
llm_calls/                         # full LLM call inputs and outputs
  1_field_selection_input.md
  1_field_selection_output.json
  2a_draco_spec_input.md
  2a_draco_spec_output.json
  2b_baseline_spec_input.md
  2b_baseline_spec_output.json
  3_comparison_input.md
  3_comparison_output.json
```

## Switching Providers

Edit `.env` to change the default model:
- Anthropic: `VISLANG_MODEL=anthropic/claude-sonnet-4-5-20250929`
- OpenAI: `VISLANG_MODEL=openai/gpt-4o`
- Ollama (local): `VISLANG_MODEL=ollama_chat/llama3.1`

Or pass `--model` at CLI / `model=` in Python for one-off overrides.

## Presenting Results

When summarizing benchmark or pipeline results to the user:
- Always note whether Draco's solver actually ran successfully (check for empty recommendations)
- Report the comparison LLM's assessment of **attribution**: was the quality difference due to Draco's structural guidance, or did the LLM make independent choices that happened to be better/worse?
- Don't just report "draco won" / "baseline won" — surface the comparison's reasoning about what drove the difference
