# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: How does the age distribution of the US population differ between males and females?

DATASET SCHEMA:
- year: number (unique=15, min=1850, max=2000)
- age: number (unique=19, min=0, max=90)
- sex: number (unique=2, min=1, max=2)
- people: number (unique=570, min=5259, max=11635647)

SAMPLE ROWS:
 year  age  sex  people
 1850    0    1 1483789
 1850    0    2 1450376
 1850    5    1 1411067

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
