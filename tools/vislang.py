"""VisLang helper module for Draco-guided visualization workflow."""

import json
import os
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
from draco import Draco, answer_set_to_dict, dict_to_facts, schema_from_dataframe
from draco.renderer import AltairRenderer


def _draco_normalize_name(name: str) -> str:
    """Normalize a column name to match Draco's fact encoding.

    Draco's dict_to_facts lowercases the first character of all values.
    We apply the same transformation to DataFrame columns so field names
    are consistent between the schema facts and the actual data.
    """
    if not name:
        return name
    return name[0].lower() + name[1:]


def load_data(path: str) -> pd.DataFrame:
    """Load CSV/JSON/TSV data file into DataFrame.

    Resolves paths relative to vega-datasets/data/ if not absolute.
    Column names are normalized to match Draco's fact encoding convention
    (first character lowercased).
    """
    p = Path(path)
    if not p.is_absolute():
        # Try relative to project root's vega-datasets/data/
        project_root = Path(__file__).parent.parent
        candidates = [
            project_root / "vega-datasets" / "data" / p,
            project_root / p,
        ]
        for c in candidates:
            if c.exists():
                p = c
                break
        else:
            raise FileNotFoundError(
                f"Data file not found: {path} (searched {[str(c) for c in candidates]})"
            )

    suffix = p.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(p)
    elif suffix == ".tsv":
        df = pd.read_csv(p, sep="\t")
    elif suffix == ".json":
        df = pd.read_json(p)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    # Normalize column names to match Draco's fact encoding (first char lowercased)
    df.columns = [_draco_normalize_name(c) for c in df.columns]
    return df


def get_schema_facts(df: pd.DataFrame) -> tuple[dict, list[str]]:
    """Return (schema_dict, schema_fact_strings).

    Uses draco.schema_from_dataframe to get the schema dict,
    then draco.dict_to_facts to convert to ASP fact strings.
    """
    schema = schema_from_dataframe(df)
    facts = dict_to_facts(schema)
    return schema, facts


def run_draco(partial_spec_facts: list[str], num_models: int = 1) -> list[dict]:
    """Run Draco 2 complete_spec on partial spec facts.

    Returns list of dicts, each with:
      - spec_dict: the completed specification as a dict
      - cost: list of optimization costs
      - violations: dict of soft constraint violation counts
      - answer_set_facts: list of fact strings from the answer set
    """
    draco = Draco()
    results = []
    for model in draco.complete_spec(partial_spec_facts, models=num_models):
        spec_dict = answer_set_to_dict(model.answer_set)
        # Get violation counts for analysis
        full_facts = [str(s) + "." for s in model.answer_set]
        violations = draco.count_preferences(full_facts)
        results.append(
            {
                "spec_dict": spec_dict,
                "cost": model.cost,
                "violations": violations,
                "answer_set_facts": full_facts,
            }
        )
    return results


def render_altair(spec_dict: dict, df: pd.DataFrame) -> alt.Chart:
    """Use AltairRenderer to produce Altair chart from Draco spec dict."""
    renderer = AltairRenderer()
    return renderer.render(spec_dict, df)


def vl_to_altair(vl_spec: dict, df: pd.DataFrame) -> alt.Chart:
    """Create Altair chart from a raw Vega-Lite spec dict (for non-Draco path).

    The vl_spec should be a standard Vega-Lite specification dict.
    Data is provided separately via the DataFrame.
    """
    # Remove top-level-only keys that prevent use in compositions (hconcat, etc.)
    skip_keys = {"data", "$schema"}
    spec = {k: v for k, v in vl_spec.items() if k not in skip_keys}
    chart = alt.Chart.from_dict({"data": {"values": []}, **spec})
    chart = chart.properties(data=df)
    return chart


def save_png(chart: alt.Chart, path: str) -> None:
    """Export chart to static PNG via vl-convert-python."""
    chart.save(path, format="png")


