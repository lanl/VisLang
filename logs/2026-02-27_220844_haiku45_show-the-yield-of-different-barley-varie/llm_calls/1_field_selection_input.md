# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: Show the yield of different barley varieties across sites — which varieties perform best and does it depend on location?

DATASET SCHEMA:
- yield: number (unique=114, min=14.43333, max=65.7667)
- variety: string (unique=10)
- year: number (unique=2, min=1931, max=1932)
- site: string (unique=6)

SAMPLE ROWS:
   yield   variety  year            site
27.00000 Manchuria  1931 University Farm
48.86667 Manchuria  1931          Waseca
27.43334 Manchuria  1931          Morris

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
