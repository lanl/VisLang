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

USER REQUEST: Show how unemployment rates across industries changed during recessions vs expansions

DATA FILE: unemployment-across-industries.json

COLUMN SUMMARY:
- series: string (unique=14)
- year: number (unique=11, min=2000, max=2010)
- month: number (unique=12, min=1, max=12)
- count: number (unique=915, min=2, max=2440)
- rate: number (unique=147, min=0.30000000000000004, max=27.1)
- date: datetime (unique=122)

SAMPLE ROWS:
    series  year  month  count  rate                      date
Government  2000      1    430   2.1 2000-01-01 08:00:00+00:00
Government  2000      2    409   2.0 2000-02-01 08:00:00+00:00
Government  2000      3    311   1.5 2000-03-01 08:00:00+00:00

Generate the best Vega-Lite spec for this request. Return JSON.


DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "y",
      "field": "series"
    },
    {
      "channel": "x",
      "field": "date",
      "binning": 10
    },
    {
      "channel": "size",
      "field": "rate",
      "aggregate": "mean"
    }
  ],
  "scales": [
    {
      "channel": "y",
      "type": "ordinal"
    },
    {
      "channel": "x",
      "type": "linear"
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
