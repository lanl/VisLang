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

USER REQUEST: Show how the scale and frequency of natural disasters has changed over time across different disaster types

DATA FILE: disasters.csv

COLUMN SUMMARY:
- entity: string (unique=11)
- year: number (unique=117, min=1900, max=2017)
- deaths: number (unique=680, min=1, max=3706227)

SAMPLE ROWS:
               entity  year  deaths
All natural disasters  1900 1267360
All natural disasters  1901  200018
All natural disasters  1902   46037

Generate the best Vega-Lite spec for this request. Return JSON.


DRACO RECOMMENDATIONS:
{
  "mark_type": "line",
  "encodings": [
    {
      "channel": "x",
      "field": "year"
    },
    {
      "channel": "y",
      "field": "deaths"
    },
    {
      "channel": "color",
      "field": "entity"
    }
  ],
  "scales": [
    {
      "channel": "color",
      "type": "ordinal"
    },
    {
      "channel": "y",
      "type": "linear",
      "zero": "true"
    },
    {
      "channel": "x",
      "type": "linear"
    }
  ],
  "coordinates": "cartesian",
  "facets": [],
  "task": "summary",
  "cost": [
    50
  ],
  "violations": {
    "encoding": 3,
    "encoding_field": 3,
    "continuous_not_zero": 1,
    "continuous_pos_not_zero": 1,
    "linear_scale": 2,
    "ordinal_scale": 1,
    "c_c_line": 1,
    "linear_x": 1,
    "linear_y": 1,
    "ordinal_color": 1,
    "summary_line": 1,
    "cartesian_coordinate": 1
  }
}

Honor Draco's structural choices above while designing the spec.
