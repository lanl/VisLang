# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: Compare the typical body mass of each penguin species and whether flipper length varies with it

DATASET SCHEMA:
- species: string (unique=3)
- island: string (unique=3)
- beak_Length_mm: number (unique=165, min=32.1, max=59.6)
- beak_Depth_mm: number (unique=81, min=13.1, max=21.5)
- flipper_Length_mm: number (unique=56, min=172.0, max=231.0)
- body_Mass_g: number (unique=95, min=2700.0, max=6300.0)
- sex: string (unique=4)

SAMPLE ROWS:
species    island  beak_Length_mm  beak_Depth_mm  flipper_Length_mm  body_Mass_g    sex
 Adelie Torgersen            39.1           18.7              181.0       3750.0   MALE
 Adelie Torgersen            39.5           17.4              186.0       3800.0 FEMALE
 Adelie Torgersen            40.3           18.0              195.0       3250.0 FEMALE

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
