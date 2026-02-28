# VisLang: Draco-Guided Visualization Workflow

## Context
Build a Claude Code workflow where the user provides a natural language visualization task + data reference, and Claude automatically generates, validates, and presents interactive Vega-Lite visualizations — one guided by Draco 2's expert constraint knowledge, one without. Draco acts as a **structural advisor** (recommending mark type, channel assignments, scales, aggregation) while Claude generates the final spec with full polish. All artifacts are logged for systematic review.

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
vl-convert-python   # static PNG export, no Node.js needed
jupyter
```

### Directory structure
```
VisLang/
├── CLAUDE.md                    # Workflow instructions for Claude
├── requirements.txt
├── draco2/                      # git submodule (cmudig/draco2)
├── vega-datasets/data/          # existing dataset collection
├── draco/                       # existing Draco 1 checkout (unused)
├── tools/
│   └── vislang.py               # helper module
├── output/                      # generated notebooks (interactive viewing)
│   └── <task-slug>.ipynb
└── logs/
    └── <YYYY-MM-DD>_<HHMMSS>_<task-slug>/
        ├── meta.json            # prompt, data path, timestamp
        ├── draco_input.json     # partial spec facts sent to Draco
        ├── draco_output.json    # Draco answer set, cost, violations
        ├── recommendations.json # extracted recommendations for Claude
        ├── with_draco.vl.json   # Claude-authored Vega-Lite (Draco-guided)
        ├── with_draco.png       # static render
        ├── without_draco.vl.json
        ├── without_draco.png
        ├── comparison.md        # analysis of differences
        ├── draco_enhanced_log.md # Draco-informed subagent reasoning
        ├── baseline_log.md      # baseline subagent reasoning
        └── iterations/          # subsequent revisions
            └── 01/
                ├── feedback.md
                ├── with_draco.vl.json
                ├── with_draco.png
                └── ...
```

---

## 2. Helper Module: `tools/vislang.py`

Single module with functions Claude calls via `python -c "from tools.vislang import ...; ..."` or inline in notebooks.

### Key functions

```python
def load_data(path: str) -> pd.DataFrame
    """Load CSV/JSON/TSV data file into DataFrame."""

def get_schema_facts(df: pd.DataFrame) -> tuple[dict, list[str]]
    """Return (schema_dict, schema_fact_strings) via draco.schema_from_dataframe + dict_to_facts."""

def run_draco(partial_spec: list[str], num_models: int = 1) -> list[dict]
    """Run Draco 2 complete_spec. Returns list of {spec_dict, cost, violations, answer_set_facts}."""

def extract_recommendations(draco_result: dict) -> dict
    """Extract structured recommendations from a Draco result.
    Returns {mark_type, encodings, scales, coordinates, facets, task, cost, violations}."""

def render_altair(spec_dict: dict, df: pd.DataFrame) -> alt.Chart
    """Use AltairRenderer to produce Altair chart from Draco spec dict."""

def vl_to_altair(vl_spec: dict, df: pd.DataFrame) -> alt.Chart
    """Create Altair chart from a raw Vega-Lite spec dict."""

def save_png(chart: alt.Chart, path: str)
    """Export chart to static PNG via vl-convert-python."""

def validate_vegalite(vl_spec: dict) -> tuple[bool, str | None]
    """Validate Vega-Lite spec structure. Returns (valid, error_message)."""

def log_task(log_dir: str, **artifacts)
    """Write all artifacts to the log directory as JSON/PNG/MD files."""

def create_log_dir(task_slug: str) -> str
    """Create timestamped log dir. Returns the path."""

def spec_dict_to_vegalite(spec_dict: dict, df: pd.DataFrame) -> dict
    """Convert a Draco spec dict to a Vega-Lite JSON spec (for debugging)."""
