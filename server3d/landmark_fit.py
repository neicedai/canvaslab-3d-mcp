"""Advisory camera fitting from explicitly supplied image/world correspondences.

These points are manual measurements or estimates, not detected image features.
Fitting cannot establish reconstruction fidelity or recover unseen geometry.
"""
from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import Field

from .scene_schema import Camera, Id, Point2, Strict, Vec3


class CameraLandmark(Strict):
    id: Id
    world: Vec3
    pixel: Point2
    measurement: Literal["measured", "estimated"]
    evidence: str = Field(min_length=1, max_length=1000)


def _basis(azimuth: float, elevation: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    a, e = azimuth, elevation
    right = np.array([math.cos(a), 0., -math.sin(a)])
    up = np.array([-math.sin(e) * math.sin(a), math.cos(e), -math.sin(e) * math.cos(a)])
    outward = np.array([math.cos(e) * math.sin(a), math.sin(e), math.cos(e) * math.cos(a)])
    return right, up, outward


def project_orthographic_points(source_size, world_points, camera) -> list[list[float]]:
    """Project with the exact native-aspect fitting rule used by runtime3d."""
    camera = Camera.model_validate(camera).model_dump()
    if camera["projection"] != "orthographic":
        raise ValueError("Landmark camera fitting currently supports orthographic cameras only")
    width, height = source_size
    outward = np.asarray(camera["position"]) - np.asarray(camera["target"])
    azimuth = math.atan2(outward[0], outward[2])
    elevation = math.atan2(outward[1], math.hypot(outward[0], outward[2]))
    right, up, _ = _basis(azimuth, elevation)
    scale = height / (camera["vertical_span"] * max(1., 1.45 / (width / height)))
    relative = np.asarray(world_points, dtype=float) - np.asarray(camera["target"])
    return (np.column_stack((relative @ right, -relative @ up)) * scale + [width / 2, height / 2]).tolist()


def fit_orthographic_landmarks(source_size, landmarks, initial_camera) -> dict:
    """Fit Y-up camera yaw, elevation, scale and screen translation, with bounds.

    ``source_size`` and every pixel must describe the same native image or crop.
    Crop callers must subtract the crop origin themselves. The original view
    direction's distance is retained, because orthographic landmarks cannot
    determine that distance or translation along the viewing direction.
    """
    if (not isinstance(source_size, (tuple, list)) or len(source_size) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 1 <= v <= 8192 for v in source_size)):
        raise ValueError("source_size must contain two finite native dimensions in [1,8192]")
    width, height = map(float, source_size)
    if not isinstance(landmarks, list) or not 4 <= len(landmarks) <= 128:
        raise ValueError("Provide 4 to 128 explicit native image/world landmarks")
    points = [CameraLandmark.model_validate(item) for item in landmarks]
    if len({point.id for point in points}) != len(points):
        raise ValueError("Landmark IDs must be unique")
    if any(not point.evidence.strip() for point in points):
        raise ValueError("Landmark evidence must explain the measurement or estimate")
    if any(point.pixel[0] > width or point.pixel[1] > height for point in points):
        raise ValueError("Landmarks must lie within the supplied native image or crop")
    camera = Camera.model_validate(initial_camera).model_dump()
    if camera["projection"] != "orthographic":
        raise ValueError("Landmark camera fitting currently supports orthographic cameras only")
    world = np.asarray([point.world for point in points], dtype=float)
    pixels = np.asarray([point.pixel for point in points], dtype=float)
    weights = np.asarray([1. if point.measurement == "measured" else .35 for point in points])
    center = np.average(world, axis=0, weights=weights)
    pixel_center = np.average(pixels, axis=0, weights=weights)
    centered, expected = world - center, pixels - pixel_center
    rank = int(np.linalg.matrix_rank(centered, tol=1e-7))
    if rank < 2 or np.linalg.matrix_rank(expected, tol=1e-5) < 2:
        raise ValueError("Landmarks must span at least two independent world and image directions")
    outward = np.asarray(camera["position"]) - np.asarray(camera["target"])
    initial_yaw = math.atan2(outward[0], outward[2])
    initial_elevation = math.atan2(outward[1], math.hypot(outward[0], outward[2]))
    # Preserve a nearby, above-ground view and the runtime's fixed Y-up/no-roll
    # convention. Geometry changes remain separate, reviewable plan edits.
    lower = np.array([initial_yaw - math.pi / 4, math.radians(10)])
    upper = np.array([initial_yaw + math.pi / 4, math.radians(75)])
    span_factor = max(1., 1.45 / (width / height))
    min_scale, max_scale = height / (150 * span_factor), height / span_factor

    def evaluate(angles):
        right, up, _ = _basis(*angles)
        projected = np.column_stack((centered @ right, -centered @ up))
        denominator = np.sum(weights[:, None] * projected * projected)
        scale = np.clip(np.sum(weights[:, None] * projected * expected) / max(denominator, 1e-12), min_scale, max_scale)
        residual = (projected * scale - expected) * np.sqrt(weights[:, None])
        return residual.ravel(), float(scale), projected * scale + pixel_center

    seeds = [np.array([a, e]) for a in np.linspace(lower[0], upper[0], 13)
             for e in np.linspace(lower[1], upper[1], 14)]
    seeds.append(np.clip([initial_yaw, initial_elevation], lower, upper))
    angles = min(seeds, key=lambda a: float(np.sum(evaluate(a)[0] ** 2)))
    damping = 1e-3
    for _ in range(60):
        residual, _, _ = evaluate(angles)
        columns = []
        for axis in range(2):
            delta = np.zeros(2); delta[axis] = 1e-5
            columns.append((evaluate(angles + delta)[0] - evaluate(angles - delta)[0]) / 2e-5)
        jacobian = np.column_stack(columns)
        normal = jacobian.T @ jacobian
        step = np.linalg.solve(normal + np.eye(2) * damping * max(1., np.trace(normal) / 2), -jacobian.T @ residual)
        step *= min(1., .3 / max(np.linalg.norm(step), 1e-12))
        candidate = np.clip(angles + step, lower, upper)
        if np.sum(evaluate(candidate)[0] ** 2) < np.sum(residual ** 2):
            angles = candidate; damping = max(1e-9, damping * .3)
            if np.linalg.norm(step) < 1e-9:
                break
        else:
            damping *= 10
            if damping > 1e9:
                break

    _, scale, projected = evaluate(angles)
    right, up, direction = _basis(*angles)
    target = center - right * (pixel_center[0] - width / 2) / scale + up * (pixel_center[1] - height / 2) / scale
    target += direction * np.dot(np.asarray(camera["target"]) - target, direction)
    proposed = {**camera, "target": target.tolist(), "position": (target + direction * np.linalg.norm(outward)).tolist(),
                "vertical_span": height / (scale * span_factor)}
    proposed = Camera.model_validate(proposed).model_dump()
    # Reproject from the returned camera, rather than trusting optimizer state.
    projected = np.asarray(project_orthographic_points(source_size, world, proposed))
    deltas = projected - pixels
    errors = np.linalg.norm(deltas, axis=1)
    initial_projected = np.asarray(project_orthographic_points(source_size, world, camera))
    rms = float(np.sqrt(np.mean(errors ** 2)))
    warnings = ["Manual landmark agreement is camera calibration evidence, not a reconstruction certificate."]
    if rank == 2:
        warnings.append("World landmarks are coplanar; include independently measured elevated features to check camera/geometry ambiguity.")
    if any(point.measurement == "estimated" for point in points):
        warnings.append("Estimated points use weight 0.35; their uncertainty remains in the result.")
    if np.any(np.isclose(angles, lower, atol=1e-5)) or np.any(np.isclose(angles, upper, atol=1e-5)) or scale in (min_scale, max_scale):
        warnings.append("A camera bound is active; inspect incorrect correspondences or object proportions before applying.")
    if rms > min(width, height) * .02:
        warnings.append("Residual exceeds 2% of the short canvas edge; camera fitting alone does not explain the supplied landmarks.")
    return {"method": "manual-landmark-orthographic-fit-v1", "certified": False,
            "source_size": [width, height], "suggested_camera": proposed,
            "initial_rms_px": float(np.sqrt(np.mean(np.sum((initial_projected - pixels) ** 2, axis=1)))),
            "rms_px": rms, "weighted_rms_px": float(np.sqrt(np.average(errors ** 2, weights=weights))),
            "max_residual_px": float(np.max(errors)),
            "fit_parameters": {"azimuth_degrees": math.degrees(angles[0]), "elevation_degrees": math.degrees(angles[1]), "pixels_per_world_unit": scale},
            "residuals": [{"id": point.id, "measurement": point.measurement, "evidence": point.evidence,
                           "pixel": point.pixel, "projected": projected[i].tolist(), "delta": deltas[i].tolist(), "error_px": float(errors[i])}
                          for i, point in enumerate(points)], "warnings": warnings}
