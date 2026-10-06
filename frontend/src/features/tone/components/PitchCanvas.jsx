import { useEffect, useRef } from 'react';
import { TONE_CONFIG } from '../config';
import { toChaoLevel } from '../analysis/normalize';
import { alignSequenceToTargets, alignToTarget, bandAt, isInBand } from './targetBand';

/*
 * Colours come from CSS custom properties so the canvas follows your theme.
 * Set any of these on the canvas or an ancestor to override.
 */
const COLOR_VARS = {
  grid: ['--tone-grid', 'rgba(128,128,128,0.18)'],
  label: ['--tone-label', 'rgba(128,128,128,0.75)'],
  band: ['--tone-band', 'rgba(99,102,241,0.16)'],
  bandEdge: ['--tone-band-edge', 'rgba(99,102,241,0.45)'],
  good: ['--tone-good', '#16a34a'],
  off: ['--tone-off', '#f59e0b'],
  neutral: ['--tone-trace', '#64748b'],
};

function readColors(el) {
  const cs = getComputedStyle(el);
  return Object.fromEntries(
    Object.entries(COLOR_VARS).map(([k, [v, fallback]]) => [k, cs.getPropertyValue(v).trim() || fallback]),
  );
}

const PAD = { left: 22, right: 10, top: 10, bottom: 10 };
const MAX_GAP_MS = 60; // don't join points across longer gaps (creak / dropouts)

/**
 * Pass `expectedTone` for one syllable, or `expectedTones` (e.g. [3, 2]) for a
 * word: each syllable then gets its own slot with its own target band, and
 * `result` is a ToneSequenceResult. Tone 5 (neutral) slots have no band.
 */