```

---

## 3. Architecture: Draco as Advisor, Claude as Spec Author

### Pipeline

```
Main agent
├─ Step 1: Parse request, create log dir
├─ Step 2: Run Draco directly → extract_recommendations() → recommendations.json
├─ Step 3: Launch two subagents IN PARALLEL:
│   ├─ Draco-informed subagent: recommendations + data → polished VL spec
│   └─ Baseline subagent: user prompt + data → polished VL spec (no Draco)
├─ Step 4: Render PNGs, create notebook, execute
├─ Step 5: Write comparison.md
└─ Step 6: Present to user
```

### Rationale

Previously, Draco produced a complete Vega-Lite spec rendered as-is — minimal, no interactivity, no context-aware styling. The new approach uses Draco as a structural advisor:

- **Draco decides**: mark type, channel assignments, scale types, aggregation, binning — the perceptually-optimized structural choices
- **Claude decides**: titles, tooltips, color schemes, mark styling, interactive selections, sizing, additional encodings for context — the polish that makes a chart production-ready

This gives us the best of both worlds: Draco's evidence-based structural recommendations plus Claude's ability to create polished, interactive visualizations.

### Agent Roles

| Agent | Role | Draco context? |
|-------|------|---------------|
| **Main agent** | Parse request, run Draco, extract recommendations, launch subagents, assemble notebook, compare, log | Runs Draco directly |
| **Draco-informed subagent** (Task) | Receives recommendations + data, produces polished Vega-Lite honoring Draco's structural advice | Yes (recommendations) |
| **Baseline subagent** (Task) | Produces polished Vega-Lite from Claude's own knowledge | No |

### Step 1: Main agent — Parse the request
- Identify the data file (resolve relative to `vega-datasets/data/` or absolute path)
- Understand the visualization intent (what relationship/distribution/comparison to show)
- Create the log directory: `logs/<YYYY-MM-DD>_<HHMMSS>_<task-slug>/`
- Write `meta.json` with prompt, data path, timestamp

### Step 2: Main agent — Run Draco and extract recommendations

This is a deterministic computation, not an LLM task. The main agent:
1. Loads data and generates schema facts
2. Formulates a partial spec (schema + encoding entities for relevant fields)
3. Runs Draco's constraint solver
4. Calls `extract_recommendations()` to produce a clean summary
5. Saves `draco_input.json`, `draco_output.json`, and `recommendations.json` to the log dir

Partial spec formulation guidelines:
- Always include: schema facts, `entity(view,root,v0).`, `entity(mark,v0,m0).`
- For each relevant field: `entity(encoding,m0,eN).`, `attribute((encoding,field),eN,fieldname).`
- Pin mark type only if user explicitly requested one
- Pin channels only if user explicitly specified axis placement
- Let Draco decide everything else (channels, mark type, scales, aggregation, binning)

### Step 3: Main agent — Launch two subagents in parallel

**Draco-informed subagent** receives:
- The user's prompt
- The data file path
- The log directory path
- The `recommendations.json` contents (mark type, encodings, scales, etc.)
- Instructions to honor Draco's structural decisions while adding polish (titles, tooltips, styling, interactivity)
- Must write: `with_draco.vl.json`, `draco_enhanced_log.md`

**Baseline subagent** receives:
- The user's prompt
- The data file path
- The log directory path
- Instructions to produce the best Vega-Lite from own knowledge
- NO mention of Draco, constraints, or recommendations
- Must write: `without_draco.vl.json`, `baseline_log.md`

### Step 4: Main agent — Collect results, render, create notebook
- Read both Vega-Lite specs from subagent output files
- Render static PNGs for logging (via `vl-convert-python`)
- Create Jupyter notebook via NotebookEdit
- Execute via `jupyter nbconvert --execute --inplace`
- Open in VS Code

### Step 5: Main agent — Compare
- Read both specs + Draco's recommendations and violations
- Write `comparison.md` covering mark type, encoding, scale choices, Draco's cost/violations, Claude's enhancements, and which better serves the user's intent

### Step 6: Main agent — Present to user
- Brief summary of what each approach chose
- Highlight what Draco recommended vs. what Claude enhanced
- Note the notebook is open for interactive viewing

### Iteration (on user feedback)
- Re-run Draco with adjusted partial spec if structural changes needed
- Re-launch both subagents with updated prompt + previous specs
- Log to `iterations/NN/` subdirectory
- Append new cells to notebook, re-execute

---

## 4. Notebook Structure (per task)

Each task produces `output/<task-slug>.ipynb` with these cells:

| # | Type | Content | Collapsed |
|---|------|---------|-----------|
| 1 | markdown | **Title**: task prompt, data source | No |
| 2 | code | Setup: imports, data loading, schema summary | Yes |
| 3 | code | Draco recommendations summary | Yes |
| 4 | markdown | **With Draco Guidance** | No |
| 5 | code | `chart_draco` — Altair chart display | No |
| 6 | markdown | **Without Draco** | No |
| 7 | code | `chart_no_draco` — Altair chart display | No |
| 8 | markdown | **Comparison** | No |
| 9 | code | Side-by-side + analysis text | No |

"Collapsed" cells contain the technical details — visible on demand but not cluttering the view.

### Notebook execution
After creating the notebook via NotebookEdit, execute it:
```bash
jupyter nbconvert --to notebook --execute --inplace output/<task>.ipynb
```
This embeds outputs (including interactive Altair chart JSON) so VS Code shows them immediately without the user running cells.

Then open in VS Code:
```bash
code output/<task>.ipynb
```

### Iteration
When the user provides feedback, Claude:
1. Adds new cells to the existing notebook (new section: "Iteration N")
2. Re-executes the notebook
3. Logs the iteration artifacts

---

## 5. Retry Mechanism

```
attempt = 0
while attempt < 3:
    try:
        validate and render spec
        break
    except:
        analyze error
        fix spec (adjust fields, types, encoding structure)
        attempt += 1
