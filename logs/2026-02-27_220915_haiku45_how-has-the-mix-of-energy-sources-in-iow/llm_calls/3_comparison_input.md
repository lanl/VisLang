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

USER REQUEST: How has the mix of energy sources in Iowa changed over time, and which sources are growing fastest?

DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "x",
      "field": "year"
    },
    {
      "channel": "y",
      "field": "source"
    },
    {
      "channel": "size",
      "field": "net_generation"
    }
  ],
  "scales": [
    {
      "channel": "x",
      "type": "ordinal"
    },
    {
      "channel": "y",
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
    25
  ],
  "violations": {
    "encoding": 3,
    "encoding_field": 3,
    "x_y_raw": 1,
    "linear_scale": 1,
    "ordinal_scale": 2,
    "d_d_point": 1,
    "linear_size": 1,
    "ordinal_x": 1,
    "ordinal_y": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

DRACO-INFORMED SPEC:
{
  "title": "Iowa Electricity Generation Mix Over Time",
  "description": "Net generation by source showing growth trends and composition changes",
  "width": 900,
  "height": 300,
  "data": {
    "url": "iowa-electricity.csv"
  },
  "mark": {
    "type": "point",
    "filled": true,
    "opacity": 0.8
  },
  "encoding": {
    "x": {
      "field": "year",
      "type": "ordinal",
      "title": "Year",
      "axis": {
        "labelAngle": -45,
        "labelOverlap": false
      }
    },
    "y": {
      "field": "source",
      "type": "ordinal",
      "title": "Energy Source"
    },
    "size": {
      "field": "net_generation",
      "type": "quantitative",
      "title": "Net Generation (GWh)",
      "scale": {
        "type": "linear",
        "zero": true,
        "range": [
          100,
          1000
        ]
      }
    },
    "color": {
      "field": "source",
      "type": "nominal",
      "scale": {
        "scheme": "tableau10"
      },
      "legend": {
        "title": "Energy Source"
      }
    },
    "tooltip": [
      {
        "field": "year",
        "type": "ordinal",
        "title": "Year"
      },
      {
        "field": "source",
        "type": "nominal",
        "title": "Source"
      },
      {
        "field": "net_generation",
        "type": "quantitative",
        "title": "Net Generation (GWh)",
        "format": ",.0f"
      }
    ]
  }
}

DRACO-INFORMED REASONING:
Following Draco's recommendations, I used point marks with size encoding to show net generation values across years and sources. The ordinal scales for both x (year) and y (source) create a clear grid layout where bubble size directly represents generation volume, making it easy to spot growth trends and compare sources. Color is added to reinforce source identity and improve visual distinction. The size scale is linear with zero baseline as recommended, allowing accurate visual comparison of generation amounts. Tooltips provide precise values for interactive exploration. This design effectively shows both the composition changes over time and which sources are growing fastest through the expanding bubble sizes.

BASELINE SPEC:
{
  "title": "Iowa Electricity Generation Mix Over Time",
  "width": 900,
  "height": 500,
  "data": {
    "url": "iowa-electricity.csv"
  },
  "transform": [
    {
      "calculate": "year(datum.year)",
      "as": "year_num"
    }
  ],
  "mark": {
    "type": "area",
    "opacity": 0.7,
    "interpolate": "monotone"
  },
  "encoding": {
    "x": {
      "field": "year_num",
      "type": "temporal",
      "axis": {
        "title": "Year",
        "format": "%Y"
      }
    },
    "y": {
      "aggregate": "sum",
      "field": "net_generation",
      "type": "quantitative",
      "axis": {
        "title": "Net Generation (GWh)"
      },
      "stack": "normalize"
    },
    "color": {
      "field": "source",
      "type": "nominal",
      "scale": {
        "scheme": "tableau10"
      },
      "legend": {
        "title": "Energy Source"
      }
    },
    "tooltip": [
      {
        "field": "year_num",
        "type": "temporal",
        "format": "%Y",
        "title": "Year"
      },
      {
        "field": "source",
        "type": "nominal",
        "title": "Source"
      },
      {
        "field": "net_generation",
        "type": "quantitative",
        "title": "Net Generation (GWh)",
        "format": ",.0f"
      }
    ]
  }
}

BASELINE REASONING:
I chose a stacked area chart with normalized (100%) scale to effectively show both the composition changes and relative growth of energy sources over time. This visualization makes it easy to see: (1) how the overall mix has shifted between sources, (2) which sources are growing (expanding areas) vs. declining (shrinking areas), and (3) the proportional contribution of each source. The normalized scale emphasizes the compositional changes, while the tooltip provides absolute generation values for detailed exploration. The smooth interpolation and semi-transparent areas improve readability when sources overlap.

Analyze both approaches. Pay special attention to whether the Draco-informed spec actually followed Draco's structural advice and whether differences between the two specs are attributable to Draco's contribution. Return JSON.
