import re
import json
import copy
import dspy
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema.exceptions import ValidationError

# Draco integration
import draco
from draco import dict_to_facts, answer_set_to_dict


# DSPy signature for Draco partial spec intent
class DracoIntentSignature(dspy.Signature):
    user_request = dspy.InputField(desc="Natural language visualization request")
    data_schema = dspy.InputField(desc="Data schema or sample records describing the dataset structure, fields, and types")
    vega_lite_partial_spec = dspy.OutputField(
        desc=(
            "An incomplete Vega-Lite spec (as JSON/dict) that captures the user's visualization intent. "
            "Do not output a full spec—leave some fields (like encoding/channel/mark details) unspecified if not mentioned by the user. "
            "Output ONLY the JSON object, no explanation or markdown."
        )
    )


class RetryDracoIntentSignature(dspy.Signature):
    user_request = dspy.InputField(desc="Original visualization request")
    data_schema = dspy.InputField(
        desc="Data schema or sample records describing the dataset structure, fields, and types"
    )
    previous_spec = dspy.InputField(desc="The previous (broken) Draco partial Vega-Lite spec attempt")
    error_message = dspy.InputField(desc="Error message from Draco completion or rendering")
    vega_lite_partial_spec = dspy.OutputField(
        desc=(
            "Corrected incomplete Vega-Lite spec (as JSON/dict) for Draco completion. "
            "Return ONLY JSON, no markdown or explanation. "
            "Fix the issues described in the error message."
        )
    )



def llm_to_draco_vl_spec(partial_spec_input, draco_models: int = 1):
    """
    Convert a partial Vega-Lite spec to Draco facts and complete it.
    """

    partial_spec = partial_spec_input

    if isinstance(partial_spec, str):
        partial_spec = _strip_fences(partial_spec)
        try:
            partial_spec = json.loads(partial_spec)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM did not return valid JSON for partial Vega-Lite spec: {e}") from e

    if not isinstance(partial_spec, dict):
        raise ValueError(
            f"Expected partial Vega-Lite spec as dict, got {type(partial_spec).__name__}"
        )

    facts = dict_to_facts(partial_spec)

    # Draco complete step
    d = draco.Draco()
    completions = d.complete_spec(facts, models=draco_models)

    # Convert to VL
    results = []
    for model in completions:
        vl_spec = answer_set_to_dict(model.answer_set)
        results.append((vl_spec, model.answer_set))
    return results

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


def classify_failure(error_msg: str) -> str:
    """Classify a failed attempt into a failure-mode category."""
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
        self.draco_first_try = dspy.ChainOfThought(DracoIntentSignature)
        self.draco_retry = dspy.ChainOfThought(RetryDracoIntentSignature)
        self.max_retries = max_retries

    def forward(self, user_request: str, records: list[dict]):
        schema_str = infer_schema(records)

        attempts = []
        result = self.draco_first_try(user_request=user_request, data_schema=schema_str)
        prompt_msgs = capture_dspy_prompt()
        raw_response = capture_dspy_response()
        raw_partial = result.vega_lite_partial_spec

        for attempt_num in range(1, self.max_retries + 1):
            attempt_record = {
                "attempt": attempt_num,
                "prompt_messages": prompt_msgs,
                "raw_response": raw_response,
                "raw_partial": raw_partial,
            }

            try:
                draco_results = llm_to_draco_vl_spec(raw_partial, draco_models=1)
                if not draco_results:
                    raise ValueError("Draco returned no completions")
            except Exception as e:
                error_msg = f"Draco completion error: {e}"
                attempt_record.update({
                    "success": False,
                    "error": error_msg,
                    "failure_mode": classify_failure(error_msg),
                })
                attempts.append(attempt_record)
                print(f"    Draco attempt {attempt_num}: {error_msg}")

                if attempt_num >= self.max_retries:
                    break

                previous_spec = raw_partial if isinstance(raw_partial, str) else json.dumps(raw_partial, indent=2)
                result = self.draco_retry(
                    user_request=user_request,
                    data_schema=schema_str,
                    previous_spec=previous_spec,
                    error_message=error_msg,
                )
                prompt_msgs = capture_dspy_prompt()
                raw_response = capture_dspy_response()
                raw_partial = result.vega_lite_partial_spec
                continue

            render_errors = []
            best_attempt_meta = None
            for model_idx, (vl_spec, draco_answer_set) in enumerate(draco_results, 1):
                _, render_error = try_render_altair(vl_spec, records)
                if render_error is None:
                    attempt_record.update({
                        "success": True,
                        "error": None,
                        "failure_mode": None,
                        "draco_model_index": model_idx,
                        "draco_answer_set": str(draco_answer_set),
                        "spec_dict": vl_spec,
                    })
                    attempts.append(attempt_record)
                    print(f"    Draco attempt {attempt_num}: Valid spec")
                    return {
                        "is_valid": True,
                        "spec_dict": vl_spec,
                        "error": None,
                        "attempts": attempts,
                    }

                render_errors.append(render_error)
                best_attempt_meta = {
                    "draco_model_index": model_idx,
                    "draco_answer_set": str(draco_answer_set),
                    "spec_dict": vl_spec,
                }

            error_msg = "Draco render error: " + " | ".join(render_errors[:3])
            attempt_record.update({
                "success": False,
                "error": error_msg,
                "failure_mode": classify_failure(error_msg),
            })
            if best_attempt_meta is not None:
                attempt_record.update(best_attempt_meta)
            attempts.append(attempt_record)
            print(f"    Draco attempt {attempt_num}: {error_msg}")

            if attempt_num >= self.max_retries:
                break

            previous_spec = raw_partial if isinstance(raw_partial, str) else json.dumps(raw_partial, indent=2)
            result = self.draco_retry(
                user_request=user_request,
                data_schema=schema_str,
                previous_spec=previous_spec,
                error_message=error_msg,
            )
            prompt_msgs = capture_dspy_prompt()
            raw_response = capture_dspy_response()
            raw_partial = result.vega_lite_partial_spec

        last = attempts[-1] if attempts else {}
        return {
            "is_valid": False,
            "spec_dict": last.get("spec_dict"),
            "error": last.get("error"),
            "raw": last.get("raw_partial"),
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
    schema_mode: str = "draco-intent",
    level_filter: list[str] | str | None = None,
) -> list[dict]:
    """
    Run the prompt-dataset matrix pipeline in Draco intent mode.
    """

    if schema_mode != "draco-intent":
        raise ValueError(
            f"Only 'draco-intent' is supported in this simplified pipeline, got {schema_mode!r}."
        )
    
    # configure dspy
    lm = dspy.LM(model=f"ollama/{model_name}", api_base=ollama_base)
    dspy.settings.configure(lm=lm)

    # load prompts (dict of {id: {text, dataset, level}})
    prompts_dict = load_prompts_file(prompts_file)

    # normalize level_filter
    if level_filter is None:
        levels = None
    elif isinstance(level_filter, str):
        levels = {level_filter}
    else:
        levels = set(level_filter)

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
        # level filter
        if levels and pobj.get("level") not in levels:
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
    print(f"  Levels   : {levels or 'all'}")
    print(f"  Retries  : max {max_retries} per run")

    pipeline = VegaLiteGenerator(max_retries=max_retries)
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
