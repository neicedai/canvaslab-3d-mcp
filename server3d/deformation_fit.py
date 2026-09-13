"""Joint, bounded source-landmark fitting; predictions precede mesh normalization.

The fixed Blender worker applies overlapping radial fields, not independent
point moves. Fit those fields together and apply estimated-point confidence
once. Final normalization, topology and source fidelity require a fresh capture.
"""
from __future__ import annotations

import copy
import math
from typing import Literal

import numpy as np
from pydantic import Field

from .component_schema import ComponentRecipe
from .scene_schema import Camera, Id, Point2, Strict


class DeformationLandmark(Strict):
    id: Id
    anchor: list[float] = Field(min_length=3, max_length=3)
    pixel: Point2
    measurement: Literal["measured", "estimated"] = "measured"
    radius: float = Field(default=.25, ge=.05, le=.9)
    evidence: str = Field(min_length=1, max_length=1000)


def _quat_rotate(point, q):
    point, xyz = np.asarray(point, dtype=float), np.asarray(q[:3], dtype=float)
    uv = np.cross(xyz, point)
    return point + 2 * (q[3] * uv + np.cross(xyz, uv))


def _camera_basis(camera):
    back = np.asarray(camera["position"], dtype=float) - np.asarray(camera["target"], dtype=float)
    back /= np.linalg.norm(back)
    horizontal = math.hypot(back[0], back[2])
    if horizontal < 1e-9:
        raise ValueError("orthographic deformation fitting requires a nonvertical camera")
    return (np.asarray([back[2]/horizontal, 0, -back[0]/horizontal]),
            np.asarray([-back[1]*back[0]/horizontal, horizontal, -back[1]*back[2]/horizontal]))


def project_local_point(plan, obj, local_point, source_size, *, framing_aspect):
    camera = plan["camera"]
    if camera["projection"] != "orthographic":
        raise ValueError("component deformation fitting currently supports orthographic cameras only")
    width, height = map(float, source_size)
    world = _quat_rotate(np.asarray(local_point)*obj["dimensions"], obj["rotation"]) + obj["position"]
    right, up = _camera_basis(camera)
    relative = world - camera["target"]
    scale = height / (camera["vertical_span"] * max(1., framing_aspect/(width/height)))
    return np.asarray([width/2 + np.dot(relative, right)*scale, height/2 - np.dot(relative, up)*scale])


def apply_handle_field(point, handles):
    """Match blender_deform.deform_point, including overlap and total-displacement cap."""
    point = np.asarray(point, dtype=float)
    displacement = np.zeros(3)
    for handle in handles:
        t = max(0., 1. - np.linalg.norm(point-handle["anchor"])/handle["radius"])
        displacement += np.asarray(handle["offset"]) * (t*t*(3-2*t)*handle["strength"])
    length = np.linalg.norm(displacement)
    if length > .45:
        displacement *= .45/length
    return point + displacement


