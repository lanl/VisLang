# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: Show how unemployment rates across industries changed during recessions vs expansions

DATASET SCHEMA:
- series: string (unique=14)
- year: number (unique=11, min=2000, max=2010)
- month: number (unique=12, min=1, max=12)
- count: number (unique=915, min=2, max=2440)
- rate: number (unique=147, min=0.30000000000000004, max=27.1)
- date: datetime (unique=122)

SAMPLE ROWS:
    series  year  month  count  rate                      date
Government  2000      1    430   2.1 2000-01-01 08:00:00+00:00
Government  2000      2    409   2.0 2000-02-01 08:00:00+00:00
Government  2000      3    311   1.5 2000-03-01 08:00:00+00:00

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
