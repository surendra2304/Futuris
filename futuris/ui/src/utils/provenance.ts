import type { Forecast } from '../api/types';

/**
 * Evidence classes whose numbers were measured or computed from persisted records.
 * ``synthetic`` (generated) and ``demo`` (illustrative) are not measured, and neither
 * is a record that carries no class at all.
 */
const MEASURED_CLASSES: ReadonlySet<string> = new Set(['live', 'derived']);

export interface ProvenanceCounts {
  measured: number;
  synthetic: number;
  total: number;
}

export function countProvenance(
  forecasts: readonly Pick<Forecast, 'evidence_class'>[],
): ProvenanceCounts {
  const measured = forecasts.filter((f) => MEASURED_CLASSES.has(f.evidence_class ?? '')).length;
  return { measured, synthetic: forecasts.length - measured, total: forecasts.length };
}
