"""One-off script to export flattened versions of datasets used by
employee_1mb_L4_1 and synthetic_nested_L4_1."""

import json
from pathlib import Path
from flatten_json import flatten as flatten_obj


def flatten_records(records, separator="."):
    return [flatten_obj(record, separator) for record in records]


DATASETS = {
    "synthetic_nested_L4_1": "synthetic_nested.json",
    "employee_1mb_L4_1": "Employee 1 MB 5 Level Formatted.json",
}

data_dir = Path("data")
out_dir = Path("unorg/flattened_data")
out_dir.mkdir(parents=True, exist_ok=True)

for prompt_id, filename in DATASETS.items():
    src = data_dir / filename
    records = json.loads(src.read_text())
    flat = flatten_records(records)
    dest = out_dir / f"{prompt_id}_flat.json"
    dest.write_text(json.dumps(flat, indent=2))
    print(f"{prompt_id}: {len(flat)} records -> {dest}")
