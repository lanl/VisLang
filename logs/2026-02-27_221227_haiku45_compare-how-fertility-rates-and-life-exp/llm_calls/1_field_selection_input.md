# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: Compare how fertility rates and life expectancy relate across world regions, and whether population size plays a role

DATASET SCHEMA:
- year: number (unique=11, min=1955, max=2005)
- country: string (unique=62)
- cluster: number (unique=6, min=0, max=5)
- pop: number (unique=682, min=82656, max=1304887562)
- life_expect: number (unique=614, min=27.79, max=82.5)
- fertility: number (unique=398, min=0.96, max=8.23)

SAMPLE ROWS:
 year     country  cluster     pop  life_expect  fertility
 1955 Afghanistan        0 7971931        43.88       7.42
 1960 Afghanistan        0 8622466        45.03       7.38
 1965 Afghanistan        0 9565147        46.13       7.35

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
