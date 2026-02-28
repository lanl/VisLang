# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: Show how the scale and frequency of natural disasters has changed over time across different disaster types

DATASET SCHEMA:
- entity: string (unique=11)
- year: number (unique=117, min=1900, max=2017)
- deaths: number (unique=680, min=1, max=3706227)

SAMPLE ROWS:
               entity  year  deaths
All natural disasters  1900 1267360
All natural disasters  1901  200018
All natural disasters  1902   46037

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
