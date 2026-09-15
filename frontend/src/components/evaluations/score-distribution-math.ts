// Scores are 0–1 fractions throughout, same contract as `scorePct`.

/** Equal-width bins over [0, 1]; a score of exactly 1 lands in the last bin. */
export function histogramBins(values: number[], binCount = 10): number[] {
  const bins: number[] = new Array<number>(binCount).fill(0);
  for (const v of values) {
    const i = Math.min(binCount - 1, Math.max(0, Math.floor(v * binCount)));
    bins[i] += 1;
  }
  return bins;
}

export function quantileSorted(sorted: number[], q: number): number {
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

export interface BoxStats {
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
  mean: number;
  n: number;
}

export function boxStats(values: number[]): BoxStats | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mean = sorted.reduce((a, b) => a + b, 0) / sorted.length;
  return {
    max: sorted[sorted.length - 1],
    mean,
    median: quantileSorted(sorted, 0.5),
    min: sorted[0],
    n: sorted.length,
    q1: quantileSorted(sorted, 0.25),
    q3: quantileSorted(sorted, 0.75),
  };
}

/**
 * Gaussian KDE over [0, 1], normalized to a peak of 1 (panels compare shapes,
 * not areas). Bandwidth is Silverman with spread = min(sd, IQR/1.34); the
 * [0.02, 0.08] clamp keeps one repeated value visible and stops bimodal 0/1
 * data from smearing into a slab. Kernel mass is reflected at both edges,
 * otherwise data near 100% loses half its mass and renders as a clipped ramp.
 */
export function kdeCurve(values: number[], points = 61): number[] {
  if (values.length === 0) return [];
  const n = values.length;
  const mean = values.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / n);
  const sorted = [...values].sort((a, b) => a - b);
  const iqr = quantileSorted(sorted, 0.75) - quantileSorted(sorted, 0.25);
  const spread = iqr > 0 ? Math.min(sd, iqr / 1.34) : sd;
  const bandwidth = Math.min(0.08, Math.max(0.02, 1.06 * spread * n ** -0.2));
  const curve: number[] = [];
  for (let i = 0; i < points; i++) {
    const x = i / (points - 1);
    let density = 0;
    for (const v of values) {
      // Kernel plus its reflections at the 0 and 1 edges.
      for (const m of [v, -v, 2 - v]) {
        const z = (x - m) / bandwidth;
        density += Math.exp(-0.5 * z * z);
      }
    }
    curve.push(density);
  }
  const peak = Math.max(...curve);
  return peak > 0 ? curve.map((c) => c / peak) : curve;
}
