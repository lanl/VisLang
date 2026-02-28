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
