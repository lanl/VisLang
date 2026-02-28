# LLM Call: 1_field_selection

## System Prompt

You are a visualization field-selection assistant. Given a dataset schema and a user's visualization request, you decide which fields are relevant and suggest structural hints for the chart.

Return a JSON object with exactly these keys:
- "fields": list of field names (strings) from the schema that should be encoded
- "reasoning": one-sentence explanation of your choices
- "mark_hint": optional mark type if the user explicitly requested one (e.g. "bar", "line"), otherwise null
- "channel_hints": optional dict mapping field names to channels if the user explicitly specified axis placement (e.g. {"price": "x"}), otherwise {}

## User Prompt

USER REQUEST: Compare the seasonal patterns of temperature and precipitation in Seattle — are wet months also cold months?

DATASET SCHEMA:
- date: string (unique=1461)
- precipitation: number (unique=111, min=0.0, max=55.9)
- temp_max: number (unique=67, min=-1.6, max=35.6)
- temp_min: number (unique=55, min=-7.1, max=18.3)
- wind: number (unique=79, min=0.4, max=9.5)
- weather: string (unique=5)

SAMPLE ROWS:
      date  precipitation  temp_max  temp_min  wind weather
2012-01-01            0.0      12.8       5.0   4.7 drizzle
2012-01-02           10.9      10.6       2.8   4.5    rain
2012-01-03            0.8      11.7       7.2   2.3    rain

Select the fields most relevant to the user's request. Only include fields that should be encoded in the visualization. Return JSON.
