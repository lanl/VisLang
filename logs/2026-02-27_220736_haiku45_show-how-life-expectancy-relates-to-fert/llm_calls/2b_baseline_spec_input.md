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

USER REQUEST: Show how life expectancy relates to fertility rate across countries, and whether income level explains the pattern

DATA FILE: gapminder-health-income.csv

COLUMN SUMMARY:
- country: string (unique=187)
- income: number (unique=187, min=599, max=132877)
- health: number (unique=140, min=48.5, max=84.1)
- population: number (unique=187, min=52993, max=1376048943)
- region: string (unique=6)

SAMPLE ROWS:
    country  income  health  population                   region
Afghanistan    1925   57.63    32526562               south_asia
    Albania   10620   76.00     2896679      europe_central_asia
    Algeria   13434   76.50    39666519 middle_east_north_africa

Generate the best Vega-Lite spec for this request. Return JSON.
