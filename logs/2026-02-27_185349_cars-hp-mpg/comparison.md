# Comparison: Draco-guided vs Baseline

## Task
Show the relationship between horsepower and miles per gallon (cars.json)

## Mark Type
Both approaches chose **point** (scatterplot) — the standard and correct choice for showing the relationship between two continuous quantitative variables.

## Channel / Encoding Decisions
| Encoding | Draco | Baseline |
|----------|-------|----------|
| x | horsepower (quantitative) | horsepower (quantitative) |
| y | miles_per_Gallon (quantitative) | miles_per_Gallon (quantitative) |
| color | — | origin (nominal) |
| tooltip | — | name, horsepower, MPG, origin |

**Key difference**: The baseline adds a color encoding by `origin`, which reveals geographic clustering patterns (Japanese/European cars tend toward high MPG/low HP; American cars spread across higher HP with lower MPG). Draco's constraint-based approach produces a minimal 2-encoding chart focused solely on the requested relationship.

## Scale Choices
- **Draco**: Linear scales with `zero: true` on both axes. This is the conservative choice — it ensures the visual proportions are honest but wastes some plot area since the data doesn't extend near zero on the y-axis.
- **Baseline**: `zero: false` on both axes. This maximizes use of plot area and makes the correlation pattern more legible, but could slightly exaggerate the visual relationship.

## Mark Styling
- **Draco**: Default point marks (unfilled, standard size)
- **Baseline**: Filled points, opacity 0.7, size 60 — better handles the 406-point overplotting

## Draco Soft Constraint Violations (Cost: 12)
- `encoding`: 2 (one per encoding — inherent cost of using encodings)
- `encoding_field`: 2 (one per field binding)
- `linear_scale`: 2 (cost of using linear scales)
- `c_c_point`: 1 (continuous × continuous → point)
- `linear_x`: 1, `linear_y`: 1 (linear scale preferences)
- `summary_point`: 1, `cartesian_coordinate`: 1

All violations are "inherent" costs — there's no way to avoid them for this type of visualization. The low total cost (12) confirms Draco considers this an optimal design.

## Assessment
For the specific request ("show the relationship between HP and MPG"), both approaches are effective. The **baseline** provides a richer, more exploratory visualization by adding the origin color encoding, which reveals meaningful structure in the data. **Draco's** approach is more focused and principled — it answers exactly what was asked and nothing more, which is appropriate for a constraint-based recommender that only received two field bindings.
