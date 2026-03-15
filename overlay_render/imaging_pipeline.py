"""
High-level imaging analysis pipeline for RamanLab antennal lobe experiments.

ROI SCOPE: One rois.json per experiment folder (per fly). All trials within
that experiment share the same ROIs. Each new fly requires a new drawing session.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def _get_image_shape(experiment_dir: Path) -> tuple[int, int]:
    import tifffile
    for p in sorted(Path(experiment_dir).glob("fly_*/trial_*/images/images/frame_*.tif")):
        return tifffile.imread(str(p)).shape[:2]
    raise FileNotFoundError(f"No frames found under {experiment_dir}")


def _save_dff_csv(dff_results: dict, output_path: Path) -> None:
    if not dff_results:
        return
    n_frames = max(len(v["dff"]) for v in dff_results.values())
    with open(output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame"] + list(dff_results.keys()))
        for i in range(n_frames):
            row = [i]
            for data in dff_results.values():
                row.append(float(data["dff"][i]) if i < len(data["dff"]) else "")
            w.writerow(row)


def run_pipeline(
    experiment_dir: Path,
    output_dir: Path,
    method: str = "manual",
    redraw_rois: bool = False,
) -> None:
    from .glomerulus_id import (
        extract_dff_traces,
        generate_mean_image,
        launch_roi_editor,
        load_rois,
        plot_dff_traces,
        save_rois,
    )

    experiment_dir = Path(experiment_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    structure_dir = experiment_dir / "structure"
    mean_image = generate_mean_image(structure_dir) if structure_dir.exists() else None

    roi_path = experiment_dir / "rois.json"
    image_shape = _get_image_shape(experiment_dir)

    if roi_path.exists() and not redraw_rois:
        rois = load_rois(roi_path, image_shape)
        print(f"Loaded {len(rois)} ROIs from {roi_path}")
    else:
        if mean_image is None:
            raise RuntimeError("No structure/ image available to draw ROIs.")
        existing = load_rois(roi_path, image_shape) if roi_path.exists() else None
        rois = launch_roi_editor(mean_image, existing_rois=existing)
        if not rois:
            print("No ROIs drawn. Exiting.")
            return
        save_rois(rois, roi_path)
        rois = load_rois(roi_path, image_shape)

    trial_dirs = sorted(experiment_dir.glob("fly_*/trial_*"))
    if not trial_dirs:
        print(f"No fly_*/trial_* directories found in {experiment_dir}")
        return

    summary = {}
    for trial_dir in trial_dirs:
        trial_name = f"{trial_dir.parent.name}/{trial_dir.name}"
        trial_out = output_dir / trial_dir.parent.name / trial_dir.name
        trial_out.mkdir(parents=True, exist_ok=True)
        print(f"Processing {trial_name} with method={method}...")
        try:
            if method == "manual":
                dff_results = extract_dff_traces(trial_dir, rois)
            elif method == "suite2p":
                from .suite2p_runner import load_suite2p_results, match_suite2p_rois_to_glomeruli, run_suite2p
                s2p_dir = run_suite2p(trial_dir, trial_out / "suite2p_output")
                s2p = load_suite2p_results(s2p_dir)
                mapping = match_suite2p_rois_to_glomeruli(s2p, rois, image_shape)
                dff_results = {}
                for name, idx in mapping.items():
                    if idx < 0:
                        continue
                    dff_results[name] = {
                        "raw_f": s2p["F_corr"][idx],
                        "dff": s2p["dff"][idx],
                        "f0": float(np.percentile(s2p["F_corr"][idx], 10)),
                    }
            elif method == "caiman":
                from .caiman_runner import load_caiman_results, match_caiman_rois_to_glomeruli, run_caiman
                cm_dir = run_caiman(trial_dir, trial_out / "caiman_output")
                cm = load_caiman_results(cm_dir)
                mapping = match_caiman_rois_to_glomeruli(cm, rois, image_shape)
                dff_results = {}
                for name, idx in mapping.items():
                    if idx < 0:
                        continue
                    dff_results[name] = {
                        "raw_f": cm["C"][idx],
                        "dff": cm["dff"][idx],
                        "f0": float(np.percentile(cm["C"][idx], 10)),
                    }
            else:
                raise ValueError(f"Unknown method: {method}")

            _save_dff_csv(dff_results, trial_out / "dff_traces.csv")
            np.save(str(trial_out / "dff_traces.npy"), dff_results, allow_pickle=True)
            plot_dff_traces(dff_results, trial_dir / "trial.json", output_path=trial_out / "dff_plot.png")
            summary[trial_name] = {"status": "ok", "glomeruli": list(dff_results.keys())}
        except Exception as e:
            summary[trial_name] = {"status": "error", "error": str(e)}
            print(f"  ERROR: {e}")

    with open(output_dir / "pipeline_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved summary to {output_dir / 'pipeline_summary.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RamanLab antennal lobe imaging pipeline")
    parser.add_argument("--experiment-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--method", default="manual", choices=["manual", "suite2p", "caiman"])
    parser.add_argument("--redraw-rois", action="store_true")
    args = parser.parse_args()
    run_pipeline(args.experiment_dir, args.output_dir, args.method, args.redraw_rois)


if __name__ == "__main__":
    main()
