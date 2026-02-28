# Comparison: Fuel Efficiency by Origin with Engine Size

## Task
"How does fuel efficiency vary across different origins, and does engine size play a role?"

## Structural Choices

### Mark Type
- **Draco-guided**: Point marks — used as a dot plot with size encoding
- **Baseline**: Circle marks — traditional scatterplot

Both chose point/circle marks, appropriate for showing relationships between variables.

### Channel Assignments
- **Draco-guided**: MPG→x (binned, 10 bins), origin→y (ordinal), displacement→size (mean aggregate). This is an unconventional but effective layout: a strip plot where each origin gets a row, MPG bins spread horizontally, and point size shows mean engine displacement within each bin.
- **Baseline**: displacement→x, MPG→y, origin→color. A classic scatterplot directly showing the displacement-MPG relationship with color-coded origins.

Key difference: Draco used the **size channel** for displacement (the third variable), creating a summary view. The baseline put displacement on a **positional axis**, preserving individual data points.

### Aggregation
- **Draco-guided**: Summary task — bins MPG and aggregates displacement to mean. Produces ~30 data points (3 origins × ~10 bins). More compact, easier to compare across origins.
- **Baseline**: No aggregation — shows all 406 individual data points. More detail but more overplotting.

### Scales
- **Draco-guided**: Linear x with zero baseline, ordinal y. The zero baseline on x is perceptually principled (prevents exaggerating differences) but wastes space since MPG ranges 9-46.
- **Baseline**: Quantitative x/y with zero=false on x. Better use of plot area for displacement's 68-455 range.

## Enhancements (Claude's additions)

### Draco-guided spec enhancements
- Color by origin (redundant encoding with y — aids quick visual parsing)
- Descriptive title and subtitle explaining the size encoding
- Tooltips with origin, MPG bin, mean displacement, and car count
- Filled points with slight opacity and thin stroke
- Bottom-oriented legends to maximize chart area

### Baseline spec enhancements
- Interactive legend selection (click to highlight an origin, dims others)
- Rich tooltips with car name, origin, MPG, displacement, horsepower, cylinders
- High-contrast color palette

## Assessment

**Draco-guided** excels at directly answering the user's question: you can immediately compare fuel efficiency distributions across origins and see how engine size (point size) varies within MPG ranges. The binned summary makes cross-origin comparison clean. Japan clearly clusters at higher MPG with smaller engines; USA clusters at lower MPG with larger engines.

**Baseline** excels at showing the raw displacement-MPG relationship: the negative correlation is visually obvious, and the interactive legend lets you isolate each origin's pattern. However, the question was about *origins* as the primary grouping, which the Draco layout handles more directly.

**Verdict**: The Draco-guided approach better addresses the specific question ("how does efficiency vary across origins") by putting origin on a positional axis and using aggregation for a cleaner comparison. The baseline is a better exploratory tool for understanding the raw data relationship. For this prompt, Draco's structural advice was the better match.

## Draco Cost Analysis
- Total cost: 25
- Key violations: aggregate (1), bin (1), 3 encodings with fields, linear scales on x and size, ordinal on y
- The relatively low cost suggests this is a well-supported design in Draco's constraint space
