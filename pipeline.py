#!/usr/bin/env python3
"""VisLang CLI — run the Draco-guided visualization pipeline from the command line.

Usage:
    python pipeline.py "Show horsepower vs MPG" cars.json
    python pipeline.py --model openai/gpt-4o "Compare stock trends" stocks.csv
    python pipeline.py --notebook --open "Weather distribution" seattle-weather.csv
"""

import argparse
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(
        description="VisLang: Draco-guided visualization pipeline",
    )
    parser.add_argument("prompt", help="Natural language visualization request")
    parser.add_argument("data", help="Path to data file (resolved via vega-datasets/data/)")
    parser.add_argument("--model", default=None, help="LiteLLM model string (default: VISLANG_MODEL env or claude-sonnet)")
    parser.add_argument("--no-log", action="store_true", help="Skip writing artifacts to logs/")
    parser.add_argument("--notebook", action="store_true", help="Create and execute a Jupyter notebook")
    parser.add_argument("--open", action="store_true", help="Open the notebook in VS Code (implies --notebook)")

    args = parser.parse_args()

    if args.open:
        args.notebook = True

    from tools.pipeline import run_vislang

    print(f"Running VisLang pipeline...")
    print(f"  Prompt: {args.prompt}")
    print(f"  Data: {args.data}")
    if args.model:
        print(f"  Model: {args.model}")
    print()

    result = run_vislang(
        prompt=args.prompt,
        data_path=args.data,
        model=args.model,
        log=not args.no_log,
        notebook=args.notebook,
    )

    # Print summary
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)

    if result.recommendations:
        print(f"\nDraco recommended: {result.recommendations.get('mark_type', '?')} mark")
        encs = result.recommendations.get("encodings", [])
        for enc in encs:
            field = enc.get("field", "?")
            channel = enc.get("channel", "?")
            agg = enc.get("aggregate", "")
            print(f"  {field} → {channel}" + (f" ({agg})" if agg else ""))

    print(f"\nDraco-informed spec: {'valid' if result.draco_chart else 'FAILED'}")
    print(f"Baseline spec: {'valid' if result.baseline_chart else 'FAILED'}")

    if result.comparison_summary:
        print(f"\nComparison: {result.comparison_summary}")
    if result.comparison_verdict:
        print(f"Verdict: {result.comparison_verdict}")

    if result.log_dir:
        print(f"\nArtifacts: {result.log_dir}")

    if args.open and result.log_dir:
        # Find the notebook
        from pathlib import Path
        output_dir = Path(__file__).parent / "output"
        notebooks = sorted(output_dir.glob("*.ipynb"))
        if notebooks:
            subprocess.run(["code", str(notebooks[-1])])

    return 0


if __name__ == "__main__":
    sys.exit(main())
