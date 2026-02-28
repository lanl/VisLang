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

USER REQUEST: How has the mix of energy sources in Iowa changed over time, and which sources are growing fastest?

DATA FILE: iowa-electricity.csv

COLUMN SUMMARY:
- year: string (unique=17)
- source: string (unique=3)
- net_generation: number (unique=51, min=1437, max=42750)

SAMPLE ROWS:
      year       source  net_generation
2001-01-01 Fossil Fuels           35361
2002-01-01 Fossil Fuels           35991
2003-01-01 Fossil Fuels           36234

Generate the best Vega-Lite spec for this request. Return JSON.


DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "x",
      "field": "year"
    },
    {
      "channel": "y",
      "field": "source"
    },
    {
      "channel": "size",
      "field": "net_generation"
    }
  ],
  "scales": [
    {
      "channel": "x",
      "type": "ordinal"
    },
    {
      "channel": "y",
      "type": "ordinal"
    },
    {
      "channel": "size",
      "type": "linear",
      "zero": "true"
    }
  ],
  "coordinates": "cartesian",
  "facets": [],
  "task": "summary",
  "cost": [
    25
  ],
  "violations": {
    "encoding": 3,
    "encoding_field": 3,
    "x_y_raw": 1,
    "linear_scale": 1,
    "ordinal_scale": 2,
    "d_d_point": 1,
    "linear_size": 1,
    "ordinal_x": 1,
    "ordinal_y": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

Honor Draco's structural choices above while designing the spec.
