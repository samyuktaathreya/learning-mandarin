/*
 * shared/speech/mic.js
 *
 * One shared microphone for speaking practice. Opening a mic takes a moment
 * (permission check, device start-up), so it's opened when a speaking question
 * appears rather than when Record is tapped. It's closed on other questions:
 * an open mic can make playback quieter on phones and drops Bluetooth
 * headphones to call quality, which would hurt listening questions.
 *
 *   setMicWanted(true|false)    open or close it (useMic does this for you)
 *   startCapture({ onChunk })   start a take; resolves to { stop }
 *     onChunk(int16Array)       each ~100 ms of 16 kHz mono PCM, live, as it's recorded
 *                               (Phase 4 feeds these straight into Azure)
 *     stop()                    resolves to { pcm, wav, durationMs } once every sample
 *                               has arrived, or null if the take was cut off
 *                               (the mic was closed mid-take)
 */
import { pcmToWavBlob } from './wav';

export const SAMPLE_RATE = 16000;

const WORKLET_URL = `${import.meta.env.BASE_URL}pcm-capture-worklet.js`;
const END_TIMEOUT_MS = 500; // don't wait forever if the worklet never answers
const CONSTRAINTS = {
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
};

let mic = null;                // { stream, ctx, node } while open
let wanted = false;
let lastError = null;
let queue = Promise.resolve(); // opens and closes run one at a time, in order
let take = null;               // the take in progress: { id, chunks, onChunk, finish }
let nextTakeId = 1;

async function open() {
    const stream = await navigator.mediaDevices.getUserMedia(CONSTRAINTS);
    let ctx;
    try {
        ctx = new AudioContext();
        await ctx.audioWorklet.addModule(WORKLET_URL);
        const node = new AudioWorkletNode(ctx, 'pcm-capture');
        ctx.createMediaStreamSource(stream).connect(node);
        node.connect(ctx.destination); // the node outputs silence; being connected keeps it running
        node.port.onmessage = onWorkletMessage;
        mic = { stream, ctx, node };
        lastError = null;
    } catch (err) {
        stream.getTracks().forEach((t) => t.stop());
        ctx?.close().catch(() => {});
        throw err;
    }
}

async function close() {
    const m = mic;
    mic = null;
    take?.finish(false); // a take still in progress is cut off
    m.node.port.onmessage = null;
    m.stream.getTracks().forEach((t) => t.stop());
    await m.ctx.close().catch(() => {});
}

// Brings the mic in line with `wanted`. Reads `wanted` when it runs, not when
// it's queued, so a quick close-then-open (one speaking question to the next)
// leaves the mic open instead of restarting it.
function sync() {
    queue = queue.then(async () => {
        try {
            if (wanted && !mic) await open();
            else if (!wanted && mic) await close();
        } catch (err) {
            lastError = err;
            console.error('[mic] could not open the microphone', err);
        }
    });
    return queue;
}

export function setMicWanted(on) {
    wanted = on;
    sync();
}

function onWorkletMessage(e) {
    const { type, id } = e.data;
    if (!take || take.id !== id) return; // late message from an old take
    if (type === 'chunk') {
        const pcm = new Int16Array(e.data.pcm);
        take.chunks.push(pcm);
        try {
            take.onChunk?.(pcm);
        } catch (err) {
            console.error('[mic] onChunk failed', err);
        }
    } else if (type === 'end') {
        take.finish(true);
    }
}

function concat(chunks) {
    const out = new Int16Array(chunks.reduce((n, c) => n + c.length, 0));
    let offset = 0;
    for (const c of chunks) {
        out.set(c, offset);
        offset += c.length;
    }
    return out;
}

export async function startCapture({ onChunk } = {}) {
    // Normally already open (useMic opened it when the question appeared).
    // If opening failed then, e.g. the permission prompt was dismissed, this
    // tap retries it, and a tap is what Safari wants before granting audio.
    wanted = true;
    await sync();
    if (!mic) throw lastError ?? new Error('Microphone unavailable');

    // Browsers keep audio suspended until the page has been interacted with.
    // The Record tap counts, so resuming here always works.
    if (mic.ctx.state !== 'running') await mic.ctx.resume();

    take?.finish(false); // shouldn't happen, but never run two takes at once

    let resolveEnd;
    const ended = new Promise((resolve) => { resolveEnd = resolve; });
    const current = {
        id: nextTakeId++,
        chunks: [],
        onChunk,
        finish(ok) {
            if (take === current) take = null;
            resolveEnd(ok);
        },
    };
    take = current;
    mic.node.port.postMessage({ type: 'start', id: current.id });

    return {
        async stop() {
            if (take === current) mic?.node.port.postMessage({ type: 'stop', id: current.id });
            const timedOut = new Promise((resolve) => setTimeout(() => resolve(true), END_TIMEOUT_MS));
            const ok = await Promise.race([ended, timedOut]);
            current.finish(ok);
            if (!ok) return null;
            const pcm = concat(current.chunks);
            return {
                pcm,
                wav: pcmToWavBlob(pcm, SAMPLE_RATE),
                durationMs: Math.round((pcm.length / SAMPLE_RATE) * 1000),
            };
        },
    };
}