```

- **Draco solver (Step 2)**: If Draco fails, adjust the partial spec and re-run
- **Draco-informed path (Step 3)**: Fix the Vega-Lite spec directly based on the validation error
- **Baseline path (Step 3)**: Fix the Vega-Lite spec directly based on the validation error

---

## 6. Verification

Test with progressively harder tasks:

### Task 1 (basic): `cars.json` — "Show the relationship between horsepower and miles per gallon"
- Straightforward 2-field quantitative relationship
- Draco should recommend: point mark, horsepower→x, miles_per_gallon→y, linear scales
- Claude's Draco-informed spec: honors scatterplot + channels, adds color by origin, tooltips, titles, filled marks
- Validates the basic pipeline works end-to-end

### Task 2 (aggregation): `seattle-weather.csv` — "Show the distribution of weather types"
- Requires aggregation (count) over a categorical field
- Tests whether Draco correctly chooses bar chart with count
- Tests whether baseline handles implicit aggregation

### Task 3 (multi-field): `cars.json` — "How does fuel efficiency vary across different origins, and does engine size play a role?"
- Ambiguous: 3+ fields, mixed types (quantitative + nominal)
- Draco must decide on mark type, channels for 3 encodings (color? facet?)
- Tests whether Draco's constraint-based approach produces a clearer multi-dimensional view than the baseline

### Task 4 (temporal): `stocks.csv` — "Compare the stock performance of different companies over time"
- Multi-series temporal data
- Tests line chart with color encoding
- Draco should handle temporal type and multi-series well

### Task 5 (edge case / harder): `movies.json` — "What factors are associated with high ratings?"
- Very open-ended — many possible fields, no single "right" answer
- Tests whether Draco handles underspecified requests (many fields, unclear which matter)
- May require judgment calls about which fields to include
- Good stress test for the retry mechanism if initial specs are too complex

Each task should produce:
- A working notebook with interactive charts (verify in VS Code)
- A complete log directory with all artifacts
- A meaningful comparison analysis
- Static PNG renders in the log dir
