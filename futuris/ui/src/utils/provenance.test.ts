import { describe, expect, it } from 'vitest';
import type { Forecast } from '../api/types';
import { countProvenance } from './provenance';

const record = (evidence_class?: Forecast['evidence_class']) => ({ evidence_class });

describe('countProvenance', () => {
  it('counts live and derived records as measured', () => {
    expect(countProvenance([record('live'), record('derived'), record('live')])).toEqual({
      measured: 3,
      synthetic: 0,
      total: 3,
    });
  });

  it('treats synthetic and demo records as not measured', () => {
    expect(countProvenance([record('synthetic'), record('demo'), record('live')])).toEqual({
      measured: 1,
      synthetic: 2,
      total: 3,
    });
  });

  it('does not count a record without an evidence class as measured', () => {
    expect(countProvenance([record(undefined)])).toEqual({ measured: 0, synthetic: 1, total: 1 });
  });

  it('returns zeros for an empty list', () => {
    expect(countProvenance([])).toEqual({ measured: 0, synthetic: 0, total: 0 });
  });
});
