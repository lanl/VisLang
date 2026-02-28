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
