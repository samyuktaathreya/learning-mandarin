import type { FinishReason } from '../analysis/live';
import type { PitchFrame, SpeakerRange, Tone, ToneResult, ToneTrace } from '../types';

/**
 * Debug log for the tone pipeline. Each take is formatted as plain text and
 * POSTed to the Vite dev server, which appends it to frontend/logs/tone-debug.log
 * (see vite-plugins/toneLogPlugin.js). Dev only: a no-op in production builds.
 * Set VITE_TONE_LOG=0 in frontend/.env.local to turn it off in dev.
 */
export const TONE_LOG_ENDPOINT = '/__tone-log';

const enabled = import.meta.env.DEV && import.meta.env.VITE_TONE_LOG !== '0';

export interface ToneAttemptLog {
  /** what was being recorded, e.g. the syllable or "calibration" */
  label?: string;
  /** anything identifying where the take came from (e.g. the question object), logged as JSON */
  context?: unknown;
  expectedTone: Tone;
  speakerRange: SpeakerRange | null;
  finishReason: FinishReason | null;
  preRollMs: number;
  frames: PitchFrame[];
  result: ToneResult;
  trace: ToneTrace;
}

/** Log one analyzed take: inputs, every intermediate value, the result, and all frames. */
export function logToneAttempt(a: ToneAttemptLog) {
  if (!enabled) return;
  const r = a.result;
  const { kinds, rawSt, contourSt, ...scalars } = a.trace;
  const lines = [
    header(`TAKE ${a.label ?? ''} · expected tone ${a.expectedTone}`),
    `speakerRange:   ${a.speakerRange ? `${a.speakerRange.lowHz.toFixed(1)}–${a.speakerRange.highHz.toFixed(1)} Hz` : 'uncalibrated'}`,
    `finishReason:   ${a.finishReason}`,
    `preRollMs:      ${a.preRollMs}`,
    `frames:         ${a.frames.length} (${a.frames.length ? `${fmt(a.frames[0].t)}–${fmt(a.frames.at(-1)!.t)} ms` : 'none'})`,
    ...(a.context !== undefined ? ['', '-- context --', json(a.context)] : []),
    '',
    '-- result --',
    r.ok
      ? `detected ${r.detectedTone} · ${r.isCorrect ? 'CORRECT' : 'WRONG'} · score ${r.score} · ${r.voiceQuality}` +
        ` · calibrated=${r.calibrated} · segment ${fmt(r.segment.startMs)}–${fmt(r.segment.endMs)} ms`
      : `FAILED: ${r.error} (${r.message})`,
    ...(r.ok
      ? [
          `toneScores:     ${Object.entries(r.toneScores).map(([t, s]) => `T${t}=${fmt(s, 3)}`).join('  ')}`,
          `hint:           ${r.hint ?? '-'}`,
          `features:       ${kv(r.features)}`,
        ]
      : []),
    '',
    '-- trace --',
    ...Object.entries(scalars).map(([k, v]) => `${k.padEnd(16)}${stringify(v)}`),
    ...(Array.isArray(rawSt) ? [`${'rawSt'.padEnd(16)}${rawSt.map((v) => (v === null ? '·' : fmt(v))).join(' ')}`] : []),
    ...(Array.isArray(contourSt) ? [`${'contourSt'.padEnd(16)}${contourSt.map((v) => fmt(v)).join(' ')}`] : []),
    '',
    '-- frames --',
    frameTable(a.frames, Array.isArray(kinds) ? kinds : null, r.ok ? r.segment : null),
  ];
  send(lines.join('\n'));
}

/** Log anything else worth keeping (e.g. a calibration result). */
export function logToneEvent(title: string, data: Record<string, unknown>) {
  if (!enabled) return;
  send([header(title), ...Object.entries(data).map(([k, v]) => `${k.padEnd(16)}${stringify(v)}`)].join('\n'));
}

function send(text: string) {
  fetch(TONE_LOG_ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: text + '\n\n' }).catch(
    () => {}, // logging must never break recording
  );
}

const json = (v: unknown) => {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v); // e.g. circular
  }
};

const header = (title: string) => `==== ${new Date().toISOString()} · ${title} ====`;

const fmt = (v: number, digits = 1) => (Number.isFinite(v) && !Number.isInteger(v) ? v.toFixed(digits) : String(v));

const stringify = (v: unknown) =>
  typeof v === 'number' ? fmt(v, 3) : v && typeof v === 'object' ? kv(v as Record<string, unknown>) : String(v);

const kv = (o: Record<string, unknown>) =>
  Object.entries(o)
    .map(([k, v]) => `${k}=${typeof v === 'number' ? fmt(v, 2) : v}`)
    .join(' ');

function frameTable(frames: PitchFrame[], kinds: unknown[] | null, seg: { startMs: number; endMs: number } | null) {
  const rows = ['     t(ms)      hz  clarity   rmsDb   peak  kind      seg'];
  for (let i = 0; i < frames.length; i++) {
    const f = frames[i];
    const inSeg = seg && f.t >= seg.startMs && f.t < seg.endMs ? '*' : '';
    rows.push(
      [
        fmt(f.t).padStart(10),
        (f.hz === null ? '-' : fmt(f.hz)).padStart(7),
        fmt(f.clarity, 3).padStart(8),
        fmt(f.rmsDb).padStart(7),
        fmt(f.peak, 3).padStart(6),
        ' ' + String(kinds?.[i] ?? '').padEnd(9),
        inSeg,
      ].join(' '),
    );
  }
  return rows.join('\n');
}
