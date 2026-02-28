# VisLang — Draco-Guided Visualization Workflow

When the user provides a natural language visualization task + data reference, follow this workflow to generate, validate, and present two interactive Vega-Lite visualizations: one guided by Draco 2's constraint-based recommendations, one from your own design knowledge. Log all artifacts for review.

## Project Layout

```
tools/vislang.py          # Helper functions (load_data, run_draco, render, etc.)
draco2/                   # Draco 2 git submodule (constraint-based vis recommender)
vega-datasets/data/       # Dataset collection (cars.json, stocks.csv, etc.)
output/                   # Generated notebooks for interactive viewing
logs/                     # Timestamped log directories per task
docs/DESIGN.md            # Full design document
```

## Helper Module: `tools/vislang.py`

Import in notebooks or via `python -c`:

```python
from tools.vislang import (
    load_data,               # Load CSV/JSON/TSV → DataFrame
    get_schema_facts,        # DataFrame → (schema_dict, ASP fact strings)
    run_draco,               # partial spec facts → [{spec_dict, cost, violations, ...}]
    extract_recommendations, # Draco result → clean recommendation summary for Claude
    render_altair,           # Draco spec dict + DataFrame → Altair Chart
    vl_to_altair,            # Vega-Lite spec dict + DataFrame → Altair Chart
    save_png,                # Chart → PNG file
    validate_vegalite,       # Vega-Lite spec → (valid, error_message)
    log_task,                # Write artifacts to log directory
    create_log_dir,          # Create timestamped log dir
    spec_dict_to_vegalite,   # Draco spec dict → Vega-Lite JSON
)
```

## Workflow

### Architecture: Draco as Advisor, Claude as Spec Author

Draco acts as a **structural advisor** — recommending mark type, channel assignments, scales, and aggregation — while **Claude generates the final Vega-Lite spec** informed by those recommendations, adding interactivity, styling, titles, and more. A separate baseline (without Draco) is still generated for comparison.

```
Main agent
├─ Step 1: Parse request, create log dir
├─ Step 2: Run Draco directly → extract_recommendations() → structured recommendations JSON
├─ Step 3: Launch two subagents IN PARALLEL:
│   ├─ Draco-informed subagent: receives recommendations + data → produces final VL spec
│   └─ Baseline subagent: receives only user prompt + data → produces final VL spec
├─ Step 4: Render PNGs, create notebook, execute
├─ Step 5: Write comparison.md
└─ Step 6: Present to user
```

| Agent | Role | Draco? |
|-------|------|--------|
| **Main agent** | Parse request, run Draco, launch subagents, assemble notebook, compare, log | Runs Draco directly |
| **Draco-informed subagent** (Task tool, general-purpose) | Receives Draco recommendations + data, produces polished Vega-Lite | Informed by recommendations |
| **Baseline subagent** (Task tool, general-purpose) | Produces best Vega-Lite from own knowledge, no Draco context | No |

### Step 1: Parse the request (Main agent)

1. Identify the data file — resolve relative to `vega-datasets/data/` or accept absolute path
2. Understand the visualization intent (relationship, distribution, comparison, trend, etc.)
3. Create log directory: call `create_log_dir(task_slug)` or manually create `logs/<YYYY-MM-DD>_<HHMMSS>_<task-slug>/`
4. Write `meta.json` with: `{ "prompt": ..., "data_path": ..., "timestamp": ... }`

### Step 2: Run Draco and extract recommendations (Main agent)

This is a deterministic computation — no subagent needed. The main agent formulates the partial spec, runs Draco, and extracts recommendations:

```python
from tools.vislang import load_data, get_schema_facts, run_draco, extract_recommendations
import json

df = load_data(data_path)
schema, schema_facts = get_schema_facts(df)

# Formulate partial spec: schema facts + encoding entities for relevant fields
partial_spec = schema_facts + [
    'entity(view,root,v0).',
    'entity(mark,v0,m0).',
    # For each relevant field:
    # 'entity(encoding,m0,eN).',
    # 'attribute((encoding,field),eN,fieldname).',
    # Pin mark type ONLY if user explicitly requested one
    # Pin channels ONLY if user explicitly specified axis placement
    # Let Draco decide everything else
]

results = run_draco(partial_spec)
recommendations = extract_recommendations(results[0])

# Save artifacts to log dir
with open(f"{log_dir}/draco_input.json", "w") as f:
    json.dump(partial_spec, f, indent=2)
with open(f"{log_dir}/draco_output.json", "w") as f:
    json.dump({"spec_dict": results[0]["spec_dict"], "cost": results[0]["cost"], "violations": results[0]["violations"]}, f, indent=2, default=str)
with open(f"{log_dir}/recommendations.json", "w") as f:
    json.dump(recommendations, f, indent=2, default=str)
```

### Step 3: Launch two subagents in parallel (Task tool)

Launch both subagents simultaneously using two Task tool calls in a single message.

#### Draco-Informed Subagent Prompt Template

