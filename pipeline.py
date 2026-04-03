import re
import json
import copy
import dspy
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from functools import lru_cache
from genson import SchemaBuilder
from flatten_json import flatten as flatten_obj
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError


# vegalite schema validation

@lru_cache(maxsize=1)
def _load_vegalite_schema(
    schema_url: str = "https://vega.github.io/schema/vega-lite/v5.json",
) -> dict:
    r = requests.get(schema_url, timeout=20)
    r.raise_for_status()
    return r.json()


def validate_vegalite_spec(spec_input) -> dict:
    #returns: {is_valid: bool, errors: list[str], spec_dict: dict|None}

    if isinstance(spec_input, str):
        try:
            spec = json.loads(spec_input)
        except json.JSONDecodeError as e:
            return {"is_valid": False, "errors": [f"Invalid JSON: {e}"], "spec_dict": None}
    elif isinstance(spec_input, dict):
        spec = spec_input
    else:
        return {"is_valid": False, "errors": [f"Unsupported input type: {type(spec_input)}"], "spec_dict": None}

    try:
        schema = _load_vegalite_schema(
            spec.get("$schema", "https://vega.github.io/schema/vega-lite/v5.json")
        )
        validator = Draft7Validator(schema)
        errs = sorted(validator.iter_errors(spec), key=lambda e: list(e.path))
        if errs:
            messages = []
            for e in errs[:20]:
                path = ".".join(map(str, e.path)) or "<root>"
                messages.append(f"{path}: {e.message}")
            return {"is_valid": False, "errors": messages, "spec_dict": spec}
    except Exception as e:
        return {"is_valid": False, "errors": [f"Schema validation failed: {e}"], "spec_dict": spec}

    return {"is_valid": True, "errors": [], "spec_dict": spec}


# DSPy signatures

class DirectVegaLite(dspy.Signature):
    user_request = dspy.InputField(desc="Natural language visualization request")
    data_schema = dspy.InputField(
        desc="Data schema or sample records describing the dataset structure, fields, and types"
    )
    vega_spec = dspy.OutputField(
        desc=(
            "Valid Vega-Lite JSON spec. Return ONLY raw JSON, no markdown or "
            "explanation. Ensure 'mark' only contains one thing. x, y, etc. "
            "must be defined inside encoding, not inside mark. "
            "If the data contains nested objects or arrays, use Vega-Lite "
            "'flatten' or 'fold' transforms as needed."
        )
    )


class RetryVegaLite(dspy.Signature):
    user_request = dspy.InputField(desc="Original visualization request")
    data_schema = dspy.InputField(
        desc="Data schema or sample records describing the dataset structure, fields, and types"
    )
    previous_spec = dspy.InputField(desc="The previous (broken) Vega-Lite JSON spec attempt")
    error_message = dspy.InputField(
        desc="Error message from parsing or rendering the previous spec"
    )
    vega_spec = dspy.OutputField(
        desc=(
            "Corrected Vega-Lite JSON spec. Return ONLY raw JSON, no markdown "
            "or explanation. Fix the issues described in the error message. "
            "If the data contains nested objects or arrays, use Vega-Lite "
            "'flatten' or 'fold' transforms as needed."
        )
    )

#
def infer_schema(records: list[dict], sample_n: int = 5) -> str:
    if not records:
        return "Empty dataset"

    fields: dict[str, dict] = {}
    for record in records[:100]:
        for k, v in record.items():
            if k not in fields:
                fields[k] = {"values": [], "type": None}
            fields[k]["values"].append(v)

    def vl_type(values):
        vals = [v for v in values if v is not None]
        if not vals:
            return "nominal"
        sample = vals[0]
        if isinstance(sample, bool):
            return "nominal"
        if isinstance(sample, (int, float)):
            if len(set(vals)) <= 10:
                return "nominal"
            return "quantitative"
        if isinstance(sample, str):
            if re.search(
                r"\d{4}[-/]\d{2}|\d{2}[-/]\d{4}"
                r"|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec",
                sample,
                re.I,
            ):
                return "temporal"
        return "nominal"

    lines = []
    for field, info in fields.items():
        ftype = vl_type(info["values"])
        samples = info["values"][:sample_n]
        lines.append(f"  {field!r}: {ftype}  (e.g. {samples})")

    return f"Fields ({len(records)} rows):\n" + "\n".join(lines)


