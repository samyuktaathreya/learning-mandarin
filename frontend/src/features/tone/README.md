# Tone feature

Real-time Mandarin tone feedback, fully in the browser. No backend call, and the result arrives about 350 ms after the user stops speaking.

## Install

```bash
npm i pitchy
npm i -D vitest   # for the tests in __tests__/
```

## Use

```tsx
import { ToneRecorder, CalibrationPrompt, useSpeakerRange } from '@/features/tone';

function PinyinDrill() {
  const { range, setRange } = useSpeakerRange();   // add a per-user key if browsers are shared
  const [calibrating, setCalibrating] = useState(!range);

  if (calibrating)
    return <CalibrationPrompt onComplete={(r) => { setRange(r); setCalibrating(false); }}
                              onCancel={() => setCalibrating(false)} />;

  return (
    <ToneRecorder
      syllable="ma"
      expectedTone={2}
      speakerRange={range}
      onResult={(result) => saveAttempt(result)}     // your API call; nothing waits on it
      onRequestCalibration={() => setCalibrating(true)}
    />
  );
}
```

To build your own UI, use `useToneRecorder` together with `PitchCanvas`.

### Words (several syllables)

```tsx
<ToneRecorder
  syllables={[{ syllable: 'cao', tone: 3 }, { syllable: 'mei', tone: 2 }]}
  speakerRange={range}
  onResult={(result) => saveAttempt(result)}   // a ToneSequenceResult
/>
```

`analyzeTones` finds the whole utterance, splits it into syllables, and grades each syllable with the same pipeline as `analyzeTone`. A boundary goes at a break in the pitch (a pause, or an unvoiced consonant like the "c" in cǎoméi). Failing that, it goes at a dip in loudness (a voiced consonant like the "m" in māma), and the dip itself is left out of both syllables. Failing both, the take is split evenly. Tone 5 (neutral) syllables are split off but not graded. Use `spokenTone` to grade a syllable against a tone other than the written one (3-3 sandhi: `{ syllable: 'ni', tone: 3, spokenTone: 2 }`). Multi-syllable takes wait longer for silence (`TONE_CONFIG.sequence.silenceMs`), so a pause between syllables doesn't end the take.

## Layout

| Folder | What's in it |
|---|---|
| `audio/` | Mic, band-pass filters, AudioWorklet, McLeod pitch detection (pitchy) |
| `analysis/` | Pure functions: noise checks, segmentation, normalization, classification, the live tracker |
| `hooks/` | `useToneRecorder`, `useSpeakerRange` |
| `components/` | `ToneRecorder`, `CalibrationPrompt`, `PitchCanvas` |
| `config.ts` | Every tunable threshold |

## Notes

- **Bundler:** the worklet loads via `new URL('./pitch-worklet.js', import.meta.url)`, which works in Vite and webpack 5. If your bundler doesn't copy it, put `pitch-worklet.js` in `public/` and change the URL in `audio/createAudioGraph.ts` to `'/pitch-worklet.js'`.
- **Styling:** `ToneRecorder.module.css` exposes CSS variables (`--tone-accent`, `--tone-good`, `--tone-band`, ...). Override them on a parent to match your theme.
- **Mic:** requires HTTPS (or localhost). One mic stream is shared by every recorder on the page and released when the last one unmounts.
- **Tuning:** the thresholds were tuned on synthetic contours. Before trusting the numbers, run real recordings (for example, the Tone Perfect dataset) through `analyzeTone`.
