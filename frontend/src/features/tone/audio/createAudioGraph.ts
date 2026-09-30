import { TONE_CONFIG } from '../config';
import type { RawWindow } from './pitchDetector';

export type MicErrorCode = 'PERMISSION_DENIED' | 'NO_MIC' | 'UNSUPPORTED' | 'UNKNOWN';

export class MicError extends Error {
  constructor(public code: MicErrorCode, message: string) {
    super(message);
  }
}

export const MIC_ERROR_MESSAGES: Record<MicErrorCode, string> = {
  PERMISSION_DENIED: 'Microphone access is blocked. Allow it in your browser settings and try again.',
  NO_MIC: "We couldn't find a microphone.",
  UNSUPPORTED: "This browser doesn't support live audio analysis. Try a recent Chrome, Edge, Firefox or Safari.",
  UNKNOWN: "Couldn't start the microphone. Try again.",
};

export interface AudioGraph {
  sampleRate: number;
  windowSize: number;
  /** listen to raw windows; windows are only produced while at least one listener exists */
  subscribe(listener: (w: RawWindow) => void): () => void;
  /** browsers suspend audio until a user gesture; call from a click handler */
  resume(): Promise<void>;
}

/*
 * Mic → band-pass (70 Hz–1.5 kHz) → worklet input 0 (pitch)
 * Mic ────────────────────────────→ worklet input 1 (clipping)
 * worklet → muted gain → destination   (keeps the graph pulled in every browser)
 */
async function buildGraph(): Promise<AudioGraph & { close(): Promise<void> }> {
  if (!navigator.mediaDevices?.getUserMedia || typeof AudioWorkletNode === 'undefined') {
    throw new MicError('UNSUPPORTED', MIC_ERROR_MESSAGES.UNSUPPORTED);
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        // AGC off: it rescales loudness, which would break the noise-floor measurement
        autoGainControl: false,
      },
    });
  } catch (e) {
    const name = (e as DOMException)?.name;
    if (name === 'NotAllowedError' || name === 'SecurityError')
      throw new MicError('PERMISSION_DENIED', MIC_ERROR_MESSAGES.PERMISSION_DENIED);
    if (name === 'NotFoundError' || name === 'OverconstrainedError')
      throw new MicError('NO_MIC', MIC_ERROR_MESSAGES.NO_MIC);
    throw new MicError('UNKNOWN', MIC_ERROR_MESSAGES.UNKNOWN);
  }

  const context = new AudioContext();
  const { windowSize, hopMs } = TONE_CONFIG.detector;
  const hopSize = Math.round((context.sampleRate * hopMs) / 1000);

  try {
    await context.audioWorklet.addModule(new URL('./pitch-worklet.js', import.meta.url));
  } catch {
    stream.getTracks().forEach((t) => t.stop());
    await context.close();
    throw new MicError('UNKNOWN', MIC_ERROR_MESSAGES.UNKNOWN);
  }

  const source = context.createMediaStreamSource(stream);
  const highpass = new BiquadFilterNode(context, { type: 'highpass', frequency: TONE_CONFIG.audio.highpassHz });
  const lowpass = new BiquadFilterNode(context, { type: 'lowpass', frequency: TONE_CONFIG.audio.lowpassHz });
  const node = new AudioWorkletNode(context, 'tone-pitch-window', {
    numberOfInputs: 2,
    numberOfOutputs: 1,
    channelCount: 1,
    channelCountMode: 'explicit',
    processorOptions: { windowSize, hopSize },
  });
  const mute = new GainNode(context, { gain: 0 });

  source.connect(highpass).connect(lowpass).connect(node, 0, 0);
  source.connect(node, 0, 1);
  node.connect(mute).connect(context.destination);

  const listeners = new Set<(w: RawWindow) => void>();
  node.port.onmessage = (e: MessageEvent<RawWindow>) => listeners.forEach((l) => l(e.data));

  return {
    sampleRate: context.sampleRate,
    windowSize,
    subscribe(listener) {
      listeners.add(listener);
      node.port.postMessage({ type: 'active', value: true });
      return () => {
        listeners.delete(listener);
        if (!listeners.size) node.port.postMessage({ type: 'active', value: false });
      };
    },
    async resume() {
      if (context.state !== 'running') await context.resume();
    },
    async close() {
      node.port.postMessage({ type: 'dispose' });
      stream.getTracks().forEach((t) => t.stop());
      await context.close();
    },
  };
}

/*
 * One mic + audio graph shared by every recorder on the page. Kept open between
 * takes (so each take starts instantly) and closed when the last user releases it.
 */
let shared: Promise<Awaited<ReturnType<typeof buildGraph>>> | null = null;
let users = 0;

export async function acquireAudioGraph(): Promise<AudioGraph> {
  users++;
  if (!shared) shared = buildGraph();
  try {
    return await shared;
  } catch (e) {
    users--;
    shared = null;
    throw e;
  }
}

export function releaseAudioGraph(): void {
  users = Math.max(0, users - 1);
  if (users === 0 && shared) {
    const closing = shared;
    shared = null;
    closing.then((g) => g.close()).catch(() => {});
  }
}
