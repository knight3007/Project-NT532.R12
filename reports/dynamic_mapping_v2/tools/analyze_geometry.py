"""Per-frame geometry audit of node tags vs the ID 2 Dynamic Mapping table plane.

Read-only on evidence. Usage (from pi/):
    uv run --no-sync python ../build-output/origin22-geometry-v2/analyze_geometry.py FRAMES_DIR INTRINSICS OUT_DIR
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

from nt532.vision.calibration import load_intrinsics
from nt532.vision.dynamic_mapping import (DynamicMap, estimate_reference_map,
                                          estimate_reference_maps)
from nt532.vision.localize import project
from nt532.vision.tags import _square, detect_tags
from nt532.vision.types import Camera, TagPose

SIZE = 0.08
EDGE_TOL, DIAG_TOL = 0.005, 0.007


def table_points(quad, camera):
    table = DynamicMap(camera, {}, 0., 0., 0.)
    return np.stack([table.table_point(tuple(p)) for p in quad])


def square_metrics(points):
    edges = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    diags = np.linalg.norm(points[:2] - points[2:], axis=1)
    return edges, diags


def flat_pose(points):
    x = (points[1] - points[0] + points[2] - points[3]) / 2
    x[2] = 0
    x /= np.linalg.norm(x)
    y = np.cross([0., 0., 1.], x)
    return TagPose(0, points.mean(axis=0), np.column_stack((x, y, [0., 0., 1.])))


def reproj(pose, camera, quad, size=SIZE):
    proj = np.array([project(pose.to_world(p), camera) for p in _square(size)])
    d = np.linalg.norm(proj - quad, axis=1)
    return proj, float(np.sqrt(np.mean(d**2))), float(d.max())


def pnp(quad, camera, intrinsics, size=SIZE):
    n, rvecs, tvecs, errs = cv2.solvePnPGeneric(_square(size), quad, intrinsics.K,
                                                intrinsics.dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    out = []
    for rvec, tvec, err in zip(rvecs, tvecs, errs):
        rot = camera.R.T @ cv2.Rodrigues(rvec)[0]
        pos = camera.R.T @ (tvec.reshape(3) - camera.t)
        pose = TagPose(0, pos, rot)
        _, rms, mx = reproj(pose, camera, quad, size)
        tilt = float(np.rad2deg(np.arccos(np.clip(rot[2, 2], -1, 1))))
        out.append({"xyz_m": pos.tolist(), "tilt_deg": tilt, "rms_px": rms, "max_px": mx,
                    "opencv_err": float(err[0]), "x_axis": rot[:, 0].tolist()})
    return out


def tag_report(tag_id, quad, camera, intrinsics):
    pts = table_points(quad, camera)
    edges_px = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
    edges, diags = square_metrics(pts)
    edge_err = edges - SIZE
    diag_err = diags - SIZE * np.sqrt(2)
    pose = flat_pose(pts)
    _, t_rms, t_max = reproj(pose, camera, quad)
    sols = pnp(quad, camera, intrinsics)
    best = min(sols, key=lambda s: s["rms_px"])
    cz = camera.position[2]
    mean_side = float(edges.mean())
    # Scaling about the camera centre maps plane Z=0 to Z=h: side(h) = side(0) (Cz-h)/Cz.
    h_fit = float(cz * (1 - SIZE / mean_side))
    scaled = camera.position + (cz - h_fit) / cz * (pts - camera.position)
    s_edges, s_diags = square_metrics(scaled)
    hpose = TagPose(0, scaled.mean(axis=0), pose.rotation)
    _, h_rms, h_max = reproj(hpose, camera, quad)
    table_c = pts.mean(axis=0)
    pnp_c = np.asarray(best["xyz_m"])
    heading = np.rad2deg(np.arccos(np.clip(np.dot(
        np.asarray(best["x_axis"])[:2] / np.linalg.norm(np.asarray(best["x_axis"])[:2]),
        pose.rotation[:2, 0]), -1, 1)))
    return {
        "tag": tag_id,
        "edges_px": edges_px.round(2).tolist(),
        "edges_mm": (edges * 1000).round(2).tolist(),
        "diagonals_mm": (diags * 1000).round(2).tolist(),
        "edge_err_mm": (edge_err * 1000).round(2).tolist(),
        "diag_err_mm": (diag_err * 1000).round(2).tolist(),
        "max_edge_err_mm": round(float(np.max(np.abs(edge_err))) * 1000, 2),
        "worst_edge": ["TL-TR", "TR-BR", "BR-BL", "BL-TL"][int(np.argmax(np.abs(edge_err)))],
        "max_diag_err_mm": round(float(np.max(np.abs(diag_err))) * 1000, 2),
        "edge_gate_fail": bool(np.max(np.abs(edge_err)) > EDGE_TOL),
        "diag_gate_fail": bool(np.max(np.abs(diag_err)) > DIAG_TOL),
        "table_center_m": table_c.round(5).tolist(),
        "table_reproj_rms_px": round(t_rms, 3), "table_reproj_max_px": round(t_max, 3),
        "pnp_center_m": pnp_c.round(5).tolist(),
        "pnp_tilt_deg": round(best["tilt_deg"], 3),
        "pnp_reproj_rms_px": round(best["rms_px"], 3), "pnp_reproj_max_px": round(best["max_px"], 3),
        "pnp_solutions": [{k: (round(v, 5) if isinstance(v, float) else
                               [round(x, 5) for x in v]) for k, v in s.items()} for s in sols],
        "pnp_minus_table_mm": ((pnp_c - table_c) * 1000).round(2).tolist(),
        "pnp_table_xy_mm": round(float(np.linalg.norm((pnp_c - table_c)[:2])) * 1000, 2),
        "pnp_table_3d_mm": round(float(np.linalg.norm(pnp_c - table_c)) * 1000, 2),
        "pnp_table_heading_deg": round(float(heading), 3),
        "mean_side_mm": round(mean_side * 1000, 2),
        "scale_ratio": round(mean_side / SIZE, 5),
        "height_fit_m": round(h_fit, 5),
        "height_fit_center_m": scaled.mean(axis=0).round(5).tolist(),
        "height_fit_max_edge_err_mm": round(float(np.max(np.abs(s_edges - SIZE))) * 1000, 2),
        "height_fit_max_diag_err_mm": round(float(np.max(np.abs(s_diags - SIZE * np.sqrt(2))))
                                            * 1000, 2),
        "height_fit_reproj_rms_px": round(h_rms, 3), "height_fit_reproj_max_px": round(h_max, 3),
    }


def hull_info(references, point):
    centers = np.array([references[i][:2] for i in sorted(references)], np.float32)
    hull = cv2.convexHull(centers)
    d = cv2.pointPolygonTest(hull, (float(point[0]), float(point[1])), True)
    return {"inside": bool(d >= 0), "signed_distance_m": round(float(d), 5)}


def main():
    frames_dir, intr_path, out_dir = (Path(a) for a in sys.argv[1:4])
    out_dir.mkdir(parents=True, exist_ok=True)
    intrinsics = load_intrinsics(intr_path)
    paths = sorted(p for p in frames_dir.iterdir()
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    images = [cv2.imread(str(p)) for p in paths]
    obs = [detect_tags(im) for im in images]
    batch = estimate_reference_maps(obs, intrinsics, SIZE)
    frames = []
    for idx, (path, corners) in enumerate(zip(paths, obs), 1):
        cam = (batch.camera if idx == len(obs) else
               estimate_reference_map(corners, intrinsics, SIZE).camera)
        entry = {"frame": idx, "name": path.name, "camera_position_m":
                 cam.position.round(5).tolist(), "tags": {}}
        for tag_id in sorted(corners):
            entry["tags"][str(tag_id)] = tag_report(tag_id, corners[tag_id], cam, intrinsics)
        for tag_id in (21, 22):
            if tag_id in corners:
                entry["tags"][str(tag_id)]["hull"] = hull_info(
                    batch.references, entry["tags"][str(tag_id)]["table_center_m"])
        frames.append(entry)

    # Leave-one-reference-out on the latest frame: extrapolation sensitivity of ID 21/22.
    latest = obs[-1]
    loo = {}
    for drop in (0, 1, 3):
        subset = {i: c for i, c in latest.items() if i != drop}
        try:
            m = estimate_reference_map(subset, intrinsics, SIZE)
        except ValueError as exc:
            loo[str(drop)] = {"error": str(exc)}
            continue
        loo[str(drop)] = {"camera_position_m": m.camera.position.round(5).tolist(),
                          **{str(t): {k: tag_report(t, latest[t], m.camera, intrinsics)[k]
                                      for k in ("mean_side_mm", "max_edge_err_mm",
                                                "table_center_m", "height_fit_m")}
                             for t in (21, 22) if t in latest}}

    # Overlay for the latest and first frames.
    for idx in (1, len(obs)):
        im = images[idx - 1].copy()
        cam = batch.camera if idx == len(obs) else estimate_reference_map(
            obs[idx - 1], intrinsics, SIZE).camera
        hull_px = np.array([project(batch.references[i], cam) for i in sorted(batch.references)])
        hull_idx = cv2.convexHull(hull_px.astype(np.float32)).reshape(-1, 2)
        cv2.polylines(im, [hull_idx.round().astype(np.int32)], True, (255, 150, 0), 2)
        for tag_id, quad in obs[idx - 1].items():
            rep = frames[idx - 1]["tags"][str(tag_id)]
            for j, p in enumerate(quad):
                cv2.circle(im, tuple(np.round(p).astype(int)), 4, (0, 255, 0), -1)
                cv2.putText(im, str(j), tuple(np.round(p).astype(int) + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
            pose = TagPose(0, np.asarray(rep["table_center_m"]),
                           flat_pose(table_points(quad, cam)).rotation)
            proj, _, _ = reproj(pose, cam, quad)
            for p in proj:
                cv2.drawMarker(im, tuple(np.round(p).astype(int)), (0, 0, 255),
                               cv2.MARKER_TILTED_CROSS, 10, 2)
            c = np.round(quad.mean(axis=0)).astype(int)
            cv2.putText(im, f"ID {tag_id} side {rep['mean_side_mm']:.1f}mm "
                            f"max {rep['max_edge_err_mm']:+.1f}", (c[0] - 70, c[1] - 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
            if tag_id in (21, 22):
                inside = rep["hull"]["inside"]
                cv2.putText(im, f"{'INSIDE' if inside else 'OUTSIDE'} hull "
                                f"{rep['hull']['signed_distance_m']*100:+.1f}cm h_fit "
                                f"{rep['height_fit_m']*1000:+.1f}mm pnpZ "
                                f"{rep['pnp_center_m'][2]*1000:+.1f}mm",
                            (c[0] - 70, c[1] - 42), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (0, 0, 255) if not inside else (0, 200, 0), 1)
        cv2.imwrite(str(out_dir / f"geometry_overlay_frame{idx:03d}.png"), im)

    result = {"frames_dir": str(frames_dir), "intrinsics": str(intr_path),
              "batch": {"camera_position_m": batch.camera.position.round(5).tolist(),
                        "references_m": {str(k): v.round(5).tolist()
                                         for k, v in batch.references.items()},
                        "hull_area_m2": batch.hull_area_m2,
                        "joint_reprojection_px": batch.reprojection_px,
                        "metric_error_m": batch.metric_error_m},
              "frames": frames, "leave_one_out_latest": loo}
    (out_dir / "geometry.json").write_text(json.dumps(result, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
