# Baseline Visualization Log

## Task
How does fuel efficiency vary across different origins, and does engine size play a role?

## Data Summary
- **Dataset**: cars.json (406 rows, 9 columns)
- **Key columns**: miles_per_Gallon (quantitative), displacement (quantitative), origin (nominal: USA, Europe, Japan)
- **Null values**: 8 missing MPG values, 6 missing horsepower values

## Design Reasoning

### Analytical Goal
The user wants to understand two things:
1. How fuel efficiency (MPG) differs across origins (USA, Europe, Japan)
2. Whether engine size (displacement) plays a role in that variation

This requires showing the relationship between three variables: one quantitative (MPG), one quantitative (displacement), and one categorical (origin).

### Mark Type: Circle (Scatter Plot)
A scatter plot is the natural choice here because:
- It shows the **correlation** between two quantitative variables (displacement vs MPG)
- It reveals the **distribution** of each origin within that space
- It preserves individual data points, allowing the viewer to see density, outliers, and clusters
- Alternative considered: box plots (grouped by origin) would show distribution of MPG by origin but would hide the displacement relationship entirely

### Encoding Choices
| Channel | Field | Type | Rationale |
|---------|-------|------|-----------|
| x-axis | displacement | quantitative | Engine size as the independent/explanatory variable |
| y-axis | miles_per_Gallon | quantitative | Fuel efficiency as the dependent/outcome variable |
| color | origin | nominal | Distinguishes the three origins with a categorical palette |
| opacity | (interactive) | - | Legend-click selection dims non-selected origins |
| tooltip | multiple fields | - | Hover reveals car name, all key stats |

### Color Palette
Used ColorBrewer qualitative palette (red/blue/green) for maximum discriminability across three categories. The colors are accessible and widely recognized in visualization.

### Interactivity
- **Legend selection**: Clicking an origin in the legend highlights only those points, dimming others to 10% opacity. This allows the user to isolate each origin's pattern.
- **Tooltips**: Rich tooltips show car name, origin, MPG, displacement, horsepower, and cylinders for detailed inspection.

### Scale Decisions
- x-axis: `zero: false` to better spread the data across the plot area (displacement ranges from 68 to 455, so starting at 0 wastes space)
- y-axis: default (starts at 0) to accurately represent MPG magnitudes

### Expected Insights
The visualization should clearly show:
- **Negative correlation**: Larger engines (higher displacement) tend to have lower MPG
- **Origin clustering**: Japanese and European cars cluster at low displacement / high MPG; USA cars spread across higher displacements with lower MPG
- **Overlap regions**: Some overlap between origins at moderate displacement values

## Validation
- Spec passed Vega-Lite validation on first attempt
- No retries needed
