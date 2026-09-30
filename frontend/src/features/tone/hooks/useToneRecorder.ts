import { useCallback, useEffect, useRef, useState } from 'react';
import { analyzeTone } from '../analysis/analyze';
import { LiveTracker } from '../analysis/live';
import {
  acquireAudioGraph,
  MIC_ERROR_MESSAGES,
  MicError,
  releaseAudioGraph,
  type AudioGraph,
} from '../audio/createAudioGraph';
import { createFrameAnalyzer } from '../audio/pitchDetector';
import type { PitchFrame, SpeakerRange, Tone, ToneResult } from '../types';

export type RecorderStatus =
  | 'idle'
  | 'starting' // asking for the mic / starting audio
  | 'preroll' // measuring background noise, "get ready"
  | 'listening' // "speak now"
  | 'done'
  | 'error'; // mic problem (recording problems come back as a ToneResult instead)

export interface UseToneRecorderOptions {
  expectedTone: Tone;
  speakerRange?: SpeakerRange | null;
  onResult?: (result: ToneResult, frames: PitchFrame[]) => void;
}

/**
 * Records one syllable at a time and analyzes it the moment the user stops.
 *
 * Live pitch data goes into `liveRef` (not state), so 100 updates/s never
 * re-render React; PitchCanvas reads it on each animation frame. State only
 * changes on phase transitions.
 */
export function useToneRecorder({ expectedTone, speakerRange = null, onResult }: UseToneRecorderOptions) {
  const [status, setStatus] = useState<RecorderStatus>('idle');
  const [result, setResult] = useState<ToneResult | null>(null);
  const [micError, setMicError] = useState<string | null>(null);

  const liveRef = useRef<LiveTracker | null>(null);
  const graphRef = useRef<AudioGraph | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const analyzerRef = useRef<ReturnType<typeof createFrameAnalyzer> | null>(null);
  const mountedRef = useRef(true);

  // latest props, readable from the audio callback without re-subscribing
  const propsRef = useRef({ expectedTone, speakerRange, onResult });
  propsRef.current = { expectedTone, speakerRange, onResult };

  const finish = useCallback(() => {
    const tracker = liveRef.current;
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    if (!tracker) return;
    tracker.finish('manual'); // no-op if it already finished on its own

    const { expectedTone: tone, speakerRange: range, onResult: cb } = propsRef.current;
    const r = analyzeTone({
      frames: tracker.frames,
      expectedTone: tone,
      speakerRange: range,
      preRollMs: tracker.preRollMs,
    });
    setResult(r);
    setStatus('done');
    cb?.(r, tracker.frames);
  }, []);

  const start = useCallback(async () => {
    if (unsubscribeRef.current) return; // already recording
    setResult(null);
    setMicError(null);
    setStatus('starting');

    try {
      if (!graphRef.current) {
        const graph = await acquireAudioGraph();
        if (!mountedRef.current) return releaseAudioGraph(); // unmounted while the mic prompt was open
        graphRef.current = graph;
      }
      const graph = graphRef.current;
      await graph.resume(); // must happen inside the click that called start()
      if (!analyzerRef.current) analyzerRef.current = createFrameAnalyzer(graph.windowSize, graph.sampleRate);
    } catch (e) {
      setMicError(e instanceof MicError ? e.message : MIC_ERROR_MESSAGES.UNKNOWN);
      setStatus('error');
      return;
    }

    const { expectedTone: tone, speakerRange: range } = propsRef.current;
    const tracker = new LiveTracker(tone, range ?? null);
    liveRef.current = tracker;
    const analyzer = analyzerRef.current!;
    analyzer.resetClock();

    setStatus('preroll');
    unsubscribeRef.current = graphRef.current!.subscribe((w) => {
      if (liveRef.current !== tracker) return;
      const changed = tracker.push(analyzer.analyze(w));
      if (!changed) return;
      if (tracker.phase === 'listening') setStatus('listening');
      if (tracker.phase === 'finished') finish();
    });
  }, [finish]);

  /** stop early (e.g. a Stop button); the take is analyzed as-is */
  const stop = useCallback(() => {
    if (unsubscribeRef.current) finish();
  }, [finish]);

  const reset = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    liveRef.current = null;
    setResult(null);
    setMicError(null);
    setStatus('idle');
  }, []);

  // release the mic when the component goes away
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
      if (graphRef.current) {
        graphRef.current = null;
        releaseAudioGraph();
      }
    };
  }, []);

  const isRecording = status === 'starting' || status === 'preroll' || status === 'listening';
  return { status, isRecording, result, micError, liveRef, start, stop, reset };
}
