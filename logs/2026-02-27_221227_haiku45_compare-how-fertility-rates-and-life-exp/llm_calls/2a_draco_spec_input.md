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

USER REQUEST: Compare how fertility rates and life expectancy relate across world regions, and whether population size plays a role

DATA FILE: gapminder.json

COLUMN SUMMARY:
- year: number (unique=11, min=1955, max=2005)
- country: string (unique=62)
- cluster: number (unique=6, min=0, max=5)
- pop: number (unique=682, min=82656, max=1304887562)
- life_expect: number (unique=614, min=27.79, max=82.5)
- fertility: number (unique=398, min=0.96, max=8.23)

SAMPLE ROWS:
 year     country  cluster     pop  life_expect  fertility
 1955 Afghanistan        0 7971931        43.88       7.42
 1960 Afghanistan        0 8622466        45.03       7.38
 1965 Afghanistan        0 9565147        46.13       7.35

Generate the best Vega-Lite spec for this request. Return JSON.


DRACO RECOMMENDATIONS:
{}

Honor Draco's structural choices above while designing the spec.
