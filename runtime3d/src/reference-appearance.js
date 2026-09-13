/** Appearance is independent of geometry/sampling budgets. No Three.js side effects. */
export const REFERENCE_LIGHTING_DEFAULTS = Object.freeze({
  sky_color: '#ffffff', ground_color: '#808080', hemisphere_intensity: 1.0,
  sun_color: '#ffffff', sun_intensity: 2.0,
  sun_position: Object.freeze([-8, 14, 8]), sun_target: Object.freeze([0, 0, 0]),
  exposure: 1.0, tone_mapping: 'aces',
});

export function resolveAppearance(plan, analysis) {
  const mode = plan.appearance_mode ?? 'legacy';
  if (!['legacy', 'reference'].includes(mode)) throw new Error('Unknown appearance mode');
  const reference = mode === 'reference';
  const sourceAspect = analysis.scene_box[2] / analysis.scene_box[3];
  if (!Number.isFinite(sourceAspect) || sourceAspect <= 0) throw new Error('Invalid reference aspect ratio');
  const lighting = {...REFERENCE_LIGHTING_DEFAULTS, ...(plan.reference_lighting ?? {})};
  // Return copies: callers must never mutate defaults or the signed scene plan.
  lighting.sun_position = [...lighting.sun_position];
  lighting.sun_target = [...lighting.sun_target];
  return {mode, reference, stylized: !reference && plan.render_quality === 'showcase',
    framingAspect: reference ? sourceAspect : 1.45, lighting};
}

export function referenceLightState(lighting, dusk = false) {
  // Dusk is an explicitly requested inferred variation, not source evidence.
  return {...lighting, sun_color: dusk ? '#a8c4ef' : lighting.sun_color,
    sun_intensity: lighting.sun_intensity * (dusk ? 0.25 : 1),
    hemisphere_intensity: lighting.hemisphere_intensity * (dusk ? 0.45 : 1),
    variant: dusk ? 'inferred-dusk' : 'reference'};
}
