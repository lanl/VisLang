# LLM Call: 2b_baseline_spec

## System Prompt

You are generating a Vega-Lite visualization spec.

Design the best visualization for the user's request:
- Choose the most effective mark type for the data and task
- Assign fields to channels for maximum readability
- Use appropriate scales, aggregation, and binning
- Add a descriptive title and axis labels
- Include tooltips for interactive exploration
- Apply appropriate mark styling (filled marks, opacity for overplotting, etc.)
- Use a thoughtful color scheme
- Size the chart appropriately

Return a JSON object with exactly these keys:
- "vegalite_spec": the complete Vega-Lite specification (no $schema or data needed)
- "reasoning": brief explanation of your design choices

## User Prompt

USER REQUEST: Compare the seasonal patterns of temperature and precipitation in Seattle — are wet months also cold months?

DATA FILE: seattle-weather.csv

COLUMN SUMMARY:
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

Generate the best Vega-Lite spec for this request. Return JSON.
