/*
 * shared/speech/wav.js
 *
 * Wraps raw 16-bit mono PCM in a WAV header, so the browser can play it back
 * ("Hear yourself") and anything that reads audio files can open it.
 */
export function pcmToWavBlob(pcm, sampleRate) {
    const dataBytes = pcm.length * 2;
    const header = new ArrayBuffer(44);
    const v = new DataView(header);
    const text = (offset, s) => {
        for (let i = 0; i < s.length; i++) v.setUint8(offset + i, s.charCodeAt(i));
    };

    text(0, 'RIFF');
    v.setUint32(4, 36 + dataBytes, true);
    text(8, 'WAVE');
    text(12, 'fmt ');
    v.setUint32(16, 16, true);             // fmt chunk size
    v.setUint16(20, 1, true);              // PCM
    v.setUint16(22, 1, true);              // mono
    v.setUint32(24, sampleRate, true);
    v.setUint32(28, sampleRate * 2, true); // bytes per second
    v.setUint16(32, 2, true);              // bytes per sample frame
    v.setUint16(34, 16, true);             // bits per sample
    text(36, 'data');
    v.setUint32(40, dataBytes, true);

    return new Blob([header, pcm], { type: 'audio/wav' });
}