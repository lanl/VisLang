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

USER REQUEST: Show how life expectancy relates to fertility rate across countries, and whether income level explains the pattern

DATA FILE: gapminder-health-income.csv

COLUMN SUMMARY:
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

Generate the best Vega-Lite spec for this request. Return JSON.


DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "x",
      "field": "health",
      "binning": 10
    },
    {
      "channel": "size",
      "field": "income"
    },
    {
      "channel": "y",
      "field": "country"
    }
  ],
  "scales": [
    {
      "channel": "x",
      "type": "linear"
    },
    {
      "channel": "size",
      "type": "linear",
      "zero": "true"
    },
    {
      "channel": "y",
      "type": "ordinal"
    }
  ],
  "coordinates": "cartesian",
  "facets": [],
  "task": "summary",
  "cost": [
    34
  ],
  "violations": {
    "bin": 1,
    "encoding": 3,
    "encoding_field": 3,
    "x_y_raw": 1,
    "high_cardinality_ordinal": 1,
    "linear_scale": 2,
    "ordinal_scale": 1,
    "d_d_point": 1,
    "linear_x": 1,
    "linear_size": 1,
    "ordinal_y": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

Honor Draco's structural choices above while designing the spec.
