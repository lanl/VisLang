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

USER REQUEST: Show the yield of different barley varieties across sites — which varieties perform best and does it depend on location?

DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "size",
      "field": "yield",
      "aggregate": "mean"
    },
    {
      "channel": "x",
      "field": "variety"
    },
    {
      "channel": "y",
      "field": "site"
    }
  ],
  "scales": [
    {
      "channel": "size",
      "type": "linear",
      "zero": "true"
    },
    {
      "channel": "x",
      "type": "ordinal"
    },
    {
      "channel": "y",
      "type": "ordinal"
    }
  ],
  "coordinates": "cartesian",
  "facets": [],
  "task": "summary",
  "cost": [
    26
  ],
  "violations": {
    "aggregate": 1,
    "encoding": 3,
    "encoding_field": 3,
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
  "title": "Barley Yield by Variety and Site",
  "description": "Average yield of different barley varieties across growing sites",
  "width": 500,
  "height": 300,
  "data": {
    "url": "barley.json"
  },
  "mark": {
    "type": "point",
    "filled": true,
    "opacity": 0.8
  },
  "encoding": {
    "x": {
      "field": "variety",
      "type": "ordinal",
      "title": "Barley Variety",
      "axis": {
        "labelAngle": 45,
        "labelAlign": "left"
      }
    },
    "y": {
      "field": "site",
      "type": "ordinal",
      "title": "Growing Site"
    },
    "size": {
      "field": "yield",
      "type": "quantitative",
      "aggregate": "mean",
      "title": "Average Yield",
      "scale": {
        "type": "linear",
        "zero": true,
        "range": [
          100,
          400
        ]
      }
    },
    "color": {
      "field": "yield",
      "type": "quantitative",
      "aggregate": "mean",
      "scale": {
        "scheme": "viridis"
      },
      "title": "Average Yield"
    },
    "tooltip": [
      {
        "field": "variety",
        "type": "nominal",
        "title": "Variety"
      },
      {
        "field": "site",
        "type": "nominal",
        "title": "Site"
      },
      {
        "field": "yield",
        "type": "quantitative",
        "aggregate": "mean",
        "title": "Average Yield",
        "format": ".2f"
      },
      {
        "field": "yield",
        "type": "quantitative",
        "aggregate": "count",
        "title": "Observations"
      }
    ]
  }
}

DRACO-INFORMED REASONING:
Following Draco's recommendations, I used point marks with size encoding for mean yield to create a bubble chart showing variety-site combinations. The x-axis displays barley varieties and y-axis shows growing sites, allowing direct comparison of how each variety performs across locations. Size and color both encode average yield for redundant encoding and better perception. This design effectively answers both questions: which varieties perform best (larger points) and whether location matters (visual clustering patterns by site). The tooltip provides exact values for interactive exploration. The ordinal scales for variety and site preserve the categorical nature of these dimensions, while the linear size scale with zero baseline follows Draco's specification.

BASELINE SPEC:
{
  "title": "Barley Yield by Variety and Site",
  "width": 900,
  "height": 500,
  "data": {
    "url": "barley.json"
  },
  "mark": {
    "type": "point",
    "filled": true,
    "opacity": 0.7,
    "size": 100
  },
  "encoding": {
    "x": {
      "field": "variety",
      "type": "nominal",
      "title": "Barley Variety",
      "axis": {
        "labelAngle": 45,
        "labelAlign": "left"
      }
    },
    "y": {
      "field": "yield",
      "type": "quantitative",
      "title": "Yield"
    },
    "color": {
      "field": "site",
      "type": "nominal",
      "title": "Site",
      "scale": {
        "scheme": "category10"
      }
    },
    "tooltip": [
      {
        "field": "variety",
        "type": "nominal",
        "title": "Variety"
      },
      {
        "field": "yield",
        "type": "quantitative",
        "title": "Yield",
        "format": ".2f"
      },
      {
        "field": "site",
        "type": "nominal",
        "title": "Site"
      },
      {
        "field": "year",
        "type": "ordinal",
        "title": "Year"
      }
    ]
  }
}

BASELINE REASONING:
This scatter plot design effectively shows the relationship between barley varieties and yield while revealing site-specific patterns. Key design choices: (1) Varieties on x-axis for easy comparison across varieties; (2) Yield on y-axis to directly show performance; (3) Color encoding by site to reveal location-dependent patterns—allowing viewers to see if certain varieties perform better at specific sites; (4) Point marks with opacity to handle overplotting from multiple years; (5) Tooltips for detailed inspection of individual data points. This layout makes it easy to identify top-performing varieties overall (those with higher y-values) and whether performance is location-dependent (by observing color clustering patterns).

Analyze both approaches. Pay special attention to whether the Draco-informed spec actually followed Draco's structural advice and whether differences between the two specs are attributable to Draco's contribution. Return JSON.
