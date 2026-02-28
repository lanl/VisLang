# VisLang: Draco-Guided Visualization Pipeline

## Context
A Python pipeline where the user provides a natural language visualization task + data reference, and the system automatically generates, validates, and presents two Vega-Lite visualizations — one guided by Draco 2's expert constraint knowledge, one without. Draco acts as a **structural advisor** (recommending mark type, channel assignments, scales, aggregation) while LLM-generated specs include full polish. All artifacts are logged for systematic review.

---

## 1. Setup & Dependencies

### Git submodule for Draco 2
```bash
git submodule add https://github.com/cmudig/draco2.git draco2
pip install -e ./draco2
```

### Python dependencies (requirements.txt)
```
altair
pandas
vl-convert-python
jupyter
litellm
nest_asyncio
python-dotenv
```

### Directory structure
```
VisLang/
├── CLAUDE.md                    # Project description & usage
├── pipeline.py                  # CLI entry point
├── requirements.txt
├── .env                         # API keys (gitignored)
├── draco2/                      # git submodule (cmudig/draco2)
├── vega-datasets/data/          # dataset collection
├── tools/
│   ├── __init__.py              # Exports: run_vislang, VislangResult
│   ├── pipeline.py              # Core pipeline module
│   ├── prompts.py               # LLM prompt templates
│   └── vislang.py               # helper functions
├── output/                      # generated notebooks
│   └── <task-slug>.ipynb
└── logs/
    └── <YYYY-MM-DD>_<HHMMSS>_<task-slug>/
        ├── meta.json
        ├── field_selection.json
        ├── draco_input.json
        ├── draco_output.json
        ├── recommendations.json
        ├── with_draco.vl.json
        ├── with_draco.png
        ├── without_draco.vl.json
        ├── without_draco.png
        ├── comparison.md
        ├── draco_enhanced_log.md
        └── baseline_log.md
```

---

## 2. Helper Module: `tools/vislang.py`

Core data/Draco/rendering functions used by the pipeline:

```python
def load_data(path: str) -> pd.DataFrame
def get_schema_facts(df: pd.DataFrame) -> tuple[dict, list[str]]
def run_draco(partial_spec: list[str], num_models: int = 1) -> list[dict]
def extract_recommendations(draco_result: dict) -> dict
def render_altair(spec_dict: dict, df: pd.DataFrame) -> alt.Chart
def vl_to_altair(vl_spec: dict, df: pd.DataFrame) -> alt.Chart
def save_png(chart: alt.Chart, path: str)
def validate_vegalite(vl_spec: dict) -> tuple[bool, str | None]
def log_task(log_dir: str, **artifacts)
def create_log_dir(task_slug: str) -> str
def spec_dict_to_vegalite(spec_dict: dict, df: pd.DataFrame) -> dict
```

---

## 3. Architecture: Python Pipeline with LLM Calls

### Pipeline

```
User prompt + data_path
        │
        ▼
  [Load data, get schema]
        │
        ▼
  [LLM Call 1: Field Selection]  ← LLM picks which fields matter
        │
        ▼
  [Build partial spec facts]     ← deterministic
        │
        ▼
  [Draco solver → extract_recommendations()]  ← deterministic
        │
        ▼
  ┌─────┴──────┐
  │ asyncio.gather()
  │            │
  [LLM 2a]  [LLM 2b]   ← parallel: Draco-informed + baseline
  │            │           each with validate_vegalite() retry loop
  └─────┬──────┘
        │
        ▼
  [LLM Call 3: Comparison]
        │
        ▼
  VislangResult (displayable in notebook, logged to disk)
```

### Rationale

Draco acts as a structural advisor:

- **Draco decides**: mark type, channel assignments, scale types, aggregation, binning — perceptually-optimized structural choices
- **LLM decides**: titles, tooltips, color schemes, mark styling, interactive selections, sizing, additional encodings — the polish that makes a chart production-ready

### Key Components

| Component | Role |
|-----------|------|
| `tools/prompts.py` | Prompt templates for all 4 LLM calls (field selection, Draco-informed spec, baseline spec, comparison) |
| `tools/pipeline.py` | Core orchestration: `run_vislang()` public API, async pipeline, LLM calls via LiteLLM, retry logic |
| `pipeline.py` | CLI wrapper with argparse |
| `tools/vislang.py` | Data loading, Draco integration, rendering, validation |

### LLM Calls

All LLM calls use `litellm.acompletion()` with `response_format={"type": "json_object"}`:

1. **Field Selection** — given schema + prompt, returns `{fields, reasoning, mark_hint, channel_hints}`
2. **Draco-Informed Spec** — given recommendations + data, returns `{vegalite_spec, reasoning}` honoring Draco's structural choices
3. **Baseline Spec** — given prompt + data only, returns `{vegalite_spec, reasoning}`
4. **Comparison** — given both specs + recommendations, returns `{comparison_markdown, summary, verdict}`

Calls 2 and 3 run in parallel via `asyncio.gather()`. Both include a retry loop (up to 3 attempts) that appends validation errors as conversation context.

### VislangResult

Dataclass holding both specs, Altair charts, recommendations, comparison text, and metadata. Has `_repr_html_()` for rich Jupyter display and a `side_by_side` property returning `alt.HConcatChart`.

---

## 4. Notebook Generation

When `notebook=True`, the pipeline creates `output/<task-slug>.ipynb` with these cells:

| # | Type | Content |
|---|------|---------|
| 1 | markdown | Title: task prompt, data source |
| 2 | code | Setup: imports, data loading, schema summary |
| 3 | code | Draco recommendations summary |
| 4 | markdown | **With Draco Guidance** |
| 5 | code | Draco-informed chart display |
| 6 | markdown | **Without Draco** |
| 7 | code | Baseline chart display |
| 8 | markdown | **Comparison** |
| 9 | code | Side-by-side + analysis text |

Executed via `jupyter nbconvert --execute --inplace`.

---

## 5. Retry Mechanism

- **Draco solver**: If Draco fails, drops the last field and retries (up to 3 attempts)
- **Spec generation (LLM 2a/2b)**: Appends validation error as user message, retries (up to 3 attempts). Returns last spec even if all retries fail.

---

## 6. Provider Switching

Uses LiteLLM for provider-agnostic LLM calls. Default model set via `VISLANG_MODEL` env var (fallback: `anthropic/claude-sonnet-4-5-20250514`). Overridable per-call via `--model` flag or `model=` parameter.

API keys loaded from `.env` file via `python-dotenv`.

---

## 7. Verification Tasks

Test with progressively harder tasks:

### Task 1 (basic): `cars.json` — "Show the relationship between horsepower and miles per gallon"
- Straightforward 2-field quantitative relationship
- Draco should recommend: point mark, horsepower→x, miles_per_gallon→y, linear scales

### Task 2 (aggregation): `seattle-weather.csv` — "Show the distribution of weather types"
- Requires aggregation (count) over a categorical field
- Tests whether Draco correctly chooses bar chart with count

### Task 3 (multi-field): `cars.json` — "How does fuel efficiency vary across different origins, and does engine size play a role?"
- Ambiguous: 3+ fields, mixed types (quantitative + nominal)
- Tests multi-dimensional encoding choices

### Task 4 (temporal): `stocks.csv` — "Compare the stock performance of different companies over time"
- Multi-series temporal data
- Tests line chart with color encoding

### Task 5 (edge case): `movies.json` — "What factors are associated with high ratings?"
- Very open-ended — many possible fields
- Stress test for field selection and retry mechanism