export function PitchCanvas({ expectedTone, expectedTones, status, liveRef, result, height = 180, className }) {
  const canvasRef = useRef(null);
  const tones = expectedTones?.length ? expectedTones : [expectedTone];
  const tonesKey = tones.join(',');
  const propsRef = useRef({ tones, status, result });
  propsRef.current = { tones, status, result };
  const resultShownAt = useRef(0);

  useEffect(() => {
    if (status === 'done') resultShownAt.current = performance.now();
  }, [status, result]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let colors = readColors(canvas);
    let raf = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(canvas.clientWidth * dpr);
      canvas.height = Math.round(canvas.clientHeight * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      colors = readColors(canvas);
    };

    const draw = () => {
      const { tones, status: st, result: res } = propsRef.current;
      const multi = tones.length > 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      const plotW = w - PAD.left - PAD.right;
      const plotH = h - PAD.top - PAD.bottom;
      const y = (level) => PAD.top + ((5.5 - Math.max(0.5, Math.min(5.5, level))) / 5) * plotH;
      ctx.clearRect(0, 0, w, h);

      // grid: Chao levels 1–5
      ctx.font = '10px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (let lvl = 1; lvl <= 5; lvl++) {
        ctx.strokeStyle = colors.grid;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(PAD.left, y(lvl));
        ctx.lineTo(w - PAD.right, y(lvl));
        ctx.stroke();
        ctx.fillStyle = colors.label;
        ctx.fillText(String(lvl), PAD.left / 2, y(lvl));
      }

      // one tone's band drawn between pixel x0 and x1 (u = 0 → 1); neutral tone has none
      const drawBand = (tone, x0, x1, alpha = 1) => {
        if (tone === 5) return;
        const steps = 40;
        ctx.globalAlpha = alpha;
        ctx.fillStyle = colors.band;
        ctx.beginPath();
        for (let i = 0; i <= steps; i++) {
          const u = i / steps;
          ctx.lineTo(x0 + u * (x1 - x0), y(bandAt(tone, u).hi));
        }
        for (let i = steps; i >= 0; i--) {
          const u = i / steps;
          ctx.lineTo(x0 + u * (x1 - x0), y(bandAt(tone, u).lo));
        }
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = colors.bandEdge;
        ctx.setLineDash([5, 5]);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        for (let i = 0; i <= steps; i++) {
          const u = i / steps;
          ctx.lineTo(x0 + u * (x1 - x0), y(bandAt(tone, u).center));
        }
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
      };

      // trace segments, coloured by whether each point is inside the band
      const drawTrace = (pts, alpha = 1) => {
        ctx.globalAlpha = alpha;
        ctx.lineWidth = 4;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        for (let i = 1; i < pts.length; i++) {
          if (pts[i].breakBefore) continue;
          ctx.strokeStyle = pts[i].color;
          ctx.beginPath();
          ctx.moveTo(pts[i - 1].x, pts[i - 1].y);
          ctx.lineTo(pts[i].x, pts[i].y);
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
      };

      // every syllable's band side by side between x0 and x1, split by dotted lines
      const drawBands = (x0, x1, alpha = 1) => {
        const slot = (x1 - x0) / tones.length;
        tones.forEach((tone, i) => drawBand(tone, x0 + i * slot, x0 + (i + 1) * slot, alpha));
        ctx.strokeStyle = colors.grid;
        ctx.setLineDash([2, 4]);
        ctx.lineWidth = 1;
        for (let i = 1; i < tones.length; i++) {
          ctx.beginPath();
          ctx.moveTo(x0 + i * slot, PAD.top);
          ctx.lineTo(x0 + i * slot, h - PAD.bottom);
          ctx.stroke();
        }
        ctx.setLineDash([]);
      };

      // is a point at u (0 → number of syllables) inside its syllable's band?
      const inBands = (u, level) => {
        const i = Math.min(tones.length - 1, Math.floor(u));
        return tones[i] !== 5 && isInBand(tones[i], u - i, level);
      };

      const tracker = liveRef.current;
      const live = st === 'preroll' || st === 'listening';

      if (live && tracker) {
        const { windowMs } = TONE_CONFIG.display;
        const targetMs = multi ? TONE_CONFIG.display.sequenceTargetMs : TONE_CONFIG.display.targetMs;
        const pxPerMs = plotW / windowMs;
        const t0 = tracker.voiceStartMs;
        const latest = tracker.points.at(-1)?.t ?? 0;
        const shift = t0 === null ? 0 : Math.max(0, latest - t0 - windowMs * 0.9);
        const xOf = (tRel) => PAD.left + (tRel - shift) * pxPerMs;

        drawBands(xOf(0), xOf(targetMs * tones.length), t0 === null ? 0.6 : 1);

        // creak strip: raspy frames have no pitch to plot, so show them as a
        // row of dots at the bottom (green when tone 3 is the target)
        if (t0 !== null && tracker.creakTimes.length) {
          ctx.fillStyle = !multi && tones[0] === 3 ? colors.good : colors.neutral;
          for (const t of tracker.creakTimes) {
            ctx.beginPath();
            ctx.arc(xOf(t - t0), y(0.85), 2.5, 0, Math.PI * 2);
            ctx.fill();
          }
        }

        if (t0 !== null && tracker.range) {
          const range = tracker.range;
          const pts = tracker.points.map((p, i) => {
            const tRel = p.t - t0;
            const level = toChaoLevel(p.st, range);
            const u = tRel / targetMs;
            const color = u > tones.length ? colors.neutral : inBands(u, level) ? colors.good : colors.off;
            const breakBefore = i > 0 && p.t - tracker.points[i - 1].t > MAX_GAP_MS;
            return { x: xOf(tRel), y: y(level), color, breakBefore };
          });
          drawTrace(pts);
          const last = pts.at(-1);
          if (last) {
            ctx.fillStyle = last.color;
            ctx.beginPath();
            ctx.arc(last.x, last.y, 6, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      } else if (st === 'done' && res?.ok && multi) {
        // one slot per syllable, each contour stretched across its own slot
        drawBands(PAD.left, PAD.left + plotW);
        const slotW = plotW / tones.length;
        const fade = Math.min(1, (performance.now() - resultShownAt.current) / 250);
        // uncalibrated: shift all syllables together, so their relative heights survive
        const contours = res.calibrated
          ? res.syllables.map((r) => r?.contour ?? [])
          : alignSequenceToTargets(tones, res.syllables.map((r) => r?.contour ?? []));
        res.syllables.forEach((r, i) => {
          if (!r) return; // neutral tone: not graded, nothing drawn
          const x0 = PAD.left + i * slotW;
          if (r.voiceQuality === 'creaky') {
            ctx.globalAlpha = fade;
            ctx.fillStyle = r.isCorrect ? colors.good : colors.off;
            for (let x = x0 + 3; x < x0 + slotW; x += 7) {
              ctx.beginPath();
              ctx.arc(x, y(0.85), 2.5, 0, Math.PI * 2);
              ctx.fill();
            }
            ctx.globalAlpha = 1;
          }
          const pts = contours[i].map((p) => ({
            x: x0 + p.x * slotW,
            y: y(p.level),
            color: r.isCorrect && isInBand(tones[i], p.x, p.level) ? colors.good : colors.off,
          }));
          drawTrace(pts, fade);
        });
        if (fade < 1) raf = requestAnimationFrame(draw);
        return;
      } else if (st === 'done' && res?.ok) {
        // "you vs. target": both stretched to full width
        const tone = tones[0];
        drawBand(tone, PAD.left, PAD.left + plotW);
        const contour = res.calibrated ? res.contour : alignToTarget(tone, res.contour);
        // wrong tone: all amber, so a line that merely crosses the band doesn't look half-right
        const pts = contour.map((p) => ({
          x: PAD.left + p.x * plotW,
          y: y(p.level),
          color: res.isCorrect && isInBand(tone, p.x, p.level) ? colors.good : colors.off,
        }));
        const fade = Math.min(1, (performance.now() - resultShownAt.current) / 250);
        if (res.voiceQuality === 'creaky') {
          // raspy take: the creak has no pitch line, so show it as the bottom strip
          ctx.globalAlpha = fade;
          ctx.fillStyle = res.isCorrect ? colors.good : colors.off;
          for (let x = PAD.left + 3; x < PAD.left + plotW; x += 7) {
            ctx.beginPath();
            ctx.arc(x, y(0.85), 2.5, 0, Math.PI * 2);
            ctx.fill();
          }
          ctx.globalAlpha = 1;
        }
        drawTrace(pts, fade);
        if (fade < 1) raf = requestAnimationFrame(draw);
        return;
      } else {
        // idle / error / failed take: preview the target
        drawBands(PAD.left, PAD.left + plotW, 0.8);
      }

      if (live) raf = requestAnimationFrame(draw);
    };

    resize();
    draw();
    const ro = new ResizeObserver(() => {
      resize();
      draw();
    });
    ro.observe(canvas);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [tonesKey, status, result, liveRef]);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{ width: '100%', height, display: 'block' }}
      role="img"
      aria-label={tones.length > 1 ? `Pitch graph for tones ${tones.join(' ')}` : `Pitch graph for tone ${tones[0]}`}
    />
  );
}