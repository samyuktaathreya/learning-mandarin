import { useCallback, useEffect, useRef, useState } from 'react';
import { checkPhonemes } from './api';

// First one the browser supports wins. Safari only records mp4/aac;
// Chrome/Firefox prefer webm/opus. The backend decodes all of them.
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm'];

function pickMimeType() {
  if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return undefined;
  return MIME_CANDIDATES.find((t) => MediaRecorder.isTypeSupported(t));
}

const IDLE = { status: 'idle', result: null, error: null };

/**
 * Records the same take the tone checker is listening to, then sends it to
 * the backend phoneme checker. Driven entirely by the ToneRecorder's status,
 * so the user still presses one button:
 *
 *   <ToneRecorder onStatusChange={phonemes.onToneStatus} ... />
 *
 * status: 'idle' | 'recording' | 'checking' | 'done' | 'error'
 * result: backend response once status === 'done'
 *
 * The mic stream is opened on the first take and kept open while the
 * component is mounted, so later takes start instantly.
 */
export function usePhonemeCheck(expectedPinyin) {
  const [state, setState] = useState(IDLE);
  const expectedRef = useRef(expectedPinyin);
  expectedRef.current = expectedPinyin;

  const streamRef = useRef(null);
  const takeRef = useRef(null); // Promise<{ id, recorder, stopped } | null> for the take in progress
  const takeId = useRef(0); // bumped on every new take / cancel; stale async work checks it
  const abortRef = useRef(null);

  const ensureStream = async () => {
    if (streamRef.current?.active) return streamRef.current;
    streamRef.current = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    return streamRef.current;
  };

  const stopRecorder = (take) => {
    if (take?.recorder.state !== 'inactive') take?.recorder.stop();
  };

  const cancel = useCallback(() => {
    takeId.current += 1;
    abortRef.current?.abort();
    const pending = takeRef.current;
    takeRef.current = null;
    pending?.then(stopRecorder);
    setState(IDLE);
  }, []);

  const begin = useCallback(() => {
    cancel();
    const id = takeId.current;
    setState({ status: 'recording', result: null, error: null });

    takeRef.current = (async () => {
      const stream = await ensureStream();
      const mimeType = pickMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType, audioBitsPerSecond: 32000 } : undefined);
      const chunks = [];
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      const stopped = new Promise((resolve) => {
        recorder.onstop = () => resolve(new Blob(chunks, { type: recorder.mimeType }));
      });
      recorder.start();
      return { id, recorder, stopped };
    })().catch((err) => {
      console.error('[phoneme check] could not start recording', err);
      if (id === takeId.current) setState({ status: 'error', result: null, error: err });
      return null;
    });
  }, [cancel]);

  const finish = useCallback(async () => {
    const pending = takeRef.current;
    takeRef.current = null;
    const take = pending && (await pending);
    if (!take) return;
    stopRecorder(take);
    const blob = await take.stopped;
    if (take.id !== takeId.current) return;

    setState({ status: 'checking', result: null, error: null });
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const result = await checkPhonemes(blob, expectedRef.current, { signal: controller.signal });
      if (take.id === takeId.current) setState({ status: 'done', result, error: null });
    } catch (err) {
      if (take.id !== takeId.current || err.name === 'AbortError') return;
      console.error('[phoneme check]', err);
      setState({ status: 'error', result: null, error: err });
    }
  }, []);

  /** Pass to <ToneRecorder onStatusChange>. */
  const onToneStatus = useCallback(
    (toneStatus) => {
      if (toneStatus === 'preroll') begin();
      // in case the tone recorder ever skips its preroll
      else if (toneStatus === 'listening' && !takeRef.current) begin();
      else if (toneStatus === 'done') finish();
      else if (toneStatus === 'idle' || toneStatus === 'starting' || toneStatus === 'error') cancel();
    },
    [begin, finish, cancel],
  );

  // New target -> drop anything in flight. Unmount -> release the mic.
  useEffect(() => cancel, [expectedPinyin, cancel]);
  useEffect(
    () => () => {
      cancel();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    },
    [cancel],
  );

  return { ...state, onToneStatus, reset: cancel };
}
