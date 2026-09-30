export const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x));

/** Hz → semitones relative to 55 Hz. Semitones are how we perceive pitch distance. */
export const hzToSt = (hz: number) => 12 * Math.log2(hz / 55);
export const stToHz = (st: number) => 55 * Math.pow(2, st / 12);

export function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN;
}

export function percentile(xs: number[], p: number): number {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const idx = clamp((p / 100) * (s.length - 1), 0, s.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  return s[lo] + (s[hi] - s[lo]) * (idx - lo);
}

export const median = (xs: number[]) => percentile(xs, 50);

/** Soft threshold: 0.5 at `center`, approaching 1 above it and 0 below. */
export function sigmoid(x: number, p: { center: number; width: number }): number {
  return 1 / (1 + Math.exp(-(x - p.center) / p.width));
}

export function medianFilter(xs: number[], window: number): number[] {
  const half = Math.floor(window / 2);
  return xs.map((_, i) =>
    median(xs.slice(Math.max(0, i - half), Math.min(xs.length, i + half + 1))),
  );
}

/** Fill nulls by linear interpolation; hold the nearest value at the edges. */
export function fillGaps(values: (number | null)[]): number[] {
  const known = values.flatMap((v, i) => (v === null ? [] : [i]));
  if (!known.length) return values.map(() => NaN);
  return values.map((v, i) => {
    if (v !== null) return v;
    const next = known.find((k) => k > i);
    let prev: number | undefined;
    for (const k of known) if (k < i) prev = k;
    if (prev === undefined) return values[next!] as number;
    if (next === undefined) return values[prev] as number;
    const a = values[prev] as number;
    const b = values[next] as number;
    return a + ((b - a) * (i - prev)) / (next - prev);
  });
}

/** Linearly resample an evenly spaced series to exactly n points. */
export function resample(xs: number[], n: number): number[] {
  if (xs.length === 1) return Array(n).fill(xs[0]);
  return Array.from({ length: n }, (_, i) => {
    const pos = (i / (n - 1)) * (xs.length - 1);
    const lo = Math.floor(pos);
    const hi = Math.min(lo + 1, xs.length - 1);
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo);
  });
}
