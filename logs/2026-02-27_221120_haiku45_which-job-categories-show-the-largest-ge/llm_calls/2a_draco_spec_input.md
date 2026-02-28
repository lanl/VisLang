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

USER REQUEST: Which job categories show the largest gender gap in employment share?

DATA FILE: jobs.json

COLUMN SUMMARY:
- job: string (unique=255)
- sex: string (unique=2)
- year: number (unique=15, min=1850, max=2000)
- count: number (unique=4415, min=0, max=11270779)
- perc: number (unique=4711, min=0.0, max=0.44687157453606)

SAMPLE ROWS:
                 job sex  year  count     perc
Accountant / Auditor men  1850    708 0.000131
Accountant / Auditor men  1860   1805 0.000214
Accountant / Auditor men  1870   1310 0.000100

Generate the best Vega-Lite spec for this request. Return JSON.


DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "y",
      "field": "job"
    },
    {
      "channel": "x",
      "field": "sex"
    },
    {
      "channel": "size",
      "field": "perc",
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
    36
  ],
  "violations": {
    "aggregate": 1,
    "encoding": 3,
    "encoding_field": 3,
    "high_cardinality_ordinal": 1,
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
