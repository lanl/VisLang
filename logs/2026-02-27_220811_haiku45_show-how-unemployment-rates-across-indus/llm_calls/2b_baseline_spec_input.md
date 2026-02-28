# LLM Call: 2b_baseline_spec

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
