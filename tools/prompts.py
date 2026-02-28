"""Prompt templates for VisLang LLM calls."""

# ---------------------------------------------------------------------------
# Call 1: Field Selection
# ---------------------------------------------------------------------------

FIELD_SELECTION_SYSTEM = """\
You are a visualization field-selection assistant. Given a dataset schema and a \
user's visualization request, you decide which fields are relevant and suggest \
structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. \
"bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user \
explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}
"""

FIELD_SELECTION_USER = """\
USER REQUEST: {prompt}

DATASET SCHEMA:
{column_summary}

SAMPLE ROWS:
{sample_rows}

Select the fields most relevant to the user's request. Only include fields that \
should be encoded in the visualization. Return JSON.
"""

# ---------------------------------------------------------------------------
# Calls 2a/2b: Spec Generation (shared base + Draco addon)
# ---------------------------------------------------------------------------

SPEC_SYSTEM = """\
You are generating a Vega-Lite visualization spec.

Design the best visualization for the user's request:
- Choose the most effective mark type for the data and task
- Assign fields to channels for maximum readability
- Use appropriate scales, aggregation, and binning
- Add a descriptive title and axis labels
- Include tooltips for interactive exploration
- Apply appropriate mark styling (filled marks, opacity for overplotting, etc.)
- Use a thoughtful color scheme
- Size the chart appropriately

Return a JSON object with exactly these keys:
- "vegalite_spec": the complete Vega-Lite specification (no $schema or data needed)
- "reasoning": brief explanation of your design choices
"""

SPEC_USER = """\
USER REQUEST: {prompt}

DATA FILE: {data_path}

COLUMN SUMMARY:
{column_summary}

SAMPLE ROWS:
{sample_rows}

Generate the best Vega-Lite spec for this request. Return JSON.
"""

DRACO_ADDON_SYSTEM = """

ADDITIONAL GUIDANCE — Draco Recommendations:

You have been provided with structural recommendations from Draco 2, a \
constraint-based visualization recommender that optimizes for perceptual \
effectiveness. You MUST honor these structural decisions:
- Mark type: use the recommended mark type
- Channel assignments: use the recommended field→channel mappings
- Scale types: use the recommended scale types
- Aggregation/binning: apply if recommended

You may add polish on top of these structural choices, but do not contradict them.
"""

DRACO_ADDON_USER = """

DRACO RECOMMENDATIONS:
{recommendations_json}

Honor Draco's structural choices above while designing the spec.
"""

# ---------------------------------------------------------------------------
# Call 3: Comparison
# ---------------------------------------------------------------------------

COMPARISON_SYSTEM = """\
You are a visualization analyst comparing two Vega-Lite specs for the same task: \
one informed by Draco's constraint-based recommendations, one designed by an LLM \
without Draco.

You receive the Vega-Lite specs, reasoning text, Draco's recommendations, and \
rendered images of both visualizations. Use the images to assess the actual visual \
output — layout, readability, color effectiveness, overplotting, axis formatting, \
and overall clarity — not just the spec structure.

When evaluating, only make judgements about the relative quality of the two specs \
that you are highly confident in. Limit your discussion of differences that are
not very signficiant.

Use the Draco recommendations to assess:
- Which structural choices came from Draco vs. were added/changed by the LLM
- Whether the Draco-informed spec actually followed Draco's advice
- Whether any deviations from Draco's recommendations were justified
- Whether any difference in quality between the two specs can be attributed to Draco's contribution, or whether it was due to the LLM making different choices independently of Draco's input. Make sure to mention this point in your final evaluation.

Return a JSON object with exactly these keys:
- "comparison_markdown": detailed markdown analysis covering mark type choices, \
channel/encoding decisions, scale choices, where the Draco-informed spec followed \
or deviated from Draco's advice, enhancements added by each approach, and which \
better serves the user's intent
- "summary": 2-3 sentence summary of the key differences
- "verdict": which approach better serves the user's intent ("draco", "baseline", \
or "tie") with brief justification
"""

COMPARISON_USER = """\
USER REQUEST: {prompt}

DRACO RECOMMENDATIONS:
{recommendations_json}

DRACO-INFORMED SPEC:
{draco_spec_json}

DRACO-INFORMED REASONING:
{draco_reasoning}

BASELINE SPEC:
{baseline_spec_json}

BASELINE REASONING:
{baseline_reasoning}

Analyze both approaches. Pay special attention to whether the Draco-informed spec \
actually followed Draco's structural advice and whether differences between the two \
specs are attributable to Draco's contribution. Return JSON.
"""

# ---------------------------------------------------------------------------
# Retry fragment
# ---------------------------------------------------------------------------

VALIDATION_ERROR_RETRY = """\
The Vega-Lite spec you returned failed validation with this error:

{error}

Please fix the spec and return the corrected JSON in the same format \
(with "vegalite_spec" and "reasoning" keys).
"""
