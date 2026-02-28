## Detailed Analysis of Draco-Informed vs. Baseline Approaches

### Mark Type and Overall Design Philosophy

The most fundamental difference between these two approaches is the mark type choice: **Draco recommended point marks**, which the Draco-informed spec followed, while the **baseline uses bar marks**. This is a critical divergence that significantly impacts how effectively each visualization answers the user's question about the largest gender gaps.

**Draco-informed approach (point marks):**
- Uses a scatterplot-like layout with jobs on the y-axis and gender categories (men/women) on the x-axis
- Encodes employment share magnitude through point size
- Shows 30 job categories with the largest gender gaps
- Creates a symmetric, side-by-side comparison structure

**Baseline approach (bar marks):**
- Uses a horizontal bar chart with jobs on the y-axis and gender gap magnitude on the x-axis
- Directly visualizes the gap size as bar length
- Shows 20 job categories with the largest gender gaps
- Color-codes bars by gap direction (male-dominated vs. female-dominated)

### Effectiveness for the User's Task

The user's primary question is: **"Which job categories show the largest gender gap in employment share?"** This is fundamentally about identifying and comparing gap magnitudes across job categories.

**Draco-informed visualization issues:**
1. **Indirect gap representation**: The visualization shows individual employment shares for men and women separately (via point size), but the actual gender gap is not directly encoded. Users must mentally calculate the difference between the two point sizes for each job category.
2. **Difficult comparison**: Comparing point sizes across different jobs and genders is cognitively demanding. The human visual system is poor at comparing areas (which size encodes), especially when points are not aligned on a common baseline.
3. **Redundant encoding**: Both size and color encode gender information, which is redundant and uses valuable encoding channels.
4. **Sorting clarity**: While jobs are sorted by gender_gap (descending), the visualization doesn't make the gap magnitude visually obvious at a glance.

**Baseline visualization strengths:**
1. **Direct gap representation**: Bar length directly encodes the gender gap magnitude, making it immediately clear which jobs have the largest disparities.
2. **Easy comparison**: Horizontal bars with a common baseline (x=0) leverage the human visual system's strength in comparing lengths. Users can instantly see which bars are longest.
3. **Gap direction clarity**: Color-coding (blue for male-dominated, orange for female-dominated) adds semantic meaning and helps users understand the nature of each gap.
4. **Efficient information density**: The visualization directly answers the question without requiring mental calculations.

### Data Aggregation Differences

**Draco-informed spec:**
- Aggregates data using mean employment share across all years
- Includes 30 job categories
- Transforms data through pivot, fold operations to show both men and women values

**Baseline spec:**
- Filters to year 2000 (most recent in dataset)
- Includes 20 job categories
- Calculates both absolute and signed gaps, plus gap direction

The baseline's choice to use a single year (2000) is more defensible for answering "which jobs show the largest gaps" because it provides a snapshot of current state rather than averaging across time periods. The Draco-informed approach's averaging could obscure temporal trends and may not represent the most recent employment landscape.

### Visual Readability

**Draco-informed visualization:**
- Job labels are readable but the layout is vertically compressed with 30 categories
- The two-column layout (men vs. women) requires horizontal scanning
- Point sizes vary but the size scale legend shows ranges (0.00 to 0.12) that are difficult to interpret without context
- The visualization is cluttered with many small points that lack visual hierarchy

**Baseline visualization:**
- Job labels are clearly readable with 20 categories
- The horizontal bar layout naturally accommodates long job titles
- Bar lengths create a clear visual hierarchy, with the longest bars immediately standing out
- The color distinction between male-dominated and female-dominated jobs adds visual interest and semantic clarity
- The x-axis clearly shows percentage values, making the scale interpretable

### Adherence to Draco Recommendations

The Draco-informed spec **does follow Draco's structural recommendations**:
- Uses point marks as recommended
- Places job on y-axis and sex on x-axis as recommended
- Encodes employment share in size channel as recommended
- Uses ordinal scales for categorical dimensions as recommended
- Maintains cartesian coordinates as recommended

However, **Draco's recommendations appear poorly suited to this task**. Draco's cost score of 36 and numerous violations (11 different violation types) suggest the recommendation itself may not be optimal. The point mark with size encoding is a poor choice for directly comparing gap magnitudes.

### Enhancements and Design Decisions

**Draco-informed spec enhancements:**
- Calculates gender_gap explicitly and sorts by it
- Includes comprehensive tooltips with both individual shares and gap magnitude
- Adds opacity and fill styling for visual polish
- Filters to top 30 categories (more comprehensive)

**Baseline spec enhancements:**
- Calculates gap_direction to enable semantic color-coding
- Filters to top 20 categories (more focused)
- Uses year filter for temporal specificity
- Includes both absolute and signed gap calculations
- Provides context in tooltips showing individual percentages

### Scale and Encoding Choices

**Draco-informed:**
- Size scale: linear with zero=true, range [100, 400]
- This creates a proportional relationship but makes small differences hard to distinguish
- The zero baseline for size is unusual and may not be appropriate for employment percentages

**Baseline:**
- X-axis: quantitative scale showing absolute gender gap percentage
- This is straightforward and directly interpretable
- The scale naturally starts at 0, making bar lengths directly comparable

### Attribution of Differences

The key question is: **Are the quality differences due to Draco's recommendations or independent LLM choices?**

1. **Mark type (point vs. bar)**: This is directly from Draco's recommendation. Draco recommended point marks; the baseline chose bars independently. This is the most significant difference and directly impacts task effectiveness.

2. **Data aggregation (mean vs. year 2000)**: The baseline's choice to use year 2000 is NOT from Draco's recommendations. Draco doesn't specify temporal filtering. This is an independent LLM design choice that improves clarity.

3. **Gap direction encoding**: The baseline's gap_direction field and color-coding is NOT from Draco. This is an independent enhancement that adds semantic value.

4. **Number of categories (30 vs. 20)**: The Draco-informed spec chose 30; the baseline chose 20. Neither is explicitly recommended by Draco. The baseline's choice is more focused and readable.

5. **Tooltip design**: Both include comprehensive tooltips, but the baseline's inclusion of individual percentages alongside the gap is a thoughtful independent choice.

### Conclusion on Quality

The baseline visualization is **substantially more effective** at answering the user's question. The bar chart with direct gap encoding is the appropriate choice for comparing magnitudes across categories. The Draco recommendation for point marks with size encoding is poorly suited to this task, despite the Draco-informed spec's careful implementation of that recommendation. The baseline's independent design choices (year filtering, gap direction encoding, focused category count) further improve its effectiveness.

The quality difference is primarily attributable to **Draco's recommendation being unsuitable for this task**, not to implementation quality. The Draco-informed spec faithfully implements a suboptimal recommendation, while the baseline makes independent choices that better serve the user's intent.