def infer_schema_genson(records: list[dict], sample_n: int = 5) -> str:
    """Use genson to produce a JSON Schema, then append sample values per top-level field."""
    if not records:
        return "Empty dataset"

    builder = SchemaBuilder()
    for record in records[:100]:
        builder.add_object(record)
    schema = builder.to_schema()

    # trim unnecessary top-level boilerplate
    schema.pop("$schema", None)
    schema.pop("$id", None)

    # collect sample values for each top-level field
    samples: dict[str, list] = {}
    for record in records[:sample_n]:
        for k, v in record.items():
            samples.setdefault(k, []).append(v)

    sample_lines = []
    for field, vals in samples.items():
        sample_lines.append(f"  {field!r}: {vals}")

    schema_json = json.dumps(schema, indent=2)
    return (
        f"JSON Schema ({len(records)} rows):\n{schema_json}\n\n"
        f"Sample values:\n" + "\n".join(sample_lines)
    )


def infer_schema_raw(records: list[dict], n: int = 5, max_chars: int = 3000) -> str:
    """Return the first N records as raw JSON (truncated). No schema info."""
    if not records:
        return "Empty dataset"
    raw = json.dumps(records[:n], indent=2)
    if len(raw) > max_chars:
        raw = raw[:max_chars] + "\n... (truncated)"
    return f"Sample records ({len(records)} rows total):\n{raw}"


def flatten_records(records: list[dict], separator: str = ".") -> list[dict]:
    """Flatten nested dicts/lists in each record using dot-path keys."""
    return [flatten_obj(record, separator) for record in records]


def classify_failure(error_msg: str) -> str:
    """Classify a failed attempt into a failure-mode category."""
    if not error_msg:
        return "unknown"
    lower = error_msg.lower()

    if "json parse error" in lower or "invalid json" in lower or "jsondecodeerror" in lower:
        return "json_parse"
    if "is not defined in the schema" in lower or "additional properties" in lower:
        return "invalid_field"
    if "flatten" in lower or "nested" in lower or "array" in lower:
        return "missing_transform"
    if "altair render error" in lower:
        # sub-classify render errors
        if "field" in lower and ("not found" in lower or "undefined" in lower):
            return "invalid_field"
        return "render_error"
    if any(kw in lower for kw in ["validation", "schema", "is not valid", "is not one of"]):
        return "schema_validation"
    return "unknown"


# spec helpers 

def _strip_fences(raw: str) -> str:
    """Remove markdown code fences from LLM output."""
    return re.sub(r"```[a-z]*\s*", "", raw).strip()


def spec_with_url(spec: dict, aDataset: str) -> dict:
    """Return a copy of spec with data pointing to a URL (no inline data)."""
    s = copy.deepcopy(spec)
    s.pop("data", None)
    s["data"] = {"url": aDataset}
    return s


def spec_with_inline_data(spec: dict, records: list[dict]) -> dict:
    """Return a copy of spec with inline data values (for rendering)."""
    s = copy.deepcopy(spec)
    s["data"] = {"values": records}
    return s


# dspy prompt capture

def capture_dspy_prompt() -> list[dict]:
    """Return the full prompt messages from the last DSPy LM call."""
    try:
        history = dspy.settings.lm.history
        if not history:
            return []
        last = history[-1]
        return last.get("messages", [])
    except Exception:
        return []


def capture_dspy_response() -> str:
    """Return the raw text response from the last DSPy LM call."""
    try:
        history = dspy.settings.lm.history
        if not history:
            return ""
        last = history[-1]
        resp = last.get("response")
        if resp:
            return resp.choices[0].message.content
        return ""
    except Exception:
        return ""


# rendering

def try_render_altair(spec: dict, records: list[dict]) -> tuple[Any, str | None]:
    #Returns (chart_object, None) on success or (None, error_string) on failure.

    import altair as alt

    render_spec = spec_with_inline_data(spec, records)
    try:
        chart = alt.Chart.from_dict(render_spec)
        # Force Vega-Lite compilation to catch encoding errors
        chart.to_dict()
        return chart, None
    except ValidationError as e:
        return None, e.message

    except Exception as e:
        return None, str(e)


