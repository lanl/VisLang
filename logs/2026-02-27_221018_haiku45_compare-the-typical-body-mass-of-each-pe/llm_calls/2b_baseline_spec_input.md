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
