# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: Show how life expectancy relates to fertility rate across countries, and whether income level explains the pattern

DATASET SCHEMA:
- country: string (unique=187)
- income: number (unique=187, min=599, max=132877)
- health: number (unique=140, min=48.5, max=84.1)
- population: number (unique=187, min=52993, max=1376048943)
- region: string (unique=6)

SAMPLE ROWS:
    country  income  health  population                   region
Afghanistan    1925   57.63    32526562               south_asia
    Albania   10620   76.00     2896679      europe_central_asia
    Algeria   13434   76.50    39666519 middle_east_north_africa

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
