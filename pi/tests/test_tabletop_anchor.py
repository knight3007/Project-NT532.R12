"""Synthetic flat-target anchor errors; bbox centre is a configurable proxy."""

import copy

import numpy as np
import pytest
from test_dynamic_mapping import LAYOUTS, camera, corners_for

from nt532.vision.localize import project
from nt532.vision.system import Vision
from nt532.vision.types import Detection


def _vision(site, intrinsics, detections, anchor):
    cfg = copy.deepcopy(site)
    cfg["vision"].update(reference_mode="auto", tabletop_anchor=anchor)
    vision = Vision(cfg, intrinsics, detector=lambda image: detections)
    cam = camera(intrinsics)
    vision.commission_corners([corners_for(LAYOUTS["60x40"], cam)])
    return vision, cam


def test_center_anchor_reduces_synthetic_flat_target_error(site, intrinsics):
    cam = camera(intrinsics)
    true = np.array([0.3, 0.2, 0.])
    u, v = project(true, cam)
    detection = Detection(u, v, 30, 24, 0.9)
    center, _ = _vision(site, intrinsics, [detection], "center")
    bottom, _ = _vision(site, intrinsics, [detection], "bottom_center")
    center_error = np.linalg.norm(center.targets(np.zeros((720, 1280, 3), np.uint8))[0].position - true)
    bottom_error = np.linalg.norm(bottom.targets(np.zeros((720, 1280, 3), np.uint8))[0].position - true)
    assert center_error < 0.01
    assert bottom_error > center_error + 0.005


def test_center_anchor_handles_edge_multiple_and_no_detections(site, intrinsics):
    cam = camera(intrinsics)
    points = [np.array([0.02, 0.2, 0.]), np.array([0.4, 0.25, 0.])]
    detections = [Detection(*project(p, cam), 30, 24, 0.9) for p in points]
    vision, _ = _vision(site, intrinsics, detections, "center")
    found = vision.targets(np.zeros((720, 1280, 3), np.uint8))
    assert len(found) == 2
    assert np.stack([t.position for t in found]) == pytest.approx(np.stack(points), abs=0.01)
    vision._detector = lambda image: []
    assert vision.targets(np.zeros((720, 1280, 3), np.uint8)) == []


def test_center_anchor_rejects_outside_area_and_unusual_box(site, intrinsics):
    cam = camera(intrinsics)
    u, v = project(np.array([1.1, 1.0, 0.]), cam)
    outside = Detection(u, v, 400, 1, 0.8)
    vision, _ = _vision(site, intrinsics, [outside], "center")
    assert vision.targets(np.zeros((720, 1280, 3), np.uint8)) == []
    u, v = project(np.array([0.3, 0.2, 0.]), cam)
    vision._detector = lambda image: [Detection(u, v, 400, 1, 0.8)]
    assert len(vision.targets(np.zeros((720, 1280, 3), np.uint8))) == 1
