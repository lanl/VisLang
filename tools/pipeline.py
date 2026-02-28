"""VisLang pipeline: Draco-guided visualization with LLM calls via LiteLLM."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from textwrap import dedent

import altair as alt
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from tools.prompts import (
    COMPARISON_SYSTEM,
    COMPARISON_USER,
    DRACO_ADDON_SYSTEM,
    DRACO_ADDON_USER,
    FIELD_SELECTION_SYSTEM,
    FIELD_SELECTION_USER,
    SPEC_SYSTEM,
    SPEC_USER,
    VALIDATION_ERROR_RETRY,
)
from tools.vislang import (
    create_log_dir,
    extract_recommendations,
    get_schema_facts,
    load_data,
    log_task,
    run_draco,
    save_png,
    validate_vegalite,
    vl_to_altair,
)

DEFAULT_MODEL = os.environ.get("VISLANG_MODEL", "anthropic/claude-opus-4-6")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class VislangResult:
    """Holds all outputs from a VisLang pipeline run."""

    prompt: str
    data_path: str
    model: str

    # Draco path
    draco_spec: dict = field(default_factory=dict)
    draco_reasoning: str = ""
    draco_chart: alt.Chart | None = None

    # Baseline path
    baseline_spec: dict = field(default_factory=dict)
    baseline_reasoning: str = ""
    baseline_chart: alt.Chart | None = None

    # Draco recommendations
    recommendations: dict = field(default_factory=dict)
    field_selection: dict = field(default_factory=dict)

    # Comparison
    comparison_markdown: str = ""
    comparison_summary: str = ""
    comparison_verdict: str = ""

    # Metadata
    log_dir: str = ""

    @property
    def side_by_side(self) -> alt.HConcatChart:
        """Return side-by-side Altair chart."""
        charts = []
        if self.draco_chart is not None:
            charts.append(self.draco_chart.properties(title="With Draco"))
        if self.baseline_chart is not None:
            charts.append(self.baseline_chart.properties(title="Without Draco"))
        return alt.hconcat(*charts)

    def _repr_html_(self) -> str:
        """Rich HTML display for Jupyter notebooks."""
        parts = []
        parts.append(f"<h3>VisLang: {self.prompt}</h3>")
        parts.append(f"<p><b>Data:</b> {self.data_path} | <b>Model:</b> {self.model}</p>")

        if self.draco_chart is not None and self.baseline_chart is not None:
            chart_html = self.side_by_side._repr_html_()
            parts.append(chart_html)
        elif self.draco_chart is not None:
            parts.append("<h4>With Draco</h4>")
            parts.append(self.draco_chart._repr_html_())
        elif self.baseline_chart is not None:
            parts.append("<h4>Without Draco</h4>")
            parts.append(self.baseline_chart._repr_html_())

        if self.comparison_summary:
            parts.append(f"<p><b>Summary:</b> {self.comparison_summary}</p>")
        if self.comparison_verdict:
            parts.append(f"<p><b>Verdict:</b> {self.comparison_verdict}</p>")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_vislang(
    prompt: str,
    data_path: str,
    model: str | None = None,
    log: bool = True,
    notebook: bool = False,
) -> VislangResult:
    """Run the VisLang pipeline synchronously.

    Args:
        prompt: Natural language visualization request.
        data_path: Path to data file (resolved via vega-datasets/data/).
        model: LiteLLM model string (default: VISLANG_MODEL env or claude-sonnet).
        log: Whether to write artifacts to logs/ directory.
        notebook: Whether to create and execute a Jupyter notebook.

    Returns:
        VislangResult with both specs, charts, comparison, and metadata.
    """
    if model is None:
        model = DEFAULT_MODEL

    # Handle Jupyter's running event loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import nest_asyncio
        nest_asyncio.apply()

    return asyncio.run(_run_pipeline(prompt, data_path, model, log, notebook))


# ---------------------------------------------------------------------------
# Async pipeline
# ---------------------------------------------------------------------------

async def _run_pipeline(
    prompt: str,
    data_path: str,
    model: str,
    log: bool,
    notebook: bool,
) -> VislangResult:
    """Async orchestration of the full pipeline."""
    # Step 1: Load data, compute schema
    df = load_data(data_path)
    schema, schema_facts = get_schema_facts(df)
    col_summary = _build_column_summary(schema, df)
    sample = _build_sample_rows(df)

    log_dir = ""
    if log:
        slug = prompt.lower()[:40].replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        model_short = _short_model_name(model)
        log_dir = create_log_dir(f"{model_short}_{slug}")
        log_task(log_dir, **{
            "meta.json": {
                "prompt": prompt,
                "data_path": data_path,
                "model": model,
                "timestamp": datetime.now().isoformat(),
            }
        })

    # Step 2: LLM field selection
    field_selection_system = FIELD_SELECTION_SYSTEM
    field_selection_user = FIELD_SELECTION_USER.format(
        prompt=prompt,
        column_summary=col_summary,
        sample_rows=sample,
    )
    schema_fields = {f["name"] for f in schema.get("field", [])}
    field_retry_messages: list[dict] = []
    field_result = {}
    for _field_attempt in range(3):
        field_result = await _llm_call(
            model, field_selection_system, field_selection_user,
            retry_messages=field_retry_messages or None,
        )
        fields = field_result.get("fields", [])
        bad = [f for f in fields if f not in schema_fields]
        hints = field_result.get("channel_hints", {})
        bad += [k for k in hints if k not in schema_fields]
        if not bad:
            break
        field_retry_messages.append({"role": "assistant", "content": json.dumps(field_result)})
        field_retry_messages.append({
            "role": "user",
            "content": (
                f"The following field names are not in the schema: {bad}. "
                f"Valid field names are: {sorted(schema_fields)}. "
                "Please return corrected JSON using only exact field names from the schema."
            ),
        })

    fields = field_result.get("fields", [])
    mark_hint = field_result.get("mark_hint")
    channel_hints = field_result.get("channel_hints", {})

    if log:
        _log_llm_call(log_dir, "1_field_selection",
                       field_selection_system, field_selection_user, field_result,
                       retry_messages=field_retry_messages or None)

    # Step 3: Build partial spec facts and run Draco
    partial_spec = _build_partial_spec_facts(schema_facts, fields, mark_hint, channel_hints)

    draco_results = None
    recommendations = {}
    draco_error = None
    try:
        draco_results = run_draco(partial_spec)
        if draco_results:
            recommendations = extract_recommendations(draco_results[0])
    except Exception as e:
        draco_error = str(e)

    if log:
        log_task(log_dir, **{"2_draco_solver_input.json": partial_spec})
        if draco_results:
            log_task(log_dir, **{
                "2_draco_solver_output.json": {
                    "spec_dict": draco_results[0]["spec_dict"],
                    "cost": draco_results[0]["cost"],
                    "violations": draco_results[0]["violations"],
                },
                "2_draco_recommendations.json": recommendations,
            })
        if draco_error:
            log_task(log_dir, **{"2_draco_error.txt": draco_error})

    # Step 4: Generate specs (Draco path only if we got recommendations)
    base_user = SPEC_USER.format(
        prompt=prompt,
        data_path=data_path,
        column_summary=col_summary,
        sample_rows=sample,
    )

    baseline_spec_system = SPEC_SYSTEM
    baseline_spec_user = base_user

    draco_spec = {}
    draco_reasoning = ""
    draco_result = {}
    draco_chart: alt.Chart | None = None

    if recommendations:
        recs_json = json.dumps(recommendations, indent=2, default=str)
        draco_spec_system = SPEC_SYSTEM + DRACO_ADDON_SYSTEM
        draco_spec_user = base_user + DRACO_ADDON_USER.format(
            recommendations_json=recs_json,
        )

        draco_task = _generate_spec_with_retry(
            model, draco_spec_system, draco_spec_user, df,
            log_dir=log_dir, call_name="2a_draco_spec",
        )
        baseline_task = _generate_spec_with_retry(
            model, baseline_spec_system, baseline_spec_user, df,
            log_dir=log_dir, call_name="2b_baseline_spec",
        )

        (draco_result, draco_chart), (baseline_result, baseline_chart) = \
            await asyncio.gather(draco_task, baseline_task)

        draco_spec = draco_result.get("vegalite_spec", {})
        draco_reasoning = draco_result.get("reasoning", "")
    else:
        recs_json = "{}"
        baseline_result, baseline_chart = await _generate_spec_with_retry(
            model, baseline_spec_system, baseline_spec_user, df,
            log_dir=log_dir, call_name="2b_baseline_spec",
        )

    baseline_spec = baseline_result.get("vegalite_spec", {})
    baseline_reasoning = baseline_result.get("reasoning", "")

    if log:
        if recommendations:
            _log_llm_call(log_dir, "2a_draco_spec",
                           draco_spec_system, draco_spec_user, draco_result)
        _log_llm_call(log_dir, "2b_baseline_spec",
                       baseline_spec_system, baseline_spec_user, baseline_result)
        log_task(log_dir, **{
            "3b_baseline_spec.vl.json": baseline_spec,
            "3b_baseline_reasoning.md": f"# Baseline Design\n\n{baseline_reasoning}",
        })
        if recommendations:
            log_task(log_dir, **{
                "3a_draco_spec.vl.json": draco_spec,
                "3a_draco_reasoning.md": f"# Draco-Informed Design\n\n{draco_reasoning}",
            })
        for name, chart in [("3a_draco_spec.png", draco_chart),
                             ("3b_baseline_spec.png", baseline_chart)]:
            if chart is not None:
                try:
                    save_png(chart, os.path.join(log_dir, name))
                except Exception as e:
                    log_task(log_dir, **{
                        f"{name}.render_error.txt": str(e),
                    })

    # Step 5: LLM comparison (skip if Draco produced nothing)
    comparison_md = ""
    comparison_summary = ""
    comparison_verdict = ""

    if recommendations:
        comparison_system = COMPARISON_SYSTEM
        comparison_user = COMPARISON_USER.format(
            prompt=prompt,
            recommendations_json=recs_json,
            draco_spec_json=json.dumps(draco_spec, indent=2),
            draco_reasoning=draco_reasoning,
            baseline_spec_json=json.dumps(baseline_spec, indent=2),
            baseline_reasoning=baseline_reasoning,
        )

        chart_images = []
        for label, chart in [("Draco-informed visualization:", draco_chart),
                             ("Baseline visualization:", baseline_chart)]:
            if chart is not None:
                try:
                    chart_images.append((label, _chart_to_base64(chart)))
                except Exception:
                    pass

        comparison = await _llm_call_with_retries(
            model, comparison_system, comparison_user,
            images=chart_images or None,
        )

        comparison_md = comparison.get("comparison_markdown", "")
        comparison_summary = comparison.get("summary", "")
        comparison_verdict = comparison.get("verdict", "")

    if log and comparison_md:
        _log_llm_call(log_dir, "3_comparison",
                       comparison_system, comparison_user, comparison)
        log_task(log_dir, **{"4_comparison.md": comparison_md})

    result = VislangResult(
        prompt=prompt,
        data_path=data_path,
        model=model,
        draco_spec=draco_spec,
        draco_reasoning=draco_reasoning,
        draco_chart=draco_chart,
        baseline_spec=baseline_spec,
        baseline_reasoning=baseline_reasoning,
        baseline_chart=baseline_chart,
        recommendations=recommendations,
        field_selection=field_result,
        comparison_markdown=comparison_md,
        comparison_summary=comparison_summary,
        comparison_verdict=comparison_verdict,
        log_dir=log_dir,
    )

    if notebook:
        _create_notebook(result)

    return result


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

async def _llm_call(
    model: str,
    system: str,
    user: str,
    retry_messages: list[dict] | None = None,
    images: list[tuple[str, str]] | None = None,
) -> dict:
    """Single LLM call via litellm with JSON output. Returns parsed dict.

    images: optional list of (label, base64_png) tuples to include in the
    user message as vision content.
    """
    import litellm

    if images:
        user_content: list[dict] | str = [{"type": "text", "text": user}]
        for label, b64 in images:
            user_content.append({"type": "text", "text": label})
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
    else:
        user_content = user

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    if retry_messages:
        messages.extend(retry_messages)

    response = await litellm.acompletion(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    text = response.choices[0].message.content or ""
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    text = text.strip()

    # Parse robustly: some models emit trailing text after the JSON object
    decoder = json.JSONDecoder()
    result, _ = decoder.raw_decode(text)
    return result


async def _llm_call_with_retries(
    model: str,
    system: str,
    user: str,
    max_retries: int = 3,
    **kwargs,
) -> dict:
    """Wrapper around _llm_call that retries on JSON parse errors."""
    for attempt in range(max_retries):
        try:
            return await _llm_call(model, system, user, **kwargs)
        except (json.JSONDecodeError, ValueError):
            if attempt == max_retries - 1:
                raise
    return {}  # unreachable, but satisfies type checker


async def _generate_spec_with_retry(
    model: str,
    system: str,
    user: str,
    df: pd.DataFrame,
    max_retries: int = 3,
    log_dir: str = "",
    call_name: str = "",
) -> tuple[dict, alt.Chart | None]:
    """Generate a Vega-Lite spec with validation and render retry loop.

    Returns (result_dict, chart). The chart is None only if all retries fail
    at both validation and rendering. If retries are exhausted, the full
    conversation history is logged for debugging.
    """
    retry_messages: list[dict] = []
    last_result: dict = {}
    last_chart: alt.Chart | None = None

    for attempt in range(max_retries):
        try:
            result = await _llm_call(model, system, user, retry_messages or None)
        except (json.JSONDecodeError, ValueError) as e:
            # LLM returned unparseable JSON — treat as a retryable error
            retry_messages.append({
                "role": "user",
                "content": VALIDATION_ERROR_RETRY.format(
                    error=f"Your response was not valid JSON: {e}"),
            })
            continue
        last_result = result

        spec = result.get("vegalite_spec", {})

        # Check 1: structural validation
        valid, err = validate_vegalite(spec)
        if not valid:
            retry_messages.append({"role": "assistant", "content": json.dumps(result)})
            retry_messages.append({
                "role": "user",
                "content": VALIDATION_ERROR_RETRY.format(error=err),
            })
            continue

        # Check 2: can we build an Altair chart?
        try:
            chart = vl_to_altair(spec, df)
        except Exception as e:
            retry_messages.append({"role": "assistant", "content": json.dumps(result)})
            retry_messages.append({
                "role": "user",
                "content": VALIDATION_ERROR_RETRY.format(
                    error=f"Chart construction failed: {e}"),
            })
            continue

        # Check 3: can we render to PNG?
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                save_png(chart, tmp.name)
        except Exception as e:
            last_chart = chart  # keep the chart even if PNG fails
            retry_messages.append({"role": "assistant", "content": json.dumps(result)})
            retry_messages.append({
                "role": "user",
                "content": VALIDATION_ERROR_RETRY.format(
                    error=f"PNG rendering failed: {e}"),
            })
            continue

        return result, chart

    # Retries exhausted — log the full retry history for debugging
    if log_dir and call_name:
        _log_llm_call(log_dir, f"{call_name}_retries_exhausted",
                       system, user, last_result, retry_messages)

    return last_result, last_chart


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_llm_call(
    log_dir: str,
    call_name: str,
    system: str,
    user: str,
    response: dict,
    retry_messages: list[dict] | None = None,
) -> None:
    """Log the full input and output of an LLM call as a readable markdown file."""
    call_dir = os.path.join(log_dir, "llm_calls")
    os.makedirs(call_dir, exist_ok=True)

    parts = [f"# LLM Call: {call_name}\n"]

    parts.append("## System Prompt\n")
    parts.append(system.strip())
    parts.append("")

    parts.append("## User Prompt\n")
    parts.append(user.strip())
    parts.append("")

    if retry_messages:
        parts.append("## Retry Messages\n")
        for msg in retry_messages:
            parts.append(f"### {msg['role'].title()}\n")
            parts.append(msg["content"].strip())
            parts.append("")

    with open(os.path.join(call_dir, f"{call_name}_input.md"), "w") as f:
        f.write("\n".join(parts))
    with open(os.path.join(call_dir, f"{call_name}_output.json"), "w") as f:
        json.dump(response, f, indent=2, default=str)


def _short_model_name(model: str) -> str:
    """Extract a short label from a LiteLLM model string.

    e.g. 'anthropic/claude-opus-4-6' -> 'opus46'
         'anthropic/claude-sonnet-4-5-20250929' -> 'sonnet45'
         'openai/gpt-4o' -> 'gpt4o'
         'ollama_chat/llama3.1' -> 'llama31'
    """
    # Strip provider prefix
    name = model.split("/", 1)[-1] if "/" in model else model
    # Remove 'claude-' prefix
    name = name.replace("claude-", "")
    # Remove date suffixes like -20250929
    import re
    name = re.sub(r"-\d{8,}$", "", name)
    # Collapse separators and digits: opus-4-6 -> opus46, sonnet-4-5 -> sonnet45
    name = re.sub(r"[.-]", "", name)
    return name


def _chart_to_base64(chart: alt.Chart) -> str:
    """Render an Altair chart to a base64-encoded PNG string."""
    import base64
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        save_png(chart, tmp.name)
        tmp.seek(0)
        return base64.b64encode(tmp.read()).decode("ascii")


def _build_column_summary(schema: dict, df: pd.DataFrame | None = None) -> str:
    """Format schema dict into a readable string for LLM prompts.

    If a DataFrame is provided, includes min/max for numeric fields so the LLM
    knows the actual data range (prevents invalid filters like year==2010 when
    max is 2005).
    """
    lines = []
    for field_info in schema.get("field", []):
        name = field_info.get("name", "?")
        dtype = field_info.get("type", "?")
        unique = field_info.get("unique", "")
        parts = []
        if unique:
            parts.append(f"unique={unique}")
        if df is not None and name in df.columns and dtype == "number":
            col = df[name]
            parts.append(f"min={col.min()}, max={col.max()}")
        extra = f" ({', '.join(parts)})" if parts else ""
        lines.append(f"- {name}: {dtype}{extra}")
    return "\n".join(lines) if lines else "(no schema available)"


def _build_sample_rows(df: pd.DataFrame, n: int = 3) -> str:
    """First N rows as a readable string."""
    return df.head(n).to_string(index=False)


def _build_partial_spec_facts(
    schema_facts: list[str],
    fields: list[str],
    mark_hint: str | None,
    channel_hints: dict,
) -> list[str]:
    """Build Draco ASP partial spec facts from field selection."""
    facts = list(schema_facts)
    facts.append("entity(view,root,v0).")
    facts.append("entity(mark,v0,m0).")

    if mark_hint:
        facts.append(f'attribute((mark,type),m0,{mark_hint}).')

    for i, field_name in enumerate(fields):
        enc_id = f"e{i}"
        facts.append(f"entity(encoding,m0,{enc_id}).")
        facts.append(f"attribute((encoding,field),{enc_id},{field_name}).")

        if field_name in channel_hints:
            channel = channel_hints[field_name]
            facts.append(f"attribute((encoding,channel),{enc_id},{channel}).")

    return facts


def _create_notebook(result: VislangResult) -> str:
    """Create and execute a Jupyter notebook for the result."""
    import nbformat
    import subprocess

    project_root = Path(__file__).parent.parent
    output_dir = project_root / "output"
    output_dir.mkdir(exist_ok=True)

    slug = result.prompt.lower()[:40].replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    nb_path = output_dir / f"{slug}.ipynb"

    nb = nbformat.v4.new_notebook()
    cells = []

    # Cell 1: Title
    cells.append(nbformat.v4.new_markdown_cell(
        f"# {result.prompt}\n\n**Data:** {result.data_path} | **Model:** {result.model}"
    ))

    # Cell 2: Setup
    setup_code = dedent(f"""\
        import json
        import altair as alt
        import pandas as pd
        from tools.vislang import load_data, vl_to_altair

        df = load_data({result.data_path!r})
        print(f"{{len(df)}} rows, {{len(df.columns)}} columns")
        df.head(3)
    """)
    cells.append(nbformat.v4.new_code_cell(setup_code))

    # Cell 3: Draco recommendations
    recs_code = dedent(f"""\
        recommendations = json.loads({json.dumps(json.dumps(result.recommendations, indent=2, default=str))!s})
        print(json.dumps(recommendations, indent=2))
    """)
    cells.append(nbformat.v4.new_code_cell(recs_code))

    # Cell 4: With Draco header
    cells.append(nbformat.v4.new_markdown_cell("## With Draco Guidance"))

    # Cell 5: Draco chart
    draco_code = dedent(f"""\
        draco_spec = json.loads({json.dumps(json.dumps(result.draco_spec))!s})
        chart_draco = vl_to_altair(draco_spec, df)
        chart_draco
    """)
    cells.append(nbformat.v4.new_code_cell(draco_code))

    # Cell 6: Without Draco header
    cells.append(nbformat.v4.new_markdown_cell("## Without Draco"))

    # Cell 7: Baseline chart
    baseline_code = dedent(f"""\
        baseline_spec = json.loads({json.dumps(json.dumps(result.baseline_spec))!s})
        chart_no_draco = vl_to_altair(baseline_spec, df)
        chart_no_draco
    """)
    cells.append(nbformat.v4.new_code_cell(baseline_code))

    # Cell 8: Comparison header
    cells.append(nbformat.v4.new_markdown_cell("## Comparison"))

    # Cell 9: Side-by-side + comparison text
    comparison_code = dedent(f"""\
        from IPython.display import Markdown, display

        display(alt.hconcat(
            chart_draco.properties(title="With Draco"),
            chart_no_draco.properties(title="Without Draco"),
        ))

        display(Markdown({result.comparison_markdown!r}))
    """)
    cells.append(nbformat.v4.new_code_cell(comparison_code))

    nb.cells = cells

    with open(nb_path, "w") as f:
        nbformat.write(nb, f)

    # Execute
    subprocess.run(
        ["jupyter", "nbconvert", "--to", "notebook", "--execute", "--inplace", str(nb_path)],
        cwd=str(project_root),
        capture_output=True,
    )

    return str(nb_path)