def suggest_deformation_handles(plan, analysis, obj, recipe, landmarks):
    if obj.get("kind") != "asset" or obj.get("parent_id") is not None:
        raise ValueError("deformation fitting currently supports root registered assets only")
    Camera.model_validate(plan["camera"])
    if plan["camera"]["projection"] != "orthographic":
        raise ValueError("deformation fitting currently supports orthographic cameras only")
    for key, length in (("position", 3), ("dimensions", 3), ("rotation", 4)):
        values = obj.get(key)
        if (not isinstance(values, list) or len(values) != length or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
            raise ValueError("Object transforms must be finite numeric vectors")
    if min(obj["dimensions"]) <= 0 or abs(sum(v*v for v in obj["rotation"])-1) > .001:
        raise ValueError("Object requires positive dimensions and a normalized quaternion")
    if not isinstance(landmarks, list) or not 1 <= len(landmarks) <= 16:
        raise ValueError("Provide 1..16 source/component deformation landmarks")
    points = [DeformationLandmark.model_validate(item) for item in landmarks]
    if len({p.id for p in points}) != len(points):
        raise ValueError("deformation landmark IDs must be unique")
    sx, sy, sw, sh = analysis["scene_box"]
    if not all(math.isfinite(v) for v in (sx, sy, sw, sh)) or sw <= 0 or sh <= 0:
        raise ValueError("Invalid native source crop")
    aspect = sw/sh if plan.get("appearance_mode") == "reference" else 1.45
    base = ComponentRecipe.model_validate(recipe).model_dump()
    if base.get("deformation_handles"):
        raise ValueError("fit against the undeformed/base component recipe; existing deformation handles must be reviewed explicitly")
    for i, point in enumerate(points):
        ax, ay, az = point.anchor
        if not (-.5 <= ax <= .5 and 0 <= ay <= 1 and -.5 <= az <= .5):
            raise ValueError("component anchor must be inside normalized component bounds")
        if not sx <= point.pixel[0] <= sx+sw or not sy <= point.pixel[1] <= sy+sh:
            raise ValueError("source pixel must lie inside the original scene crop")
        if not point.evidence.strip():
            raise ValueError("Landmark evidence must explain the source correspondence")
        if any(np.linalg.norm(np.asarray(point.anchor)-p.anchor) < 1e-6 for p in points[:i]):
            raise ValueError("Duplicate component anchors; consolidate their source evidence")
    project = lambda p: project_local_point(plan, obj, p, [sw, sh], framing_aspect=aspect)
    current = np.asarray([project(p.anchor) for p in points])
    desired = np.asarray([p.pixel for p in points]) - [sx, sy]
    confidence = np.asarray([1. if p.measurement == "measured" else .4 for p in points])
    # For orthographic projection the transform Jacobian is constant everywhere.
    origin = project([0., 0., 0.])
    jacobian = np.column_stack([project(axis)-origin for axis in np.eye(3)])
    kernel = np.zeros((len(points), len(points)))
    for i, a in enumerate(points):
        for j, b in enumerate(points):
            t = max(0., 1-np.linalg.norm(np.asarray(a.anchor)-b.anchor)/b.radius)
            kernel[i, j] = t*t*(3-2*t)*confidence[j]
    system = np.kron(kernel, jacobian)
    # Confidence reduces the desired move once. The system already includes the
    # strength used by Blender, so it must NOT attenuate the raw offset again.
    target_delta = ((desired-current)*confidence[:, None]).ravel()
    ridge = 1e-5 * max(1., np.sum(system*system)/system.shape[1])
    hessian = system.T@system + np.eye(system.shape[1])*ridge
    rhs = system.T@target_delta
    def bounded(value):
        value = np.clip(value.reshape(-1, 3), -.35, .35)
        norms = np.linalg.norm(value, axis=1)
        return (value*np.minimum(1., .45/np.maximum(norms, 1e-12))[:, None]).ravel()
    offsets = bounded(np.linalg.solve(hessian, rhs))
    step = 1/max(float(np.linalg.norm(hessian, 2)), 1e-12)
    for _ in range(160):
        revised = bounded(offsets-step*(hessian@offsets-rhs))
        if np.max(np.abs(revised-offsets)) < 1e-10:
            break
        offsets = revised
    handles = [{"anchor":p.anchor, "offset":v.tolist(), "radius":p.radius, "strength":float(c)}
               for p, v, c in zip(points, offsets.reshape(-1, 3), confidence) if np.dot(v, v) >= 1e-8]
    # Evaluate the complete executed field, including clipping, at EVERY point.
    # Backtracking can retain an unchanged recipe rather than publish a worse fit.
    target = current + (desired-current)*confidence[:, None]
    initial_loss = float(np.sum((target-current)**2))
    best_loss, best_handles, predicted = initial_loss, [], current.copy()
    for gain in (1., .75, .5, .25):
        trial = [dict(h, offset=[v*gain for v in h["offset"]]) for h in handles
                 if sum((v*gain)**2 for v in h["offset"]) >= 1e-8]
        evaluated = np.asarray([project(apply_handle_field(p.anchor, trial)) for p in points])
        loss = float(np.sum((evaluated-target)**2))
        if loss < best_loss-1e-10:
            best_loss, best_handles, predicted = loss, trial, evaluated
    proposed = ComponentRecipe.model_validate(dict(base, deformation_handles=best_handles)).model_dump()
    diagnostics = []
    for i, point in enumerate(points):
        diagnostics.append({"id":point.id, "measurement":point.measurement, "evidence":point.evidence,
                            "source_pixel":point.pixel, "current_crop_pixel":current[i].tolist(),
                            "target_crop_pixel":desired[i].tolist(), "predicted_crop_pixel":predicted[i].tolist(),
                            "residual_px":(desired[i]-current[i]).tolist(),
                            "before_error_px":float(np.linalg.norm(desired[i]-current[i])),
                            "linearized_after_error_px":float(np.linalg.norm(desired[i]-predicted[i]))})
    return {"method":"joint-bounded-rbf-orthographic-fit-v2", "applied":False, "certified":False,
            "object_id":obj["id"], "source_crop":analysis["scene_box"], "proposed_recipe":proposed,
            "landmark_diagnostics":diagnostics,
            "before_rms_px":float(np.sqrt(np.mean(np.sum((desired-current)**2, axis=1)))),
            "linearized_after_rms_px":float(np.sqrt(np.mean(np.sum((desired-predicted)**2, axis=1)))),
            "no_op":not best_handles, "prediction_space":"joint field BEFORE whole-mesh renormalization",
            "warnings":["Correspondences are caller supplied; no automatic feature detection is performed.",
                        "Whole-mesh normalization can also move untouched regions; these predictions do not include it.",
                        "Rebuild and recapture. Reject regressions in silhouettes, fine detail, topology or other regions."],
            "next_action":"Generate the proposed recipe only when no_op=false; compare a fresh capture before accepting."}
