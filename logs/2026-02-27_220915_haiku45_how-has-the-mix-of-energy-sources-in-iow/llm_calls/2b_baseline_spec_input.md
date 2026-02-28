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
