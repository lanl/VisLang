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

USER REQUEST: How does the age distribution of the US population differ between males and females?

DATA FILE: population.json

COLUMN SUMMARY:
- year: number (unique=15, min=1850, max=2000)
- age: number (unique=19, min=0, max=90)
- sex: number (unique=2, min=1, max=2)
- people: number (unique=570, min=5259, max=11635647)

SAMPLE ROWS:
 year  age  sex  people
 1850    0    1 1483789
 1850    0    2 1450376
 1850    5    1 1411067

Generate the best Vega-Lite spec for this request. Return JSON.


DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "x",
      "field": "age"
    },
    {
      "channel": "y",
      "field": "sex"
    },
    {
      "channel": "size",
      "field": "people"
    }
  ],
  "scales": [
    {
      "channel": "x",
      "type": "linear",
      "zero": "true"
    },
    {
      "channel": "y",
      "type": "linear",
      "zero": "true"
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
    19
  ],
  "violations": {
    "encoding": 3,
    "encoding_field": 3,
    "linear_scale": 3,
    "c_c_point": 1,
    "linear_x": 1,
    "linear_y": 1,
    "linear_size": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

Honor Draco's structural choices above while designing the spec.
