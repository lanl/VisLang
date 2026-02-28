# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: How has the mix of energy sources in Iowa changed over time, and which sources are growing fastest?

DATASET SCHEMA:
- year: string (unique=17)
- source: string (unique=3)
- net_generation: number (unique=51, min=1437, max=42750)

SAMPLE ROWS:
      year       source  net_generation
2001-01-01 Fossil Fuels           35361
2002-01-01 Fossil Fuels           35991
2003-01-01 Fossil Fuels           36234

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
