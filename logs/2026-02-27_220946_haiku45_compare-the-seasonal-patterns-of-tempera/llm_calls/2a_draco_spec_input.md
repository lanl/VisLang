# LLM Call: 2a_draco_spec

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


ADDITIONAL GUIDANCE — Draco Recommendations:

You have been provided with structural recommendations from Draco 2, a constraint-based visualization recommender that optimizes for perceptual effectiveness. You MUST honor these structural decisions:
- Mark type: use the recommended mark type
- Channel assignments: use the recommended field→channel mappings
- Scale types: use the recommended scale types
- Aggregation/binning: apply if recommended

You may add polish on top of these structural choices, but do not contradict them.

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


DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "color",
      "field": "date"
    },
    {
      "channel": "size",
      "field": "precipitation"
    },
    {
      "channel": "x",
      "field": "temp_max"
    },
    {
      "channel": "y",
      "field": "temp_min"
    }
  ],
  "scales": [
    {
      "channel": "color",
      "type": "ordinal"
    },
    {
      "channel": "size",
      "type": "linear",
      "zero": "true"
    },
    {
      "channel": "x",
      "type": "linear"
    },
    {
      "channel": "y",
      "type": "linear"
    }
  ],
  "coordinates": "cartesian",
  "facets": [],
  "task": "summary",
  "cost": [
    52
  ],
  "violations": {
    "encoding": 4,
    "encoding_field": 4,
    "multi_non_pos": 1,
    "continuous_not_zero": 2,
    "continuous_pos_not_zero": 2,
    "high_cardinality_ordinal": 1,
    "linear_scale": 3,
    "ordinal_scale": 1,
    "c_c_point": 1,
    "linear_x": 1,
    "linear_y": 1,
    "linear_size": 1,
    "ordinal_color": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

Honor Draco's structural choices above while designing the spec.
