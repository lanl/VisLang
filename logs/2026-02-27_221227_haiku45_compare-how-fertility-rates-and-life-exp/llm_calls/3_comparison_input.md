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

USER REQUEST: Compare how fertility rates and life expectancy relate across world regions, and whether population size plays a role

DRACO RECOMMENDATIONS:
{}

DRACO-INFORMED SPEC:
{
  "title": "Fertility Rate vs Life Expectancy Across World Regions",
  "width": 900,
  "height": 600,
  "data": {
    "url": "gapminder.json"
  },
  "mark": {
    "type": "circle",
    "opacity": 0.7
  },
  "encoding": {
    "x": {
      "field": "life_expect",
      "type": "quantitative",
      "title": "Life Expectancy (years)",
      "scale": {
        "zero": false
      }
    },
    "y": {
      "field": "fertility",
      "type": "quantitative",
      "title": "Fertility Rate (children per woman)",
      "scale": {
        "zero": false
      }
    },
    "size": {
      "field": "pop",
      "type": "quantitative",
      "title": "Population Size",
      "scale": {
        "type": "sqrt",
        "range": [
          50,
          1000
        ]
      }
    },
    "color": {
      "field": "cluster",
      "type": "nominal",
      "title": "Region",
      "scale": {
        "scheme": "category10"
      }
    },
    "tooltip": [
      {
        "field": "country",
        "type": "nominal",
        "title": "Country"
      },
      {
        "field": "year",
        "type": "quantitative",
        "title": "Year"
      },
      {
        "field": "life_expect",
        "type": "quantitative",
        "title": "Life Expectancy",
        "format": ".2f"
      },
      {
        "field": "fertility",
        "type": "quantitative",
        "title": "Fertility Rate",
        "format": ".2f"
      },
      {
        "field": "pop",
        "type": "quantitative",
        "title": "Population",
        "format": ","
      },
      {
        "field": "cluster",
        "type": "nominal",
        "title": "Region"
      }
    ]
  }
}

DRACO-INFORMED REASONING:
This bubble chart is the most effective visualization for comparing three continuous variables across regions. Life expectancy and fertility rate are plotted on the x and y axes to show their inverse relationship clearly. Population size is encoded as bubble size using a square root scale to prevent large populations from dominating the visual space. Color represents world regions (clusters), allowing easy identification of regional patterns. The scatter plot reveals the strong negative correlation between life expectancy and fertility while simultaneously showing how population size varies across countries and regions. Tooltips enable detailed exploration of individual countries and their specific values.

BASELINE SPEC:
{
  "title": "Fertility Rate vs Life Expectancy by World Region (Population Size Shown)",
  "width": 900,
  "height": 600,
  "data": {
    "url": "gapminder.json"
  },
  "transform": [
    {
      "filter": "datum.year == 2005"
    }
  ],
  "mark": {
    "type": "circle",
    "opacity": 0.7,
    "stroke": "white",
    "strokeWidth": 1.5
  },
  "encoding": {
    "x": {
      "field": "fertility",
      "type": "quantitative",
      "title": "Fertility Rate (children per woman)",
      "scale": {
        "zero": false
      }
    },
    "y": {
      "field": "life_expect",
      "type": "quantitative",
      "title": "Life Expectancy (years)",
      "scale": {
        "zero": false
      }
    },
    "size": {
      "field": "pop",
      "type": "quantitative",
      "title": "Population Size",
      "scale": {
        "type": "sqrt",
        "range": [
          100,
          3000
        ]
      }
    },
    "color": {
      "field": "cluster",
      "type": "nominal",
      "title": "World Region",
      "scale": {
        "scheme": "category10"
      },
      "legend": {
        "title": "Region Cluster"
      }
    },
    "tooltip": [
      {
        "field": "country",
        "type": "nominal",
        "title": "Country"
      },
      {
        "field": "fertility",
        "type": "quantitative",
        "title": "Fertility Rate",
        "format": ".2f"
      },
      {
        "field": "life_expect",
        "type": "quantitative",
        "title": "Life Expectancy",
        "format": ".1f"
      },
      {
        "field": "pop",
        "type": "quantitative",
        "title": "Population",
        "format": ","
      },
      {
        "field": "cluster",
        "type": "nominal",
        "title": "Region"
      }
    ]
  }
}

BASELINE REASONING:
This bubble chart is ideal for comparing three variables simultaneously: fertility (x-axis), life expectancy (y-axis), and population size (bubble size). The design choices are: 1) Scatter plot with circles to show the relationship between fertility and life expectancy; 2) Bubble size encodes population, allowing viewers to see if larger populations cluster in certain regions; 3) Color represents world regions (clusters), making geographic patterns visible; 4) Filtered to 2005 (most recent year) for clarity; 5) Square root scale for bubble size prevents large populations from dominating; 6) White stroke on circles improves readability with overlapping marks; 7) Comprehensive tooltips enable detailed exploration. The inverse relationship between fertility and life expectancy should be clearly visible, and regional clustering will show whether geography plays a role.

Analyze both approaches. Pay special attention to whether the Draco-informed spec actually followed Draco's structural advice and whether differences between the two specs are attributable to Draco's contribution. Return JSON.
