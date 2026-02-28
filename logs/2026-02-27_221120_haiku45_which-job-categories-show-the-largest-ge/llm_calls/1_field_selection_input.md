# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: Which job categories show the largest gender gap in employment share?

DATASET SCHEMA:
- job: string (unique=255)
- sex: string (unique=2)
- year: number (unique=15, min=1850, max=2000)
- count: number (unique=4415, min=0, max=11270779)
- perc: number (unique=4711, min=0.0, max=0.44687157453606)

SAMPLE ROWS:
                 job sex  year  count     perc
Accountant / Auditor men  1850    708 0.000131
Accountant / Auditor men  1860   1805 0.000214
Accountant / Auditor men  1870   1310 0.000100

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
