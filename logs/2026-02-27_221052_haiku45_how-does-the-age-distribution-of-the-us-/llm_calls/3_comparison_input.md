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

USER REQUEST: How does the age distribution of the US population differ between males and females?

DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "x",
      "field": "age"
    },
    {
      "channel": "y",
      "field": "sex"
    },
    {
      "channel": "size",
      "field": "people"
    }
  ],
  "scales": [
    {
      "channel": "x",
      "type": "linear",
      "zero": "true"
    },
    {
      "channel": "y",
      "type": "linear",
      "zero": "true"
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
    19
  ],
  "violations": {
    "encoding": 3,
    "encoding_field": 3,
    "linear_scale": 3,
    "c_c_point": 1,
    "linear_x": 1,
    "linear_y": 1,
    "linear_size": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

DRACO-INFORMED SPEC:
{
  "title": "US Population Age Distribution by Sex",
  "width": 800,
  "height": 300,
  "data": {
    "url": "population.json"
  },
  "transform": [
    {
      "filter": "datum.year == 2000"
    },
    {
      "calculate": "datum.sex == 1 ? 'Male' : 'Female'",
      "as": "sex_label"
    }
  ],
  "mark": {
    "type": "point",
    "filled": true,
    "opacity": 0.7
  },
  "encoding": {
    "x": {
      "field": "age",
      "type": "quantitative",
      "scale": {
        "type": "linear",
        "zero": true
      },
      "axis": {
        "title": "Age (years)",
        "labelAngle": 0
      }
    },
    "y": {
      "field": "sex_label",
      "type": "nominal",
      "scale": {
        "type": "point",
        "zero": false
      },
      "axis": {
        "title": "Sex"
      }
    },
    "size": {
      "field": "people",
      "type": "quantitative",
      "scale": {
        "type": "linear",
        "zero": true
      },
      "title": "Population"
    },
    "color": {
      "field": "sex_label",
      "type": "nominal",
      "scale": {
        "domain": [
          "Male",
          "Female"
        ],
        "range": [
          "#1f77b4",
          "#ff7f0e"
        ]
      },
      "legend": {
        "title": "Sex"
      }
    },
    "tooltip": [
      {
        "field": "age",
        "type": "quantitative",
        "title": "Age"
      },
      {
        "field": "sex_label",
        "type": "nominal",
        "title": "Sex"
      },
      {
        "field": "people",
        "type": "quantitative",
        "title": "Population",
        "format": ","
      }
    ]
  }
}

DRACO-INFORMED REASONING:
Following Draco's recommendations, I used point marks with age on the x-axis and sex on the y-axis, with population encoded as point size. To make this visualization more effective for comparing age distributions between sexes, I: (1) filtered to the most recent year (2000) to show current distribution; (2) converted numeric sex codes to readable labels ('Male'/'Female'); (3) added color encoding to reinforce the sex distinction and improve visual separation; (4) used a linear scale for age with zero baseline as recommended; (5) applied opacity and filled marks to handle potential overplotting; (6) included comprehensive tooltips for interactive exploration. The bubble chart effectively shows how population varies by age and sex, making it easy to spot differences in age distribution patterns between males and females.

BASELINE SPEC:
{
  "title": "US Population Age Distribution by Sex",
  "width": 800,
  "height": 500,
  "data": {
    "url": "population.json"
  },
  "transform": [
    {
      "filter": "datum.year == 2000"
    },
    {
      "calculate": "datum.sex == 1 ? 'Male' : 'Female'",
      "as": "sex_label"
    },
    {
      "calculate": "datum.sex == 1 ? datum.people : -datum.people",
      "as": "people_signed"
    }
  ],
  "mark": "bar",
  "encoding": {
    "y": {
      "field": "age",
      "type": "ordinal",
      "title": "Age",
      "sort": "ascending"
    },
    "x": {
      "field": "people_signed",
      "type": "quantitative",
      "title": "Population",
      "axis": {
        "format": "~s",
        "labelExpr": "abs(datum.value) > 0 ? format(abs(datum.value), '~s') : '0'"
      }
    },
    "color": {
      "field": "sex_label",
      "type": "nominal",
      "title": "Sex",
      "scale": {
        "domain": [
          "Male",
          "Female"
        ],
        "range": [
          "#1f77b4",
          "#ff7f0e"
        ]
      }
    },
    "tooltip": [
      {
        "field": "age",
        "type": "ordinal",
        "title": "Age"
      },
      {
        "field": "sex_label",
        "type": "nominal",
        "title": "Sex"
      },
      {
        "field": "people",
        "type": "quantitative",
        "title": "Population",
        "format": ","
      }
    ]
  },
  "config": {
    "mark": {
      "opacity": 0.8
    }
  }
}

BASELINE REASONING:
A population pyramid is the most effective visualization for comparing age distributions between sexes. I used a diverging bar chart where males extend left (negative values) and females extend right (positive values) from a central axis, making it easy to compare populations at each age level. The 2000 data was selected as the most recent year to show current distribution. The ordinal y-axis displays ages in ascending order, and the quantitative x-axis uses signed values to create the pyramid effect. Color distinguishes between sexes, and tooltips provide exact population counts for interactive exploration. The axis labels use abbreviated format (e.g., '1M' for 1 million) for readability.

Analyze both approaches. Pay special attention to whether the Draco-informed spec actually followed Draco's structural advice and whether differences between the two specs are attributable to Draco's contribution. Return JSON.
