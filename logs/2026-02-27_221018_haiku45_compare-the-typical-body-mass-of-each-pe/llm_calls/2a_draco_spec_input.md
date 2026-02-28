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

USER REQUEST: Compare the typical body mass of each penguin species and whether flipper length varies with it

DATA FILE: penguins.json

COLUMN SUMMARY:
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

Generate the best Vega-Lite spec for this request. Return JSON.


DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "y",
      "field": "species"
    },
    {
      "channel": "size",
      "field": "body_Mass_g",
      "aggregate": "mean"
    },
    {
      "channel": "x",
      "field": "flipper_Length_mm",
      "binning": 10
    }
  ],
  "scales": [
    {
      "channel": "y",
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
    }
  ],
  "coordinates": "cartesian",
  "facets": [],
  "task": "summary",
  "cost": [
    25
  ],
  "violations": {
    "aggregate": 1,
    "bin": 1,
    "encoding": 3,
    "encoding_field": 3,
    "linear_scale": 2,
    "ordinal_scale": 1,
    "d_d_point": 1,
    "linear_x": 1,
    "linear_size": 1,
    "ordinal_y": 1,
    "aggregate_mean": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

Honor Draco's structural choices above while designing the spec.
