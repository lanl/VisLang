# Draco-Enhanced Visualization Log

## User Request
How does fuel efficiency vary across different origins, and does engine size play a role?

## Data
- **File**: vega-datasets/data/cars.json
- **Records**: 406 cars, 398 with non-null MPG values
- **Key columns**: miles_per_Gallon (float), origin (str: USA/Europe/Japan), displacement (float, cu. in.)

## Draco Structural Recommendations (Honored)

| Decision | Draco Recommendation | Implementation |
|----------|---------------------|----------------|
| Mark type | point | `"type": "point"` with filled=true, opacity=0.85, thin stroke |
| X channel | miles_per_Gallon, binned (10 bins) | `"bin": {"maxbins": 10}`, quantitative, linear scale with zero=true |
| Y channel | origin, ordinal | Ordinal type, sorted USA/Europe/Japan |
| Size channel | displacement, mean aggregate | Mean aggregate, linear scale with zero=true, range [40, 900] |
| Scales | x=linear(zero), y=ordinal, size=linear(zero) | All honored exactly |
| Task | summary | Aggregation via mean displacement and binning of MPG |
| Coordinates | cartesian | Default cartesian (no polar/radial) |
| Cost | 25 | Accepted as optimal |

## Enhancements Added (Beyond Draco)

1. **Color by origin**: Added nominal color encoding (red=USA, blue=Europe, green=Japan) for immediate visual distinction between origin categories. This does not conflict with Draco's channel assignments — it supplements them.

2. **Title and subtitle**: Descriptive title poses the user's question; subtitle explains the size encoding to aid interpretation.

3. **Tooltips**: Four-field tooltip showing origin, MPG bin, mean displacement (1 decimal), and car count per bin. Enables interactive data exploration.

4. **Mark styling**: Filled points with 0.85 opacity and thin dark stroke for definition. This helps distinguish overlapping points.

5. **Axis labels**: Renamed axes from raw column names to human-readable labels ("Miles per Gallon (binned)", "Origin").

6. **Legend placement**: Both color and size legends placed at bottom, horizontal orientation, to maximize chart area.

7. **Origin sort order**: Explicitly sorted USA, Europe, Japan for consistent ordering.

8. **Sizing**: 520×200 px — wide enough for the binned MPG range, compact height since there are only 3 origin categories.

9. **Config refinements**: Transparent view stroke, subtle grid lines (0.3 opacity), consistent font sizing.

## Design Rationale

Draco's recommendation of a binned scatterplot with size encoding is well-suited to this question:
- **Binned MPG on x** creates a natural histogram-like structure showing the distribution of fuel efficiency per origin.
- **Origin on y** separates the three categories clearly, making cross-origin comparison straightforward.
- **Mean displacement as size** encodes engine size within each bin, revealing the relationship: larger points (bigger engines) tend to cluster at lower MPG values, especially for USA cars.
- **Point marks** are appropriate for the combination of a continuous binned axis and a categorical axis, allowing size variation to be clearly perceived.

The color enhancement adds redundant encoding of origin, which is a recognized best practice for accessibility and rapid visual parsing.

## Validation
- Spec passed Vega-Lite v5 schema validation on first attempt.
