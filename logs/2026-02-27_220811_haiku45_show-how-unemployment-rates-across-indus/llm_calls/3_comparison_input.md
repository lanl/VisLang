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

USER REQUEST: Show how unemployment rates across industries changed during recessions vs expansions

DRACO RECOMMENDATIONS:
{
  "mark_type": "point",
  "encodings": [
    {
      "channel": "y",
      "field": "series"
    },
    {
      "channel": "x",
      "field": "date",
      "binning": 10
    },
    {
      "channel": "size",
      "field": "rate",
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
      "type": "linear"
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
    "aggregate": 1,
    "bin": 1,
    "encoding": 3,
    "encoding_field": 3,
    "linear_scale": 2,
    "ordinal_scale": 1,
    "d_d_point": 1,
    "linear_x": 1,
    "linear_size": 1,
    "ordinal_y": 1,
    "aggregate_mean": 1,
    "summary_point": 1,
    "cartesian_coordinate": 1
  }
}

DRACO-INFORMED SPEC:
{
  "title": "Unemployment Rates Across Industries: Recessions vs Expansions (2000-2010)",
  "width": 1000,
  "height": 500,
  "data": {
    "url": "unemployment-across-industries.json"
  },
  "transform": [
    {
      "calculate": "datum.date",
      "as": "date_parsed"
    },
    {
      "bin": {
        "step": 10
      },
      "field": "date_parsed",
      "as": "date_binned"
    },
    {
      "aggregate": [
        {
          "op": "mean",
          "field": "rate",
          "as": "mean_rate"
        }
      ],
      "groupby": [
        "series",
        "date_binned"
      ]
    }
  ],
  "mark": {
    "type": "point",
    "opacity": 0.7,
    "tooltip": true
  },
  "encoding": {
    "y": {
      "field": "series",
      "type": "ordinal",
      "title": "Industry",
      "sort": "-x"
    },
    "x": {
      "field": "date_binned",
      "type": "quantitative",
      "title": "Time Period (2000-2010)",
      "axis": {
        "format": "d"
      }
    },
    "size": {
      "field": "mean_rate",
      "type": "quantitative",
      "title": "Mean Unemployment Rate (%)",
      "scale": {
        "type": "linear",
        "zero": true,
        "range": [
          50,
          400
        ]
      }
    },
    "color": {
      "field": "mean_rate",
      "type": "quantitative",
      "title": "Mean Unemployment Rate (%)",
      "scale": {
        "scheme": "reds"
      }
    },
    "tooltip": [
      {
        "field": "series",
        "type": "nominal",
        "title": "Industry"
      },
      {
        "field": "mean_rate",
        "type": "quantitative",
        "title": "Mean Unemployment Rate",
        "format": ".2f"
      },
      {
        "field": "date_binned",
        "type": "quantitative",
        "title": "Time Period"
      }
    ]
  }
}

DRACO-INFORMED REASONING:
Fixed the validation error by changing the x-axis type from 'linear' to 'quantitative', which is the correct Vega-Lite data type specification. The scale type remains 'linear' within the scale object. Following Draco's recommendations, point marks display industries on the y-axis (ordinal) and binned dates on the x-axis (quantitative). Mean unemployment rate is encoded as both size and color using a red sequential scheme to highlight severity during recessions. The binning aggregates data into 10-unit time periods to reduce overplotting while preserving temporal trends. Industries are sorted by maximum unemployment rate for readability, and tooltips enable detailed exploration of specific periods.

BASELINE SPEC:
{
  "title": "Unemployment Rates Across Industries: Recessions vs Expansions (2000-2010)",
  "width": 1000,
  "height": 600,
  "data": {
    "url": "unemployment-across-industries.json"
  },
  "transform": [
    {
      "calculate": "datum.year + (datum.month - 1) / 12",
      "as": "year_fraction"
    },
    {
      "calculate": "datum.year === 2001 || datum.year === 2008 || (datum.year === 2009 && datum.month <= 6) ? 'Recession' : 'Expansion'",
      "as": "period"
    }
  ],
  "mark": {
    "type": "line",
    "point": true,
    "opacity": 0.8,
    "strokeWidth": 2
  },
  "encoding": {
    "x": {
      "field": "date",
      "type": "temporal",
      "title": "Date",
      "axis": {
        "format": "%Y",
        "labelAngle": 0
      }
    },
    "y": {
      "field": "rate",
      "type": "quantitative",
      "title": "Unemployment Rate (%)",
      "scale": {
        "zero": false
      }
    },
    "color": {
      "field": "series",
      "type": "nominal",
      "title": "Industry",
      "scale": {
        "scheme": "category10"
      }
    },
    "strokeDash": {
      "field": "period",
      "type": "nominal",
      "title": "Economic Period",
      "scale": {
        "domain": [
          "Recession",
          "Expansion"
        ],
        "range": [
          [
            3,
            3
          ],
          []
        ]
      }
    },
    "tooltip": [
      {
        "field": "date",
        "type": "temporal",
        "format": "%B %Y",
        "title": "Date"
      },
      {
        "field": "series",
        "type": "nominal",
        "title": "Industry"
      },
      {
        "field": "rate",
        "type": "quantitative",
        "title": "Unemployment Rate (%)",
        "format": ".2f"
      },
      {
        "field": "period",
        "type": "nominal",
        "title": "Period"
      }
    ]
  },
  "config": {
    "mark": {
      "tooltip": true
    },
    "axis": {
      "labelFontSize": 11,
      "titleFontSize": 12
    },
    "legend": {
      "labelFontSize": 10,
      "titleFontSize": 11
    }
  }
}

BASELINE REASONING:
This visualization uses line charts with dashed lines for recession periods to effectively show unemployment trends across industries over time. Key design choices: (1) Temporal x-axis shows the full 2000-2010 period; (2) Color encoding distinguishes industries for easy comparison; (3) Stroke dash pattern differentiates recession vs expansion periods without cluttering the view; (4) Points on lines aid readability; (5) Tooltips provide precise values for exploration; (6) The 2001 recession, 2008-2009 financial crisis, and expansion periods are marked using domain knowledge of US economic cycles. This allows viewers to see how different industries were affected differently during economic downturns vs growth periods.

Analyze both approaches. Pay special attention to whether the Draco-informed spec actually followed Draco's structural advice and whether differences between the two specs are attributable to Draco's contribution. Return JSON.
