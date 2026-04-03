import re
import json
import copy
import dspy
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Any

import draco
from draco import dict_to_facts, answer_set_to_dict
from draco.renderer.altair.altair_renderer import AltairRenderer


# ---------------------------------------------------------------------------
# Draco example included in the LLM prompt so it knows the expected format
# ---------------------------------------------------------------------------

DRACO_EXAMPLE_SPEC = """\
{
  "view": [
    {
      "mark": [
        {
          "type": "bar",
          "encoding": [
            {"channel": "x", "field": "Category"},
            {"channel": "y", "field": "Amount", "aggregate": "mean"}
          ]
        }
      ]
    }
  ]
}"""


# ---------------------------------------------------------------------------
# DSPy Signatures
# ---------------------------------------------------------------------------

_DRACO_OUTPUT_DESC = (
    "A partial Draco visualization spec as a JSON object. "
    "Use Draco format (NOT Vega-Lite). The spec MUST have exactly ONE view with ONE mark — "
    "do NOT create multiple views or multiple marks. "
    "The spec should have a 'view' array containing a single object with a 'mark' array containing a single mark object. "
    "Each mark has a 'type' (one of: point, bar, line, area, text, tick, rect) and an 'encoding' array. "
    "Each encoding has 'channel' (x, y, color, size, shape, text), 'field' (matching a dataset field name), "
    "and optionally 'aggregate' (count, mean, median, min, max, stdev, sum). "
    "Leave out encodings/details not mentioned by the user — Draco will complete them. "
    "Example:\n" + DRACO_EXAMPLE_SPEC + "\n"
    "Output ONLY the JSON object, no explanation or markdown."
)

_DRACO_RETRY_OUTPUT_DESC = (
    "Corrected partial Draco visualization spec as a JSON object. "
    "Use Draco format (NOT Vega-Lite). The spec MUST have exactly ONE view with ONE mark — "
    "do NOT create multiple views or multiple marks. "
    "Each mark has a 'type' (one of: point, bar, line, area, text, tick, rect) and an 'encoding' array. "
    "Each encoding has 'channel' (x, y, color, size, shape, text), 'field' (matching a dataset field name), "
    "and optionally 'aggregate' (count, mean, median, min, max, stdev, sum). "
    "Fix the issues described in the error message. "
    "Output ONLY the JSON object, no explanation or markdown."
)

_SCHEMA_DESC = "Dataset field names and Draco types (number, string, boolean, datetime) with sample values"


class DracoIntentSignature(dspy.Signature):
    user_request = dspy.InputField(desc="Natural language visualization request")
    data_schema = dspy.InputField(desc=_SCHEMA_DESC)
    draco_partial_spec = dspy.OutputField(desc=_DRACO_OUTPUT_DESC)


class RetryDracoIntentSignature(dspy.Signature):
    user_request = dspy.InputField(desc="Original visualization request")
    data_schema = dspy.InputField(desc=_SCHEMA_DESC)
    previous_spec = dspy.InputField(desc="The previous (broken) Draco partial spec attempt")
    error_message = dspy.InputField(desc="Error message from Draco completion or rendering")
    draco_partial_spec = dspy.OutputField(desc=_DRACO_RETRY_OUTPUT_DESC)


# ---------------------------------------------------------------------------
# Field name sanitization (ASP/clingo requires lowercase, no spaces)
# ---------------------------------------------------------------------------

def _sanitize_field_name(name: str) -> str:
    """Convert a field name to an ASP-safe identifier (lowercase, underscores)."""
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "field"


def _build_field_name_map(records: list[dict]) -> dict[str, str]:
    """Return {original_name: sanitized_name} for all fields in the dataset.
    Handles collisions by appending a suffix."""
    if not records:
        return {}

    originals = list(dict.fromkeys(k for record in records[:1] for k in record.keys()))
    name_map = {}
    seen: set[str] = set()
    for orig in originals:
        safe = _sanitize_field_name(orig)
        if safe in seen:
            i = 2
            while f"{safe}_{i}" in seen:
                i += 1
            safe = f"{safe}_{i}"
        seen.add(safe)
        name_map[orig] = safe
    return name_map


def sanitize_records(records: list[dict], name_map: dict[str, str]) -> list[dict]:
    """Rename fields in records to ASP-safe names."""
    return [{name_map.get(k, k): v for k, v in record.items()} for record in records]