# main pipeline
class VegaLiteGenerator(dspy.Module):

    def __init__(self, max_retries: int = 5, schema_mode: str = "genson"):
        super().__init__()
        self.first_try = dspy.ChainOfThought(DirectVegaLite)
        self.retry = dspy.ChainOfThought(RetryVegaLite)
        self.max_retries = max_retries
        if schema_mode not in ("raw", "genson", "flat"):
            raise ValueError(f"Invalid schema_mode: {schema_mode!r}. Must be 'raw', 'genson', or 'flat'.")
        self.schema_mode = schema_mode

    def forward(self, user_request: str, records: list[dict]):
        # apply schema_mode routing
        if self.schema_mode == "flat":
            records = flatten_records(records)
            schema_str = infer_schema_genson(records)
        elif self.schema_mode == "genson":
            schema_str = infer_schema_genson(records)
        elif self.schema_mode == "raw":
            schema_str = infer_schema_raw(records)
        else:
            schema_str = infer_schema_genson(records)
        attempts = []

        # first attempt
        result = self.first_try(user_request=user_request, data_schema=schema_str)
        prompt_msgs = capture_dspy_prompt()
        raw_response = capture_dspy_response()
        raw = _strip_fences(result.vega_spec)

        for attempt_num in range(1, self.max_retries + 1):
            attempt_record = {
                "attempt": attempt_num,
                "prompt_messages": prompt_msgs,
                "raw_response": raw_response,
            }

            # try to parse json
            try:
                spec_dict = json.loads(raw)
                if isinstance(spec_dict, list):
                    spec_dict = spec_dict[0] if spec_dict else {}
                if not isinstance(spec_dict, dict):
                    raise ValueError(f"Expected dict, got {type(spec_dict).__name__}")
            except (json.JSONDecodeError, ValueError) as e:
                error_msg = f"JSON parse error: {e}"
                attempt_record.update({
                    "success": False,
                    "error": error_msg,
                    "failure_mode": classify_failure(error_msg),
                    "raw": raw,
                })
                attempts.append(attempt_record)
                print(f"    Attempt {attempt_num}: {error_msg}")

                if attempt_num >= self.max_retries:
                    break

                # retry and give error field
                result = self.retry(
                    user_request=user_request,
                    data_schema=schema_str,
                    previous_spec=raw,
                    error_message=error_msg,
                )
                prompt_msgs = capture_dspy_prompt()
                raw_response = capture_dspy_response()
                raw = _strip_fences(result.vega_spec)
                continue

            # try to render
            chart, render_error = try_render_altair(spec_dict, records)
            if render_error:
                error_msg = f"Altair render error: {render_error}"
                attempt_record.update({
                    "success": False,
                    "error": error_msg,
                    "failure_mode": classify_failure(error_msg),
                    "spec_dict": spec_dict,
                })
                attempts.append(attempt_record)
                print(f"    Attempt {attempt_num}: {error_msg}")

                if attempt_num >= self.max_retries:
                    break

                # retry with error field
                result = self.retry(
                    user_request=user_request,
                    data_schema=schema_str,
                    previous_spec=json.dumps(spec_dict, indent=2),
                    error_message=error_msg,
                )
                prompt_msgs = capture_dspy_prompt()
                raw_response = capture_dspy_response()
                raw = _strip_fences(result.vega_spec)
                continue

            # success
            attempt_record.update({"success": True, "error": None, "failure_mode": None, "spec_dict": spec_dict})
            attempts.append(attempt_record)
            print(f"    Attempt {attempt_num}: Valid spec")
            return {
                "is_valid": True,
                "spec_dict": spec_dict,
                "error": None,
                "attempts": attempts,
            }

        last = attempts[-1] if attempts else {}
        return {
            "is_valid": False,
            "spec_dict": last.get("spec_dict"),
            "error": last.get("error"),
            "raw": last.get("raw", raw),
            "attempts": attempts,
        }


def _divider(label: str = "", char: str = "─", width: int = 70):
    if label:
        pad = width - len(label) - 2
        print(f"\n{'─' * (pad // 2)} {label} {'─' * (pad - pad // 2)}")
    else:
        print(char * width)


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


#

