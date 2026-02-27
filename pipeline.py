import re
import json
import copy
import dspy
import os
from pathlib import Path
from typing import Any

import requests
from functools import lru_cache
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
        desc="Inferred schema: field names, types, and a few sample values"
    )
    vega_spec = dspy.OutputField(
        desc=(
            "Valid Vega-Lite JSON spec. Return ONLY raw JSON, no markdown or "
            "explanation. Ensure 'mark' only contains one thing. x, y, etc. "
            "must be defined inside encoding, not inside mark"
        )
    )


class RetryVegaLite(dspy.Signature):
    user_request = dspy.InputField(desc="Original visualization request")
    data_schema = dspy.InputField(desc="Inferred schema: field names, types, sample values")
    previous_spec = dspy.InputField(desc="The previous (broken) Vega-Lite JSON spec attempt")
    error_message = dspy.InputField(
        desc="Error message from parsing or rendering the previous spec"
    )
    vega_spec = dspy.OutputField(
        desc=(
            "Corrected Vega-Lite JSON spec. Return ONLY raw JSON, no markdown "
            "or explanation. Fix the issues described in the error message."
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

    def __init__(self, max_retries: int = 5):
        super().__init__()
        self.first_try = dspy.ChainOfThought(DirectVegaLite)
        self.retry = dspy.ChainOfThought(RetryVegaLite)
        self.max_retries = max_retries

    def forward(self, user_request: str, records: list[dict]):
        schema_str = infer_schema(records)
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
                attempt_record.update({"success": False, "error": error_msg, "raw": raw})
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
            attempt_record.update({"success": True, "error": None, "spec_dict": spec_dict})
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


def load_prompts_file(prompts_path: str) -> list[dict]:
    with open(prompts_path, "r") as f:
        payload = json.load(f)
    prompts = payload.get("prompts", [])
    enabled = [p for p in prompts if p.get("enabled", True)]
    if not enabled:
        raise ValueError("No enabled prompts found")
    for p in enabled:
        if not p.get("id") or not p.get("text"):
            raise ValueError("Each prompt needs 'id' and 'text'")
    return enabled


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
) -> list[dict]:
    
    # configure dspy
    lm = dspy.LM(model=f"ollama/{model_name}", api_base=ollama_base)
    dspy.settings.configure(lm=lm)

    #load number of prompts from file
    prompt_items = load_prompts_file(prompts_file)
    if prompt_limit is not None:
        prompt_items = prompt_items[:prompt_limit]

    # look in data path for json files
    datasets_in_path = sorted(Path(data_dir).glob("*.json"))
    if not datasets_in_path:
        print(f"No datasets found in {data_dir}/")
        return []

    total_runs = len(datasets_in_path) * len(prompt_items)
    _divider("BATCH RUN START")
    print(f"  Datasets : {len(datasets_in_path)} from {data_dir}/")
    print(f"  Prompts  : {len(prompt_items)} from {prompts_file}")
    print(f"  Total    : {total_runs} runs  (max {max_retries} retries each)")

    #
    pipeline = VegaLiteGenerator(max_retries=max_retries)
    all_results: list[dict] = []
    valid_count = 0
    run_idx = 0


    Path(specs_dir).mkdir(parents=True, exist_ok=True)

    for aDataset in datasets_in_path:
        records = load_json_file(str(aDataset))
        dataset_name = aDataset.stem

        for prompt in prompt_items:
            run_idx += 1
            pid = prompt["id"]
            ptxt = prompt["text"]

            _divider(f"RUN {run_idx}/{total_runs}")
            print(f"  Dataset  : {aDataset}")
            print(f"  Prompt   : {pid}")
            print(f"  Text     : {ptxt[:80]}{'...' if len(ptxt) > 80 else ''}")

            result = pipeline(user_request=ptxt, records=records)

            run_record = {
                "run_index": run_idx,
                "dataset": str(aDataset),
                "dataset_name": dataset_name,
                "prompt_id": pid,
                "prompt_text": ptxt,
                "is_valid": result["is_valid"],
                "error": result.get("error"),
                "attempts": result.get("attempts", []),
                "total_attempts": len(result.get("attempts", [])),
            }

            # Save spec file (URL-based, no inline data)
            if result["is_valid"] and result.get("spec_dict"):
                saved_spec = spec_with_url(result["spec_dict"], str(aDataset))
                spec_filename = f"{dataset_name}__{pid}.json"
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
