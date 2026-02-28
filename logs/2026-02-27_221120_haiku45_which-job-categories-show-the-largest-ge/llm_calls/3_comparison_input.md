# LLM Call: 3_comparison

## System Prompt

You are a visualization analyst comparing two Vega-Lite specs for the same task: one informed by Draco's constraint-based recommendations, one designed by an LLM without Draco.

You receive the Vega-Lite specs, reasoning text, Draco's recommendations, and rendered images of both visualizations. Use the images to assess the actual visual output — layout, readability, color effectiveness, overplotting, axis formatting, and overall clarity — not just the spec structure.

When evaluating, only make judgements about the relative quality of the two specs that you are highly confident in. Limit your discussion of differences that are
not very signficiant.

Use the Draco recommendations to assess:
- Which structural choices came from Draco vs. were added/changed by the LLM
- Whether the Draco-informed spec actually followed Draco's advice
- Whether any deviations from Draco's recommendations were justified
- Whether any difference in quality between the two specs can be attributed to Draco's contribution, or whether it was due to the LLM making different choices independently of Draco's input. Make sure to mention this point in your final evaluation.

Return a JSON object with exactly these keys:
- "comparison_markdown": detailed markdown analysis covering mark type choices, channel/encoding decisions, scale choices, where the Draco-informed spec followed or deviated from Draco's advice, enhancements added by each approach, and which better serves the user's intent
- "summary": 2-3 sentence summary of the key differences
- "verdict": which approach better serves the user's intent ("draco", "baseline", or "tie") with brief justification

## User Prompt

USER REQUEST: Which job categories show the largest gender gap in employment share?

DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "y",
      "field": "job"
    },
    {
      "channel": "x",
      "field": "sex"
    },
    {
      "channel": "size",
      "field": "perc",
      "aggregate": "mean"
    }
  ],
  "scales": [
    {
      "channel": "y",
      "type": "ordinal"
    },
    {
      "channel": "x",
      "type": "ordinal"
    },
    {
      "channel": "size",
      "type": "linear",
      "zero": "true"
    }
  ],
  "coordinates": "cartesian",
  "facets": [],
  "task": "summary",
  "cost": [
    36
  ],
  "violations": {
    "aggregate": 1,
    "encoding": 3,
    "encoding_field": 3,
    "high_cardinality_ordinal": 1,
    "linear_scale": 1,
    "ordinal_scale": 2,
    "d_d_point": 1,
    "linear_size": 1,
    "ordinal_x": 1,
    "ordinal_y": 1,
    "aggregate_mean": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

DRACO-INFORMED SPEC:
{
  "title": "Gender Gap in Employment Share by Job Category",
  "description": "Comparing average employment share between men and women across job categories to identify largest gender gaps",
  "width": 800,
  "height": 600,
  "data": {
    "url": "jobs.json"
  },
  "transform": [
    {
      "aggregate": [
        {
          "op": "mean",
          "field": "perc",
          "as": "avg_perc"
        }
      ],
      "groupby": [
        "job",
        "sex"
      ]
    },
    {
      "pivot": "sex",
      "value": "avg_perc",
      "groupby": [
        "job"
      ]
    },
    {
      "calculate": "abs(datum.men - datum.women)",
      "as": "gender_gap"
    },
    {
      "window": [
        {
          "op": "rank",
          "as": "rank"
        }
      ],
      "sort": [
        {
          "field": "gender_gap",
          "order": "descending"
        }
      ]
    },
    {
      "filter": "datum.rank <= 30"
    },
    {
      "fold": [
        "men",
        "women"
      ],
      "as": [
        "sex",
        "perc"
      ]
    }
  ],
  "mark": {
    "type": "point",
    "filled": true,
    "opacity": 0.8
  },
  "encoding": {
    "y": {
      "field": "job",
      "type": "ordinal",
      "sort": {
        "field": "gender_gap",
        "order": "descending"
      },
      "title": "Job Category",
      "axis": {
        "labelFontSize": 11
      }
    },
    "x": {
      "field": "sex",
      "type": "ordinal",
      "title": "Gender",
      "axis": {
        "labelFontSize": 12
      }
    },
    "size": {
      "field": "perc",
      "type": "quantitative",
      "scale": {
        "zero": true,
        "range": [
          100,
          400
        ]
      },
      "title": "Avg Employment Share"
    },
    "color": {
      "field": "sex",
      "type": "nominal",
      "scale": {
        "domain": [
          "men",
          "women"
        ],
        "range": [
          "#1f77b4",
          "#ff7f0e"
        ]
      },
      "title": "Gender"
    },
    "tooltip": [
      {
        "field": "job",
        "type": "nominal",
        "title": "Job Category"
      },
      {
        "field": "sex",
        "type": "nominal",
        "title": "Gender"
      },
      {
        "field": "perc",
        "type": "quantitative",
        "title": "Avg Employment Share",
        "format": ".2%"
      },
      {
        "field": "gender_gap",
        "type": "quantitative",
        "title": "Gender Gap",
        "format": ".2%"
      }
    ]
  }
}

DRACO-INFORMED REASONING:
Fixed the scale type error by changing 'linear' to 'quantitative' in the size encoding's type field. The 'type' property in Vega-Lite encodings must use valid data types (quantitative, ordinal, temporal, nominal, geojson), not scale types. The scale object itself correctly specifies the scale type through its properties. The visualization maintains Draco's structural recommendations: point marks with job on y-axis, gender on x-axis, and size encoding average employment share. The transform pipeline calculates gender gaps and filters to the top 30 categories with largest disparities, sorted for easy comparison.

BASELINE SPEC:
{
  "title": "Gender Gap in Employment Share by Job Category",
  "description": "Largest differences in employment percentage between men and women across job categories",
  "width": 900,
  "height": 600,
  "data": {
    "url": "jobs.json"
  },
  "transform": [
    {
      "filter": "datum.year == 2000"
    },
    {
      "pivot": "sex",
      "value": "perc",
      "groupby": [
        "job"
      ]
    },
    {
      "calculate": "abs(datum.men - datum.women)",
      "as": "gender_gap"
    },
    {
      "calculate": "datum.men > datum.women ? datum.men - datum.women : datum.women - datum.men",
      "as": "gap_signed"
    },
    {
      "calculate": "datum.men > datum.women ? 'Male-dominated' : 'Female-dominated'",
      "as": "gap_direction"
    },
    {
      "window": [
        {
          "op": "rank",
          "as": "rank"
        }
      ],
      "sort": [
        {
          "field": "gender_gap",
          "order": "descending"
        }
      ]
    },
    {
      "filter": "datum.rank <= 20"
    }
  ],
  "mark": {
    "type": "bar",
    "tooltip": true
  },
  "encoding": {
    "y": {
      "field": "job",
      "type": "nominal",
      "sort": {
        "field": "gender_gap",
        "order": "descending"
      },
      "title": "Job Category",
      "axis": {
        "labelLimit": 200
      }
    },
    "x": {
      "field": "gender_gap",
      "type": "quantitative",
      "title": "Gender Gap (Absolute Difference in Employment Share)",
      "axis": {
        "format": ".1%"
      }
    },
    "color": {
      "field": "gap_direction",
      "type": "nominal",
      "title": "Gap Direction",
      "scale": {
        "domain": [
          "Male-dominated",
          "Female-dominated"
        ],
        "range": [
          "#1f77b4",
          "#ff7f0e"
        ]
      }
    },
    "tooltip": [
      {
        "field": "job",
        "type": "nominal",
        "title": "Job Category"
      },
      {
        "field": "gender_gap",
        "type": "quantitative",
        "title": "Gender Gap",
        "format": ".2%"
      },
      {
        "field": "men",
        "type": "quantitative",
        "title": "Men's Share",
        "format": ".2%"
      },
      {
        "field": "women",
        "type": "quantitative",
        "title": "Women's Share",
        "format": ".2%"
      },
      {
        "field": "gap_direction",
        "type": "nominal",
        "title": "Direction"
      }
    ]
  }
}

BASELINE REASONING:
This visualization effectively identifies the largest gender gaps in employment by: (1) Using the most recent year (2000) to show current state; (2) Pivoting the data to calculate the absolute difference between male and female employment shares; (3) Displaying the top 20 job categories with largest gaps as a horizontal bar chart for easy comparison; (4) Color-coding by gap direction (male vs female-dominated) to show which gender dominates each field; (5) Sorting bars by gap size for immediate visual ranking; (6) Including comprehensive tooltips showing both the gap and individual percentages for context. The horizontal bar layout accommodates long job titles and makes reading easier than vertical bars.

Analyze both approaches. Pay special attention to whether the Draco-informed spec actually followed Draco's structural advice and whether differences between the two specs are attributable to Draco's contribution. Return JSON.