def validate_vegalite(vl_spec: dict) -> tuple[bool, str | None]:
    """Validate Vega-Lite spec structure.

    Returns (valid, error_message). Uses Altair's built-in validation.
    """
    try:
        # Check required top-level keys
        if "mark" not in vl_spec and "layer" not in vl_spec:
            return False, "Spec must have 'mark' or 'layer' at top level"
        if "encoding" not in vl_spec and "layer" not in vl_spec:
            return False, "Spec must have 'encoding' or 'layer' at top level"

        # Try to construct the chart — Altair validates the schema
        spec_with_data = {**vl_spec, "data": {"values": []}}
        alt.Chart.from_dict(spec_with_data)
        return True, None
    except Exception as e:
        return False, str(e)


def log_task(log_dir: str, **artifacts) -> None:
    """Write all artifacts to the log directory.

    Artifact values can be:
      - dict/list: written as JSON
      - str: written as-is (for .md files)
      - alt.Chart: saved as PNG (key must end with .png)

    Keys are used as filenames.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    for filename, content in artifacts.items():
        filepath = log_path / filename
        if isinstance(content, (dict, list)):
            with open(filepath, "w") as f:
                json.dump(content, f, indent=2, default=str)
        elif isinstance(content, str):
            with open(filepath, "w") as f:
                f.write(content)
        elif isinstance(content, alt.Chart):
            save_png(content, str(filepath))
        else:
            # Try JSON serialization as fallback
            with open(filepath, "w") as f:
                json.dump(content, f, indent=2, default=str)


def create_log_dir(task_slug: str) -> str:
    """Create a timestamped log directory for a task.

    Returns the path to the created directory.
    """
    project_root = Path(__file__).parent.parent
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_dir = project_root / "logs" / f"{timestamp}_{task_slug}"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir)


def extract_recommendations(draco_result: dict) -> dict:
    """Extract structured recommendations from a Draco result.

    Takes a single result dict from run_draco() and returns a clean summary
    that can be passed to Claude for spec generation.

    Returns dict with:
      - mark_type: str (e.g. "point", "bar", "line")
      - encodings: list of {channel, field?, aggregate?, binning?, stack?}
      - scales: list of {channel, type, zero?}
      - coordinates: str ("cartesian" or "polar")
      - facets: list of {channel, field, binning?} (if any)
      - task: str or None ("summary" or "value")
      - cost: list[int]
      - violations: dict
    """
    spec = draco_result["spec_dict"]
    view = spec["view"][0]
    mark = view["mark"][0]

    # Extract encodings
    encodings = []
    for enc in mark.get("encoding", []):
        entry = {"channel": enc["channel"]}
        if enc.get("field"):
            entry["field"] = enc["field"]
        if enc.get("aggregate"):
            entry["aggregate"] = enc["aggregate"]
        if enc.get("binning"):
            entry["binning"] = enc["binning"]
        if enc.get("stack"):
            entry["stack"] = enc["stack"]
        encodings.append(entry)

    # Extract scales (view-local first, then top-level fallback)
    raw_scales = view.get("scale") or spec.get("scale") or []
    scales = []
    for s in raw_scales:
        entry = {"channel": s["channel"], "type": s["type"]}
        if s.get("zero") is not None:
            entry["zero"] = s["zero"]
        scales.append(entry)

    # Extract facets
    facets = []
    for f in view.get("facet") or []:
        entry = {"channel": f["channel"], "field": f["field"]}
        if f.get("binning"):
            entry["binning"] = f["binning"]
        facets.append(entry)

    return {
        "mark_type": mark["type"],
        "encodings": encodings,
        "scales": scales,
        "coordinates": view.get("coordinates", "cartesian"),
        "facets": facets,
        "task": spec.get("task"),
        "cost": draco_result.get("cost", []),
        "violations": draco_result.get("violations", {}),
    }


def spec_dict_to_vegalite(spec_dict: dict, df: pd.DataFrame) -> dict:
    """Convert a Draco spec dict to a Vega-Lite JSON spec.

    Renders via AltairRenderer, then extracts the Vega-Lite JSON.
    """
    chart = render_altair(spec_dict, df)
    return chart.to_dict()