def run_prompt_dataset_matrix(
    data_dir: str = "data",
    prompts_file: str = "prompts/prompts.json",
    output_file: str = "generatedViz/run_results.json",
    specs_dir: str = "generatedViz/specs",
    max_retries: int = 5,
    ollama_base: str = "http://localhost:11434",
    model_name: str = "mistral",
    prompt_limit: int | None = None,
    schema_mode: str = "genson",
    prompt_ids: list[str] | str | None = None,
) -> list[dict]:
    
    # configure dspy
    lm = dspy.LM(model=f"ollama/{model_name}", api_base=ollama_base)
    dspy.settings.configure(lm=lm)

    # load prompts (dict of {id: {text, dataset, level}})
    prompts_dict = load_prompts_file(prompts_file)

    # normalize prompt_ids filter
    if prompt_ids is None:
        pid_filter = None
    elif isinstance(prompt_ids, str):
        pid_filter = {prompt_ids}
    else:
        pid_filter = set(prompt_ids)

    # build lookup of available dataset files by filename
    dataset_files: dict[str, Path] = {}
    for p in sorted(Path(data_dir).glob("*.json")):
        dataset_files[p.name] = p
    if not dataset_files:
        print(f"No datasets found in {data_dir}/")
        return []

    # pair prompts with their datasets, applying level filter
    paired: list[tuple[str, dict, Path]] = []  # (prompt_id, prompt_obj, dataset_path)
    for pid, pobj in prompts_dict.items():
        # prompt_id filter
        if pid_filter and pid not in pid_filter:
            continue
        # match to dataset
        target_ds = pobj.get("dataset")
        if target_ds and target_ds in dataset_files:
            paired.append((pid, pobj, dataset_files[target_ds]))
        elif target_ds:
            print(f"  Warning: prompt '{pid}' references dataset '{target_ds}' not found in {data_dir}/, skipping.")
        else:
            # legacy prompts without 'dataset' field — run against all datasets
            for fpath in dataset_files.values():
                paired.append((pid, pobj, fpath))

    if prompt_limit is not None:
        paired = paired[:prompt_limit]

    total_runs = len(paired)
    _divider("BATCH RUN START")
    print(f"  Datasets : {len(dataset_files)} in {data_dir}/")
    print(f"  Prompts  : {len(prompts_dict)} loaded, {total_runs} runs after filtering")
    print(f"  Schema   : {schema_mode}")
    print(f"  Retries  : max {max_retries} per run")

    pipeline = VegaLiteGenerator(max_retries=max_retries, schema_mode=schema_mode)
    all_results: list[dict] = []
    valid_count = 0

    Path(specs_dir).mkdir(parents=True, exist_ok=True)

    for run_idx, (pid, pobj, dataset_path) in enumerate(paired, 1):
        ptxt = pobj["text"] if isinstance(pobj, dict) else str(pobj)
        prompt_level = pobj.get("level", "unknown") if isinstance(pobj, dict) else "unknown"
        dataset_name = dataset_path.stem

        _divider(f"RUN {run_idx}/{total_runs}")
        print(f"  Dataset  : {dataset_path.name}")
        print(f"  Prompt   : {pid} (level={prompt_level})")
        print(f"  Text     : {ptxt[:80]}{'...' if len(ptxt) > 80 else ''}")

        records = load_json_file(str(dataset_path))
        result = pipeline(user_request=ptxt, records=records)

        run_record = {
            "run_index": run_idx,
            "dataset": str(dataset_path),
            "dataset_name": dataset_name,
            "prompt_id": pid,
            "prompt_text": ptxt,
            "prompt_level": prompt_level,
            "schema_mode": schema_mode,
            "is_valid": result["is_valid"],
            "error": result.get("error"),
            "attempts": result.get("attempts", []),
            "total_attempts": len(result.get("attempts", [])),
        }

        # Save spec file (URL-based, no inline data)
        if result["is_valid"] and result.get("spec_dict"):
            saved_spec = spec_with_url(result["spec_dict"], str(dataset_path))
            date_str = datetime.now().strftime("%Y-%m-%d")
            spec_filename = f"{pid}_{schema_mode}{max_retries}_{date_str}.json"
            spec_path = Path(specs_dir) / spec_filename
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

    # save summary
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # strip prompt_messages from attempts before saving
    save_results = copy.deepcopy(all_results)
    for r in save_results:
        for a in r.get("attempts", []):
            a.pop("prompt_messages", None)

    with open(output_path, "w") as f:
        json.dump(save_results, f, indent=2)

    _divider("BATCH RUN COMPLETE")
    print(f"  Valid    : {valid_count}/{total_runs}")
    print(f"  Results  : {output_path}")
    print(f"  Specs    : {specs_dir}/")
    _divider()

    return all_results
