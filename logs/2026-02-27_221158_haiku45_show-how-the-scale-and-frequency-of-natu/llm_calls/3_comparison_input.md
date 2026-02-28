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

USER REQUEST: Show how the scale and frequency of natural disasters has changed over time across different disaster types

DRACO RECOMMENDATIONS:
{
  "mark_type": "line",
  "encodings": [
    {
      "channel": "x",
      "field": "year"
    },
    {
      "channel": "y",
      "field": "deaths"
    },
    {
      "channel": "color",
      "field": "entity"
    }
  ],
  "scales": [
    {
      "channel": "color",
      "type": "ordinal"
    },
    {
      "channel": "y",
      "type": "linear",
      "zero": "true"
    },
    {
      "channel": "x",
      "type": "linear"
    }
  ],
  "coordinates": "cartesian",
  "facets": [],
  "task": "summary",
  "cost": [
    50
  ],
  "violations": {
    "encoding": 3,
    "encoding_field": 3,
    "continuous_not_zero": 1,
    "continuous_pos_not_zero": 1,
    "linear_scale": 2,
    "ordinal_scale": 1,
    "c_c_line": 1,
    "linear_x": 1,
    "linear_y": 1,
    "ordinal_color": 1,
    "summary_line": 1,
    "cartesian_coordinate": 1
  }
}

DRACO-INFORMED SPEC:
{
  "title": "Natural Disasters: Deaths Over Time by Disaster Type",
  "width": 1000,
  "height": 600,
  "mark": {
    "type": "line",
    "point": true,
    "tooltip": true
  },
  "encoding": {
    "x": {
      "field": "year",
      "type": "quantitative",
      "scale": {
        "type": "linear"
      },
      "axis": {
        "title": "Year",
        "labelAngle": 0
      }
    },
    "y": {
      "field": "deaths",
      "type": "quantitative",
      "scale": {
        "type": "linear",
        "zero": true
      },
      "axis": {
        "title": "Number of Deaths",
        "format": ",.0f"
      }
    },
    "color": {
      "field": "entity",
      "type": "nominal",
      "scale": {
        "type": "ordinal",
        "scheme": "category10"
      },
      "legend": {
        "title": "Disaster Type",
        "orient": "right"
      }
    },
    "tooltip": [
      {
        "field": "year",
        "type": "quantitative",
        "title": "Year"
      },
      {
        "field": "entity",
        "type": "nominal",
        "title": "Disaster Type"
      },
      {
        "field": "deaths",
        "type": "quantitative",
        "title": "Deaths",
        "format": ",.0f"
      }
    ]
  }
}

DRACO-INFORMED REASONING:
Following Draco's recommendations, I used a line mark with points to show temporal trends across disaster types. The x-axis represents years (linear scale) and y-axis represents deaths (linear scale with zero baseline). Each disaster type is distinguished by color using an ordinal scale with a categorical color scheme for clear differentiation. The line mark effectively reveals patterns and trends over the 117-year span, while the color encoding allows comparison across the 11 different disaster entities. Point marks are added for better interactivity and clarity at specific data points. Tooltips provide detailed information on hover, and axis labels are formatted for readability (deaths shown with thousand separators).

BASELINE SPEC:
{
  "title": "Natural Disasters: Scale and Frequency Over Time (1900-2017)",
  "width": 1000,
  "height": 600,
  "data": {
    "url": "disasters.csv"
  },
  "transform": [
    {
      "filter": "datum.entity !== 'All natural disasters'"
    },
    {
      "calculate": "datum.deaths > 0 ? 1 : 0",
      "as": "event_count"
    }
  ],
  "mark": {
    "type": "circle",
    "opacity": 0.6,
    "tooltip": true
  },
  "encoding": {
    "x": {
      "field": "year",
      "type": "temporal",
      "axis": {
        "title": "Year",
        "format": "d"
      }
    },
    "y": {
      "field": "entity",
      "type": "nominal",
      "axis": {
        "title": "Disaster Type",
        "labelLimit": 150
      }
    },
    "size": {
      "field": "deaths",
      "type": "quantitative",
      "scale": {
        "type": "sqrt",
        "zero": false
      },
      "title": "Deaths (area represents magnitude)"
    },
    "color": {
      "field": "deaths",
      "type": "quantitative",
      "scale": {
        "scheme": "reds",
        "type": "log"
      },
      "title": "Deaths (log scale)"
    },
    "tooltip": [
      {
        "field": "year",
        "type": "temporal",
        "format": "d",
        "title": "Year"
      },
      {
        "field": "entity",
        "type": "nominal",
        "title": "Disaster Type"
      },
      {
        "field": "deaths",
        "type": "quantitative",
        "format": ",",
        "title": "Deaths"
      }
    ]
  }
}

BASELINE REASONING:
This bubble chart design effectively shows three dimensions of disaster data: (1) Time progression on the x-axis, (2) Disaster types on the y-axis, and (3) Scale/severity through bubble size (area) and color intensity. The sqrt scale for size prevents large values from dominating the visualization, while the log color scale highlights both extreme and moderate disasters. The 'All natural disasters' aggregate is filtered out to focus on specific disaster types. Bubble charts are ideal for showing frequency (each point = one year-disaster combination) and magnitude simultaneously. The red color scheme intuitively represents danger/severity. Tooltips enable detailed exploration of specific events.

Analyze both approaches. Pay special attention to whether the Draco-informed spec actually followed Draco's structural advice and whether differences between the two specs are attributable to Draco's contribution. Return JSON.
