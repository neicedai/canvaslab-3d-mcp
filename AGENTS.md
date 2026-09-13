# CanvasLab 3D: source-faithful reconstruction

This repository is the independent 3D MCP prototype (`server3d`, `runtime3d`,
`scripts3d`). The former 2D image-to-web instructions do not apply here. Do not
call nonexistent 2D reconstruction tools or use a full reference-image plane as
a substitute for a three-dimensional scene.

## Reference workflow

- Inspect the original image at native resolution. Retain its digest and crop;
  never downsample merely to fit a tool call. Measured and inferred information
  must remain distinguishable, especially on occluded/back-facing surfaces.
- Read the runtime status and strict scene/component contracts before proposing
  a plan. Save native-coordinate source annotations, validate the plan, build
  from its returned ID, and capture that exact immutable build.
- For NEW reference-faithful plans, explicitly set `appearance_mode: "reference"`.
  Keep `render_quality` independent: `showcase` increases budgets/sampling but
  must not authorize invented color grading, vegetation, material textures,
  environment lighting or decorative geometry. See
  `docs/reference-fidelity-foundation.md` for the first-stage contract.
- Omitted appearance mode means `legacy` for backward compatibility. Migrating
  an existing scene changes appearance and crop framing; recalibrate and capture
  again rather than mixing old screenshots or audits with the new mode.
- In reference mode, author bounded `reference_lighting` from evidence where
  possible. Defaults are only a neutral starting point, not a recovered light
  field. Dusk is an inferred variation, not reference evidence.
- Correct camera/composition first, then silhouettes/occlusion, proportions,
  internal structure, materials and lighting. Increasing template detail or
  triangle count is not proof that any of those differences were corrected.
- Both camera fitters remain advisory and orthographic. Preserve the current
  appearance mode and native crop when fitting. Apply a proposal only via a new
  validated plan, then rebuild and recapture; do not edit generated scene JS.
- Review the source and fresh original-view capture together, plus close-ups
  and alternate views. Retain per-object differences and executable next steps.
  A lower image error must not be achieved with foreground reference billboards
  or by changing source annotations to fit a generated model.

## Asset and execution boundaries

- Only managed recipes and registered immutable components are accepted. Do not
  enable arbitrary Python/shell scripts, untrusted .blend uploads, external
  texture URLs or general-purpose model imports to bypass the asset contract.
- The texture foundation accepts tightly bounded embedded PNG base-color maps
  with verified float UVs. It does NOT yet unwrap or bake reference pixels, infer
  geometry from an image, or reconstruct hidden surfaces automatically.
- Preserve content hashes, plan revisions, idempotency, capture signatures,
  budgets, provenance and no-production-certificate limitations. Higher texture
  or geometry resolution does not remove those requirements.
- Server/runtime changes require a new runtime build and process restart. Never
  relabel old generated artifacts as having been built with new code.

## Verification and changes

- Work on a separate branch and review changes before merging. Preserve unrelated
  user modifications and immutable generated outputs.
- Run backend tests with `python -m unittest discover -s server3d/tests` and
  runtime tests with `npm test` from `runtime3d`; run `npm run build` there before
  a real capture. Record exactly which tests ran and any unavailable dependency.
- Test reference/legacy modes at standard/showcase quality, texture decode
  failures, source-aspect camera calibration, day/dusk/reset, diagnostics and
  movement. Validate a real textured component in the pinned Three.js runtime.
- Report unit-test, browser, Blender and source-visual evidence separately.
  Synthetic fixtures and successful builds are not reconstruction acceptance.
  Do not claim full completion while major source-visible differences remain.
