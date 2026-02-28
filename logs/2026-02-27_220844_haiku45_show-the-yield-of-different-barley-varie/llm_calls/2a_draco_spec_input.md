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

USER REQUEST: Show the yield of different barley varieties across sites — which varieties perform best and does it depend on location?

DATA FILE: barley.json

COLUMN SUMMARY:
- yield: number (unique=114, min=14.43333, max=65.7667)
- variety: string (unique=10)
- year: number (unique=2, min=1931, max=1932)
- site: string (unique=6)

SAMPLE ROWS:
   yield   variety  year            site
27.00000 Manchuria  1931 University Farm
48.86667 Manchuria  1931          Waseca
27.43334 Manchuria  1931          Morris

Generate the best Vega-Lite spec for this request. Return JSON.


DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "size",
      "field": "yield",
      "aggregate": "mean"
    },
    {
      "channel": "x",
      "field": "variety"
    },
    {
      "channel": "y",
      "field": "site"
    }
  ],
  "scales": [
    {
      "channel": "size",
      "type": "linear",
      "zero": "true"
    },
    {
      "channel": "x",
      "type": "ordinal"
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
    26
  ],
  "violations": {
    "aggregate": 1,
    "encoding": 3,
    "encoding_field": 3,
    "linear_scale": 1,
    "ordinal_scale": 2,
    "d_d_point": 1,
    "linear_size": 1,
    "ordinal_x": 1,
    "ordinal_y": 1,
    "aggregate_mean": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

Honor Draco's structural choices above while designing the spec.