```
You are generating a polished Vega-Lite visualization informed by Draco's perceptual recommendations. Working directory: /Users/michaelballantyne/code/VisLang

USER REQUEST: {prompt}
DATA FILE: {data_path}
LOG DIR: {log_dir}

DRACO RECOMMENDATIONS:
{recommendations_json}

These recommendations come from Draco 2's constraint-based visualization recommender, which optimizes
for perceptual effectiveness. You MUST honor these structural decisions:
- Mark type: use the recommended mark type
- Channel assignments: use the recommended field→channel mappings
- Scale types: use the recommended scale types
- Aggregation/binning: apply if recommended

You SHOULD enhance the visualization with:
- Descriptive title and axis labels
- Tooltips for interactive exploration
- Additional encodings (color, opacity, size) if they add insight beyond what Draco specified
- Appropriate mark styling (filled marks, opacity for overplotting, stroke width, etc.)
- Non-zero scales if it improves readability (note if Draco recommended zero)
- Interactive selections if appropriate for the data
- Proper width/height sizing
- Thoughtful color schemes

Steps:
1. Load data: `from tools.vislang import load_data, validate_vegalite; import pandas as pd`
2. Run `df = load_data("{data_path}")` — examine columns, types, sample rows
3. Design a Vega-Lite spec that honors Draco's structural recommendations while adding polish
4. Validate: `valid, err = validate_vegalite(spec)`
5. Retry up to 3 times on validation errors
6. Save artifacts to log dir:
   - with_draco.vl.json: the Vega-Lite spec
   - draco_enhanced_log.md: your reasoning, how you used the recommendations, enhancements added
```

#### Baseline Subagent Prompt Template

```
You are generating a visualization using your own design knowledge (NO Draco). Working directory: /Users/michaelballantyne/code/VisLang

USER REQUEST: {prompt}
DATA FILE: {data_path}
LOG DIR: {log_dir}

Steps:
1. Load data: `from tools.vislang import load_data, validate_vegalite; import pandas as pd`
2. Run `df = load_data("{data_path}")` — examine columns, types, sample rows
3. Design the best Vega-Lite spec for the user's request using your visualization expertise
4. Validate: `valid, err = validate_vegalite(spec)`
5. Retry up to 3 times on validation errors
6. Save artifacts to log dir:
   - without_draco.vl.json: the Vega-Lite spec
   - baseline_log.md: your reasoning and design choices

Do NOT use Draco, ASP, or constraint-based tools. Rely on your own knowledge of visualization best practices.
```

### Step 4: Collect results, render, create notebook (Main agent)

1. Read `with_draco.vl.json` and `without_draco.vl.json` from the log dir
2. Render static PNGs:
   ```python
   from tools.vislang import load_data, vl_to_altair, save_png
   df = load_data(data_path)
   save_png(vl_to_altair(draco_spec, df), f"{log_dir}/with_draco.png")
   save_png(vl_to_altair(baseline_spec, df), f"{log_dir}/without_draco.png")
   ```

3. Create `output/<task-slug>.ipynb` using NotebookEdit with these cells:

| # | Type | Content |
|---|------|---------|
| 1 | markdown | Title: task prompt, data source |
| 2 | code | Setup: imports, data loading, schema summary (collapse via metadata) |
| 3 | code | Draco recommendations summary display (collapse) |
| 4 | markdown | **With Draco Guidance** |
| 5 | code | `chart_draco` display |
| 6 | markdown | **Without Draco** |
| 7 | code | `chart_no_draco` display |
| 8 | markdown | **Comparison** |
| 9 | code | Side-by-side analysis |

4. Execute and open:
```bash
jupyter nbconvert --to notebook --execute --inplace output/<task-slug>.ipynb
code output/<task-slug>.ipynb
```

### Step 5: Compare (Main agent)

Write `comparison.md` to the log dir covering:
- Mark type choices and rationale
- Channel/encoding decisions
- Scale choices
- Draco soft constraint violations and costs
- Enhancements added by Claude on top of Draco's recommendations
- Assessment of which better serves the user's intent

### Step 6: Present to user (Main agent)

- Brief summary of what each approach chose
- Highlight what Draco recommended vs. what Claude enhanced
- Note the notebook is open for interactive viewing
- Don't dump specs/constraints unless asked

### Iteration (on user feedback)

1. Re-run Step 2 (Draco) with adjusted partial spec if structural changes needed
2. Re-launch both subagents with updated prompt (original + feedback), including previous spec as starting point
3. Log new artifacts to `{log_dir}/iterations/NN/`
4. Append new cells to the existing notebook, re-execute

## Log Directory Structure

```
logs/<timestamp>_<slug>/
├── meta.json               # prompt, data path, timestamp
├── draco_input.json        # partial spec facts
├── draco_output.json       # full Draco result (spec_dict, cost, violations)
├── recommendations.json    # extracted recommendations for Claude
├── with_draco.vl.json      # Claude-authored spec guided by Draco
├── with_draco.png          # static render
├── without_draco.vl.json   # Claude-authored spec without Draco
├── without_draco.png       # static render
├── comparison.md           # analysis of differences
├── draco_enhanced_log.md   # Draco-informed subagent reasoning
└── baseline_log.md         # baseline subagent reasoning
```

## Retry Mechanism

Both subagents retry up to 3 times on errors:
- **Draco-informed path**: fix the Vega-Lite spec directly based on the error (Draco recommendations are already extracted)
- **Baseline path**: fix the Vega-Lite spec directly based on the error

If Draco itself fails in Step 2, the main agent adjusts the partial spec and re-runs (up to 3 attempts).

## Data Resolution

Data paths are resolved in order:
1. Absolute path (if provided)
2. Relative to `vega-datasets/data/`
3. Relative to project root
