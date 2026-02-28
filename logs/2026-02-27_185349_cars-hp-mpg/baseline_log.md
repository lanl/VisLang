# Baseline Visualization Design Log (No Draco)

## User Request
Show the relationship between horsepower and miles per gallon.

## Data Summary
- **Dataset**: cars.json (406 rows, 9 columns)
- **Key fields**: `horsepower` (quantitative, range 46–230), `miles_per_Gallon` (quantitative, range 9–46.6)
- **Additional field used**: `origin` (nominal: USA, Europe, Japan)
- **Missing values**: 6 nulls in horsepower, 8 in miles_per_Gallon

## Design Choices

### Mark: Point (Scatter Plot)
A scatter plot is the most effective and standard chart type for revealing the
relationship between two continuous/quantitative variables. It directly encodes
each observation as a position in 2D space, making correlations, clusters, and
outliers immediately visible.

### X-axis: Horsepower
Horsepower is placed on the x-axis as the independent/predictor variable.
Convention in automotive analysis treats engine power as a design input that
influences fuel economy as an outcome.

### Y-axis: Miles per Gallon
Miles per gallon is placed on the y-axis as the dependent/outcome variable.
We expect a negative correlation: higher horsepower generally means lower fuel
efficiency.

### Color: Origin
Encoding origin (USA, Europe, Japan) as color reveals important group-level
structure. Japanese and European cars tend to cluster in the low-horsepower /
high-MPG region, while American cars spread across higher horsepower with lower
MPG. This additional encoding enriches the visualization without adding clutter.

### Scale: zero=false on both axes
Setting `zero: false` allows the axes to start near the data minimum rather
than at zero. This maximizes the use of plot area and makes the spread and
correlation more legible, which is appropriate for a scatter plot where the
absolute distance from zero is not the primary message.

### Opacity and Size
- `opacity: 0.7` — semi-transparency helps reveal overlapping points
  (overplotting) in a dataset of ~400 records.
- `size: 60` — moderately sized points for clear visibility without excessive
  overlap.

### Tooltips
Interactive tooltips (name, horsepower, MPG, origin) allow the viewer to
inspect individual data points on hover, supporting exploratory analysis.

### Dimensions
- `width: 500, height: 350` — a landscape aspect ratio suits the data ranges
  and provides comfortable reading space.

## Expected Pattern
A clear negative correlation: as horsepower increases, miles per gallon
decreases. The relationship appears roughly hyperbolic/curvilinear rather than
strictly linear. Color grouping by origin should show that Japanese cars
cluster in the high-MPG / low-HP region, European cars in a moderate range,
and American cars predominantly in the high-HP / low-MPG region.

## Validation
Spec passed Vega-Lite validation successfully.