def unsanitize_spec(vl_spec: dict, name_map: dict[str, str]) -> dict:
    """Restore original field names in a Vega-Lite spec dict (string replacement)."""
    reverse_map = {v: k for k, v in name_map.items()}
    spec_str = json.dumps(vl_spec)
    for safe, orig in reverse_map.items():
        spec_str = spec_str.replace(json.dumps(safe), json.dumps(orig))
    return json.loads(spec_str)


# ---------------------------------------------------------------------------
# Schema / field inference
# ---------------------------------------------------------------------------

def _detect_draco_type(values: list) -> str:
    """Map a list of Python values to a Draco field type."""
    vals = [v for v in values if v is not None]
    if not vals:
        return "string"

    sample = vals[0]
    if isinstance(sample, bool):
        return "boolean"
    if isinstance(sample, (int, float)):
        return "number"
    if isinstance(sample, str) and re.search(
        r"\d{4}[-/]\d{2}|\d{2}[-/]\d{4}"
        r"|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec",
        sample, re.I,
    ):
        return "datetime"
    return "string"


def infer_draco_fields(records: list[dict]) -> list[dict]:
    """Return Draco-format field metadata for a dataset.

    Expects records with already-sanitized field names.
    Example output: [{"name": "passenger_capacity", "type": "number", "unique": 42}, ...]
    """
    if not records:
        return []

    fields: dict[str, list] = {}
    for record in records[:100]:
        for k, v in record.items():
            fields.setdefault(k, []).append(v)

    return [
        {
            "name": name,
            "type": _detect_draco_type(vals),
            "unique": len(set(str(v) for v in vals)),
        }
        for name, vals in fields.items()
    ]


def infer_schema_str(records: list[dict], sample_n: int = 5) -> str:
    """Return a human-readable schema string for the LLM prompt.

    Expects records with already-sanitized field names.
    """
    draco_fields = infer_draco_fields(records)
    if not draco_fields:
        return "Empty dataset"

    field_samples: dict[str, list] = {}
    for record in records[:sample_n]:
        for k, v in record.items():
            field_samples.setdefault(k, []).append(v)

    lines = [
        f"  {f['name']!r}: {f['type']}  (e.g. {field_samples.get(f['name'], [])[:sample_n]})"
        for f in draco_fields
    ]
    return f"Fields ({len(records)} rows):\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Draco spec building & completion
# ---------------------------------------------------------------------------

def build_draco_spec(partial_view_spec: dict, records: list[dict]) -> dict:
    """Combine the LLM's partial view spec with dataset metadata into a full Draco spec."""
    spec = {
        "number_rows": len(records),
        "field": infer_draco_fields(records),
    }

    if "view" in partial_view_spec:
        views = partial_view_spec["view"]
        # Draco only supports single-view specs; take the first view if LLM gave multiple
        if isinstance(views, list) and len(views) > 1:
            views = [views[0]]
        spec["view"] = views
    else:
        # LLM gave a single view object (or bare mark) — wrap it
        spec["view"] = [partial_view_spec]

    # Ensure each view's mark is a list (LLM sometimes gives a dict)
    for view in spec["view"]:
        if "mark" in view and isinstance(view["mark"], dict):
            view["mark"] = [view["mark"]]

    return spec


