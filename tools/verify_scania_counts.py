"""Independent chunked Pandas cross-check; source CSVs are opened read-only."""

import argparse
import json
from pathlib import Path

import httpx
import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8075")
    args = parser.parse_args()
    root = args.data_dir / "raw"
    with httpx.Client(base_url=args.url, timeout=75, trust_env=False) as client:
        for split in ("train", "validation", "test"):
            specs = pd.read_csv(root / f"{split}_specifications.csv")
            total = present = 0
            minimum, maximum = float("inf"), 0
            for part in pd.read_csv(
                root / f"{split}_operational_readouts.csv",
                usecols=["vehicle_id", "time_step", "171_0"],
                chunksize=200000,
                na_values=["na"],
            ):
                total += len(part)
                present += int(part["171_0"].notna().sum())
                minimum = min(minimum, float(part.time_step.min()))
                maximum = max(maximum, float(part.time_step.max()))
            response = client.post(
                "/api/bi/datasets/scania_component_x/dashboard",
                json={"filters": {"split": split}, "feature": "171_0"},
            )
            response.raise_for_status()
            data = response.json()
            actual = data["overview"]
            assert actual["vehicle_count"] == len(specs)
            assert actual["readout_count"] == total
            assert actual["min_time"] == minimum and actual["max_time"] == maximum
            assert abs(actual["feature_missing_pct"] - 100 * (total - present) / total) < 1e-8
            assert {r["category"]: r["vehicle_count"] for r in data["specifications"]} == specs.groupby(
                "Spec_0"
            ).size().to_dict()
            file_name = "train_tte.csv" if split == "train" else f"{split}_labels.csv"
            label = "in_study_repair" if split == "train" else "class_label"
            outcomes = pd.read_csv(root / file_name)
            assert {r["label"]: r["vehicle_count"] for r in data["labels"]} == outcomes.groupby(
                label
            ).size().to_dict()
            print(
                json.dumps(
                    {
                        "split": split,
                        "vehicles": len(specs),
                        "readouts": total,
                        "pandas_matches": True,
                        "time_range": [minimum, maximum],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