def complete_with_draco(draco_spec: dict, draco_models: int = 1) -> list[tuple[dict, Any]]:
    """Run the Draco constraint solver on a full spec dict. Returns completed (spec, answer_set) pairs."""
    facts = dict_to_facts(draco_spec)
    print(f"    [DEBUG] Draco spec: {json.dumps(draco_spec, indent=2)[:800]}")
    print(f"    [DEBUG] Facts ({len(facts)}): {facts[:5]}...")
    d = draco.Draco()
    completions = d.complete_spec(facts, models=draco_models)
    results = [(answer_set_to_dict(m.answer_set), m.answer_set) for m in completions]
    print(f"    [DEBUG] Completions returned: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

def classify_failure(error_msg: str) -> str:
    if not error_msg:
        return "unknown"
    lower = error_msg.lower()
    if "invalid json" in lower or "jsondecodeerror" in lower:
        return "json_parse"
    if "draco completion error" in lower and "parsing failed" in lower:
        return "draco_parse"
    if "draco completion error" in lower:
        return "draco_completion"
    if "draco render error" in lower:
        return "draco_render"
    return "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_fences(raw: str) -> str:
    """Remove markdown code fences from LLM output."""
    return re.sub(r"```[a-z]*\s*", "", raw).strip()


def _parse_llm_json(raw) -> dict:
    """Parse LLM output into a dict, handling strings and markdown fences."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        cleaned = _strip_fences(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM did not return valid JSON: {e}") from e
    raise ValueError(f"Expected dict or str from LLM, got {type(raw).__name__}")


def _divider(label: str = "", char: str = "─", width: int = 70):
    if label:
        pad = width - len(label) - 2
        print(f"\n{'─' * (pad // 2)} {label} {'─' * (pad - pad // 2)}")
    else:
        print(char * width)


# ---------------------------------------------------------------------------
# DSPy prompt/response capture
# ---------------------------------------------------------------------------

def capture_dspy_prompt() -> list[dict]:
    try:
        history = dspy.settings.lm.history
        if not history:
            return []
        return history[-1].get("messages", [])
    except Exception:
        return []


def capture_dspy_response() -> str:
    try:
        history = dspy.settings.lm.history
        if not history:
            return ""
        resp = history[-1].get("response")
        return resp.choices[0].message.content if resp else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_renderer = AltairRenderer()


def try_render_draco(draco_spec: dict, safe_records: list[dict]) -> tuple[Any, str | None]:
    """Render a completed Draco spec. Returns (chart, None) on success or (None, error_str) on failure.

    Expects records with sanitized (ASP-safe) field names matching the spec.
    """
    try:
        chart = _renderer.render(draco_spec, pd.DataFrame(safe_records))
        chart.to_dict()  # force Vega-Lite compilation to surface errors
        return chart, None
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

class DracoVizGenerator(dspy.Module):
    """Generate a visualization by asking an LLM for a partial Draco spec,
    completing it with the Draco constraint solver, and rendering with Altair."""

    def __init__(self, max_retries: int = 5):
        super().__init__()
        self.first_try = dspy.ChainOfThought(DracoIntentSignature)
        self.retry = dspy.ChainOfThought(RetryDracoIntentSignature)
        self.max_retries = max_retries

    # -- public entry point --------------------------------------------------

    def forward(self, user_request: str, records: list[dict]):
        # Sanitize field names to be ASP-safe (lowercase, underscores)
        name_map = _build_field_name_map(records)
        safe_records = sanitize_records(records, name_map)

        schema_str = infer_schema_str(safe_records)

        # Initial LLM call
        result = self.first_try(user_request=user_request, data_schema=schema_str)
        raw_partial = result.draco_partial_spec
        prompt_msgs = capture_dspy_prompt()
        raw_response = capture_dspy_response()

        attempts = []
        for attempt_num in range(1, self.max_retries + 1):
            attempt_record = {
                "attempt": attempt_num,
                "prompt_messages": prompt_msgs,
                "raw_response": raw_response,
                "raw_partial": raw_partial,
            }

            outcome = self._single_attempt(raw_partial, safe_records)

            attempt_record.update(outcome["record"])
            attempts.append(attempt_record)
            print(f"    Draco attempt {attempt_num}: {outcome['message']}")

            if outcome["success"]:
                # Restore original field names in the VL spec for display/saving
                vl_spec = unsanitize_spec(outcome["vl_spec"], name_map)
                return {
                    "is_valid": True,
                    "spec_dict": vl_spec,
                    "draco_spec": outcome["draco_spec"],
                    "chart": outcome["chart"],
                    "error": None,
                    "attempts": attempts,
                }

            # Give up if we've exhausted retries
            if attempt_num >= self.max_retries:
                break

            # Retry: feed error back to LLM
            raw_partial = self._ask_retry(
                user_request, schema_str, raw_partial, outcome["error"]
            )
            prompt_msgs = capture_dspy_prompt()
            raw_response = capture_dspy_response()

        last = attempts[-1] if attempts else {}
        return {
            "is_valid": False,
            "spec_dict": last.get("spec_dict"),
            "draco_spec": last.get("draco_spec"),
            "chart": None,
            "error": last.get("error"),
            "raw": last.get("raw_partial"),
            "attempts": attempts,
        }

    # -- single attempt logic ------------------------------------------------

    def _single_attempt(self, raw_partial, records: list[dict]) -> dict:
        """Try to parse, complete, and render one LLM output.

        Returns a dict with keys: success, record, message, error,
        and on success: vl_spec, draco_spec, chart.
        """
        # Step 1: Parse JSON
        try:
            partial_dict = _parse_llm_json(raw_partial)
        except ValueError as e:
            error_msg = f"Draco completion error: {e}"
            return self._fail(error_msg, "json_parse")

        # Step 2: Build full spec & run Draco solver
        try:
            draco_spec = build_draco_spec(partial_dict, records)
            draco_results = complete_with_draco(draco_spec, draco_models=1)
            if not draco_results:
                raise ValueError("Draco returned no completions")
        except Exception as e:
            error_msg = f"Draco completion error: {e}"
            return self._fail(error_msg, classify_failure(error_msg))

        # Step 3: Try rendering each completion
        render_errors = []
        best_meta = None
        for model_idx, (completed_spec, answer_set) in enumerate(draco_results, 1):
            chart, render_error = try_render_draco(completed_spec, records)
            if render_error is None:
                vl_spec = chart.to_dict()
                vl_spec.pop("datasets", None)
                vl_spec.pop("data", None)
                return {
                    "success": True,
                    "message": "Valid spec",
                    "error": None,
                    "vl_spec": vl_spec,
                    "draco_spec": completed_spec,
                    "chart": chart,
                    "record": {
                        "success": True,
                        "error": None,
                        "failure_mode": None,
                        "draco_model_index": model_idx,
                        "draco_answer_set": str(answer_set),
                        "draco_spec": completed_spec,
                        "spec_dict": vl_spec,
                    },
                }
            render_errors.append(render_error)
            best_meta = {
                "draco_model_index": model_idx,
                "draco_answer_set": str(answer_set),
                "draco_spec": completed_spec,
            }

        error_msg = "Draco render error: " + " | ".join(render_errors[:3])
        result = self._fail(error_msg, classify_failure(error_msg))
        if best_meta:
            result["record"].update(best_meta)
        return result

    @staticmethod
    def _fail(error_msg: str, failure_mode: str) -> dict:
        return {
            "success": False,
            "message": error_msg,
            "error": error_msg,
            "record": {
                "success": False,
                "error": error_msg,
                "failure_mode": failure_mode,
            },
        }

    def _ask_retry(self, user_request, schema_str, raw_partial, error_msg) -> str:
        previous_spec = raw_partial if isinstance(raw_partial, str) else json.dumps(raw_partial, indent=2)
        result = self.retry(
            user_request=user_request,
            data_schema=schema_str,
            previous_spec=previous_spec,
            error_message=error_msg,
        )
        return result.draco_partial_spec


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_json_file(filepath: str) -> list[dict]:
    with open(filepath, "r") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError(f"Unsupported JSON structure: {type(data)}")


def load_prompts_file(prompts_path: str) -> dict[str, dict]:
    """Load prompts JSON. Supports new format {id: {text, dataset, level}}
    and legacy format {prompts: [{id, text, enabled}]}."""
    with open(prompts_path, "r") as f:
        payload = json.load(f)

    # New format: flat dict of {prompt_id: {text, dataset, level}}
    if "prompts" not in payload and isinstance(payload, dict):
        first_val = next(iter(payload.values()), None)
        if isinstance(first_val, dict) and "text" in first_val:
            return payload

    # Legacy format: {prompts: [{id, text, enabled}]}
    prompts = payload.get("prompts", [])
    enabled = [p for p in prompts if p.get("enabled", True)]
    if not enabled:
        raise ValueError("No enabled prompts found")
    result = {}
    for p in enabled:
        if not p.get("id") or not p.get("text"):
            raise ValueError("Each prompt needs 'id' and 'text'")
        result[p["id"]] = p
    return result


def _pair_prompts_with_datasets(
    prompts_dict: dict[str, dict],
    dataset_files: dict[str, Path],
    data_dir: str,
    levels: set[str] | None,
    selected_ids: set[str] | None,
) -> list[tuple[str, dict, Path]]:
    """Filter prompts and match each to its dataset file."""
    paired = []
    for pid, pobj in prompts_dict.items():
        if selected_ids and pid not in selected_ids:
            continue
        if levels and pobj.get("level") not in levels:
            continue

        target_ds = pobj.get("dataset")
        if target_ds and target_ds in dataset_files:
            paired.append((pid, pobj, dataset_files[target_ds]))
        elif target_ds:
            print(f"  Warning: prompt '{pid}' references dataset '{target_ds}' not found in {data_dir}/, skipping.")
        else:
            for fpath in dataset_files.values():
                paired.append((pid, pobj, fpath))
    return paired


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_prompt_dataset_matrix(
    data_dir: str = "data",
    prompts_file: str = "prompts/prompts.json",
    output_file: str = "generatedViz/run_results.json",
    specs_dir: str = "generatedViz/specs",
    max_retries: int = 5,
    ollama_base: str = "http://localhost:11434",
    model_name: str = "mistral",
    prompt_limit: int | None = None,
    schema_mode: str = "draco-intent",
    level_filter: list[str] | str | None = None,
    prompt_ids: list[str] | str | None = None,
) -> list[dict]:
    """Run the prompt-dataset matrix pipeline in Draco intent mode."""

    if schema_mode != "draco-intent":
        raise ValueError(
            f"Only 'draco-intent' is supported in this simplified pipeline, got {schema_mode!r}."
        )

    # Configure DSPy
    lm = dspy.LM(model=f"ollama/{model_name}", api_base=ollama_base)
    dspy.settings.configure(lm=lm)

    prompts_dict = load_prompts_file(prompts_file)

    # Normalize filters
    levels = None if level_filter is None else ({level_filter} if isinstance(level_filter, str) else set(level_filter))
    selected_ids = None if prompt_ids is None else ({prompt_ids} if isinstance(prompt_ids, str) else set(prompt_ids))

    # Discover datasets
    dataset_files: dict[str, Path] = {p.name: p for p in sorted(Path(data_dir).glob("*.json"))}
    if not dataset_files:
        print(f"No datasets found in {data_dir}/")
        return []

    # Pair prompts with datasets
    paired = _pair_prompts_with_datasets(prompts_dict, dataset_files, data_dir, levels, selected_ids)
    if prompt_limit is not None:
        paired = paired[:prompt_limit]

    total_runs = len(paired)
    _divider("BATCH RUN START")
    print(f"  Datasets : {len(dataset_files)} in {data_dir}/")
    print(f"  Prompts  : {len(prompts_dict)} loaded, {total_runs} runs after filtering")
    print(f"  Schema   : {schema_mode}")
    print(f"  Levels   : {levels or 'all'}")
    print(f"  Retries  : max {max_retries} per run")

    pipeline = DracoVizGenerator(max_retries=max_retries)
    all_results: list[dict] = []
    valid_count = 0
    Path(specs_dir).mkdir(parents=True, exist_ok=True)

    for run_idx, (pid, pobj, dataset_path) in enumerate(paired, 1):
        ptxt = pobj["text"] if isinstance(pobj, dict) else str(pobj)
        prompt_level = pobj.get("level", "unknown") if isinstance(pobj, dict) else "unknown"

        _divider(f"RUN {run_idx}/{total_runs}")
        print(f"  Dataset  : {dataset_path.name}")
        print(f"  Prompt   : {pid} (level={prompt_level})")
        print(f"  Text     : {ptxt[:80]}{'...' if len(ptxt) > 80 else ''}")

        records = load_json_file(str(dataset_path))
        result = pipeline(user_request=ptxt, records=records)

        run_record = {
            "run_index": run_idx,
            "dataset": str(dataset_path),
            "dataset_name": dataset_path.stem,
            "prompt_id": pid,
            "prompt_text": ptxt,
            "prompt_level": prompt_level,
            "schema_mode": schema_mode,
            "is_valid": result["is_valid"],
            "error": result.get("error"),
            "attempts": result.get("attempts", []),
            "total_attempts": len(result.get("attempts", [])),
        }

        if result["is_valid"] and result.get("spec_dict"):
            saved_spec = copy.deepcopy(result["spec_dict"])
            saved_spec.pop("datasets", None)
            saved_spec["data"] = {"url": str(dataset_path)}

            date_str = datetime.now().strftime("%Y-%m-%d")
            spec_path = Path(specs_dir) / f"{pid}_{schema_mode}{max_retries}_{date_str}.json"
            with open(spec_path, "w") as f:
                json.dump(saved_spec, f, indent=2)

            run_record["spec_file"] = str(spec_path)
            run_record["spec_dict"] = result["spec_dict"]
            valid_count += 1
            print(f"  Result   : Valid (attempt {run_record['total_attempts']})")
            print(f"  Saved    : {spec_path}")
        else:
            run_record["spec_file"] = None
            run_record["spec_dict"] = result.get("spec_dict")
            run_record["raw"] = result.get("raw")
            print(f"  Result   : FAILED after {run_record['total_attempts']} attempts")
            print(f"  Error    : {result.get('error', 'unknown')}")

        all_results.append(run_record)

    # Save results (strip non-serializable fields)
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_results = copy.deepcopy(all_results)
    for r in save_results:
        for a in r.get("attempts", []):
            a.pop("prompt_messages", None)
            a.pop("draco_spec", None)

    with open(output_path, "w") as f:
        json.dump(save_results, f, indent=2)

    _divider("BATCH RUN COMPLETE")
    print(f"  Valid    : {valid_count}/{total_runs}")
    print(f"  Results  : {output_path}")
    print(f"  Specs    : {specs_dir}/")
    _divider()

    return all_results
