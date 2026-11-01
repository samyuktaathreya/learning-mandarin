import React, { useState, useRef } from 'react';
import { RealtimeAgent, RealtimeSession } from "@openai/agents/realtime";
import { ClickableText } from '../../features/characters';
import { API_BASE_URL } from '../../shared/config';
import { apiFetch } from '../../shared/api/client';

const hasChinese = (str) => /[一-鿿]/.test(str);
// True if the transcript has any letters/digits, i.e. isn't empty, "..." or just punctuation.
const hasWords = (str) => /[\p{L}\p{N}]/u.test(str || '');

// Tutor turn length is controlled by the prompt only (see services.py). We
// used to cancel over-long turns here, but response.cancel also discards
// audio that's been generated and not yet played -- and the transcript only
// runs a few characters ahead of playback -- so any mid-turn cancel chopped
// off words the student was meant to hear.

// Keep the mic muted briefly after the tutor stops, so the tail of her audio
// (still echoing out of the speakers) isn't picked up as you speaking.
const UNMUTE_DELAY_MS = 500;

// Thinking pauses. Beginners stop mid-sentence to think ("我学…… 计算机").
// SILENCE_MS: quiet needed before the server closes a speech segment.
// REPLY_GRACE_MS: after a segment is transcribed, wait this long before asking
// the tutor to reply. If the student starts talking again in that window, the
// reply is held, and all their segments get answered together in one turn.
// Total wait after the student stops is roughly SILENCE_MS + ~0.4s
// transcription + REPLY_GRACE_MS. Raise these if it still interrupts.
const SILENCE_MS = 1500;
const REPLY_GRACE_MS = 1500;

// Conversation length, counted in the student's turns (not minutes, since
// beginners pause to think). After this many turns the tutor wraps up and the
// summary screen appears. "Keep talking" allows EXTRA_TURNS more each time.
const WRAP_UP_AFTER_TURNS = 12;
const EXTRA_TURNS = 8;

// Hidden instructions sent as system messages. They're never shown in the chat:
// history_updated only renders user/assistant messages.
const WRAP_UP_NOTE = `(Note for the tutor; the student cannot see this.)
The conversation is ending now. In your next turn, reply briefly to what the
student just said, then say goodbye naturally, e.g. 我要走了，下次再聊！
Do not ask a question.`;

const CONTINUE_NOTE = `(Note for the tutor; the student cannot see this.)
The student wants to keep talking. Continue the conversation naturally: pick a
new thread related to today's topic and ask one simple question. Don't mention
that you said goodbye.`;

const addSystemNote = (session, text) => {
  session.transport.sendEvent({
    type: 'conversation.item.create',
    item: { type: 'message', role: 'system', content: [{ type: 'input_text', text }] },
  });
};

// ---------------- debug log (written to logs/voice_practice.log on the server) ----------------
// Events are buffered and sent in batches, tagged with the session id the
// backend returns, so one conversation can be grepped out of the log.
const LOG_FLUSH_MS = 3000;
// High-frequency events that would flood the log without helping debugging.
const NOISY_EVENTS = new Set([
  'response.output_audio_transcript.delta',
  'response.audio_transcript.delta',
  'response.output_audio.delta',
  'response.audio.delta',
  'response.output_text.delta',
  'response.text.delta',
  'conversation.item.input_audio_transcription.delta',
  'input_audio_buffer.append',
]);

// response.done carries the whole response; keep only what helps debugging.
const summarizeTransportEvent = (event) => {
  if (event.type === 'response.done') {
    const r = event.response || {};
    return {
      response_id: r.id,
      status: r.status,                 // completed | cancelled | failed | incomplete
      status_details: r.status_details, // WHY it was cancelled/failed -- key for debugging
      transcript: (r.output || [])
        .flatMap(item => item.content || [])
        .map(c => c.transcript || c.text || '')
        .join(' ')
        .trim(),
      usage: r.usage,
    };
  }
  return event; // backend truncates anything very long
};

export default function MandarinVoicePractice() {
  const [isConnected, setIsConnected] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]);
  // null while chatting; an object when the end-of-conversation summary is showing
  const [summary, setSummary] = useState(null);
  // Claude's review of the transcript: { status: 'loading' | 'done' | 'error', data }
  const [feedback, setFeedback] = useState(null);
  const feedbackRequestRef = useRef(0); // ignore responses from an older summary

  // Track which tutor message transcripts have been revealed by the user
  const [revealedIds, setRevealedIds] = useState(new Set());

  const localStreamRef = useRef(null);

  // Mic is muted while the tutor is speaking. Refs because the session's event
  // handlers are set up once and would otherwise see stale state.
  const [tutorSpeaking, setTutorSpeaking] = useState(false);
  const tutorSpeakingRef = useRef(false);
  const pausedRef = useRef(false);
  const unmuteTimerRef = useRef(null);

  // Conversation-length tracking (refs for the same stale-closure reason).
  const sessionRef = useRef(null);
  const messagesRef = useRef([]);
  const targetWordsRef = useRef([]);
  const userTurnsRef = useRef(0);
  const wrapAtRef = useRef(WRAP_UP_AFTER_TURNS);
  const wrappingUpRef = useRef(false);     // wrap-up note sent, waiting for the goodbye
  const goodbyeStartedRef = useRef(false); // the goodbye audio has started playing
  const summaryOpenRef = useRef(false);    // keeps the mic muted while the summary shows
  const replyTimerRef = useRef(null);      // pending "tutor, reply now" after a grace period
  const awaitingReplyRef = useRef(false);  // student has said something the tutor hasn't answered
  const studentSpeakingRef = useRef(false); // between speech_started and speech_stopped

  // Debug log buffer.
  const voiceSessionIdRef = useRef(null);
  const logBufferRef = useRef([]);
  const logTimerRef = useRef(null);

  const logEvent = (type, data) => {
    if (!voiceSessionIdRef.current) return;
    logBufferRef.current.push({ t: new Date().toISOString(), type, data });
  };

  const flushLogs = async () => {
    const sessionId = voiceSessionIdRef.current;
    const events = logBufferRef.current.splice(0);
    if (!sessionId || events.length === 0) return;
    try {
      await apiFetch(`${API_BASE_URL}/api/voice-session/log`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, events }),
        keepalive: true, // lets the last batch send even if the page is closing
      });
    } catch (err) {
      console.warn("Failed to send voice log:", err);
    }
  };

  // Mute through here so every mute change is logged with the reason.
  const setMuted = (sess, muted, reason) => {
    sess?.mute(muted);
    logEvent('client.mute', { muted, reason });
  };

  const toggleReveal = (id) => {
    setRevealedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  // Which target words the student actually SAID (in their own transcripts).
  const buildSummary = () => {
    const userText = messagesRef.current
      .filter(m => m.role === 'user' && !m.pending)
      .map(m => m.text)
      .join('');
    const targets = targetWordsRef.current;
    return {
      turns: userTurnsRef.current,
      said: targets.filter(w => userText.includes(w.hanzi)),
      notYet: targets.filter(w => !userText.includes(w.hanzi)),
    };
  };

  // Send the finished transcript to the backend, which asks Claude to find the
  // student's mistakes (and to separate out likely mis-transcriptions).
  const requestFeedback = async () => {
    const transcript = messagesRef.current
      .filter(m => !m.pending && hasWords(m.text))
      .map(m => ({ role: m.role, text: m.text }));
    if (!transcript.some(m => m.role === 'user')) {
      setFeedback(null);
      return;
    }
    const requestId = ++feedbackRequestRef.current;
    setFeedback({ status: 'loading' });
    logEvent('client.feedback_requested', { lines: transcript.length });
    try {
      const res = await apiFetch(`${API_BASE_URL}/api/voice-session/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript, session_id: voiceSessionIdRef.current }),
      });
      if (!res.ok) throw new Error(`feedback ${res.status}: ${await res.text()}`);
      const data = await res.json();
      logEvent('client.feedback_received', { mistakes: data.mistakes?.length ?? 0 });
      if (requestId === feedbackRequestRef.current) setFeedback({ status: 'done', data });
    } catch (err) {
      console.error("Failed to get feedback:", err);
      logEvent('client.feedback_failed', { error: String(err) });
      if (requestId === feedbackRequestRef.current) setFeedback({ status: 'error' });
    }
    flushLogs();
  };

  const showSummaryScreen = () => {
    const s = buildSummary();
    logEvent('client.summary_shown', {
      turns: s.turns,
      said: s.said.map(w => w.hanzi),
      not_yet: s.notYet.map(w => w.hanzi),
    });
    setSummary(s);
    requestFeedback();
  };

  const openSummary = () => {
    summaryOpenRef.current = true;
    setMuted(sessionRef.current, true, 'summary open');
    showSummaryScreen();
  };

  const resetConversationTracking = () => {
    userTurnsRef.current = 0;
    clearTimeout(replyTimerRef.current);
    replyTimerRef.current = null;
    awaitingReplyRef.current = false;
    studentSpeakingRef.current = false;
    wrapAtRef.current = WRAP_UP_AFTER_TURNS;
    wrappingUpRef.current = false;
    goodbyeStartedRef.current = false;
    summaryOpenRef.current = false;
    messagesRef.current = [];
  };

  const startPracticeSession = async () => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/api/voice-session`, { method: 'POST' });
      if (!res.ok) throw new Error(`voice-session ${res.status}: ${await res.text()}`);
      // Instructions are built server-side from the student's known vocab.
      const { session_id, client_secret, instructions, target_words } = await res.json();

      voiceSessionIdRef.current = session_id;
      logBufferRef.current = [];
      clearInterval(logTimerRef.current);
      logTimerRef.current = setInterval(flushLogs, LOG_FLUSH_MS);
      logEvent('client.session_starting', { target_words: (target_words || []).length });

      targetWordsRef.current = target_words || [];
      resetConversationTracking();
      setSummary(null);
      setFeedback(null);
      feedbackRequestRef.current++; // drop any feedback still loading from last time
      setMessages([]);

      const agent = new RealtimeAgent({
        name: "MandarinTutor",
        instructions,
      });

      const newSession = new RealtimeSession(agent, {
        model: "gpt-realtime-2.1",
        config: {
          audio: {
            input: {
              transcription: { model: "gpt-realtime-whisper", language: "zh" },
              turnDetection: {
                // server_vad (not semantic_vad) so we can set an explicit silence window.
                // This only closes a speech SEGMENT; whether the tutor replies is decided
                // by the REPLY_GRACE_MS wait below.
                type: "server_vad",
                silenceDurationMs: SILENCE_MS,
                // Let the tutor finish its turn instead of stopping when you start talking.
                interruptResponse: false,
                // Don't reply automatically when you stop talking; we trigger the reply
                // ourselves once the transcript comes back with actual words in it.
                createResponse: false,
              },
            },
          },
        }
      });
      sessionRef.current = newSession;

      newSession.on('history_updated', (history) => {
        const formattedMessages = history
          .filter(item => item.type === 'message' && (item.role === 'user' || item.role === 'assistant'))
          .map(item => {
            const content = item.content || [];
            const text = content.map(c => c.text || c.transcript || '').join(' ').trim();

            // A user's audio has transcript === null until Whisper finishes transcribing it.
            const pending = item.role === 'user'
              && content.some(c => c.type === 'input_audio' && c.transcript == null);

            return {
              id: item.itemId,
              role: item.role,
              text: text || "...",
              pending,
            };
          });

        messagesRef.current = formattedMessages;
        setMessages(formattedMessages);
      });

      const setTutorTalking = (talking) => {
        clearTimeout(unmuteTimerRef.current);
        if (talking) {
          tutorSpeakingRef.current = true;
          setTutorSpeaking(true);
          setMuted(newSession, true, 'tutor speaking');
        } else {
          unmuteTimerRef.current = setTimeout(() => {
            tutorSpeakingRef.current = false;
            setTutorSpeaking(false);
            const muted = pausedRef.current || summaryOpenRef.current;
            setMuted(newSession, muted, muted
              ? `tutor done, still muted (${pausedRef.current ? 'paused' : 'summary open'})`
              : 'tutor done, your turn');
          }, UNMUTE_DELAY_MS);
        }
      };

      // Ask the tutor to reply to everything the student has said since her
      // last turn. One call here = one student turn, however many speech
      // segments (thinking pauses) it was made of.
      const sendReply = () => {
        replyTimerRef.current = null;
        if (!awaitingReplyRef.current) return;
        // Still mid-sentence (they resumed before the last segment finished
        // transcribing). Wait: this segment's transcript will re-arm the reply.
        if (studentSpeakingRef.current) {
          logEvent('client.reply_held', { reason: 'student still speaking' });
          return;
        }
        awaitingReplyRef.current = false;

        userTurnsRef.current += 1;
        logEvent('client.user_turn', { turn: userTurnsRef.current });
        if (!wrappingUpRef.current && userTurnsRef.current >= wrapAtRef.current) {
          // Sent before requestResponse, so the tutor's next reply is the goodbye.
          wrappingUpRef.current = true;
          addSystemNote(newSession, WRAP_UP_NOTE);
          logEvent('client.wrap_up_sent', { turn: userTurnsRef.current });
        }
        newSession.transport.requestResponse();
      };

      const scheduleReply = () => {
        clearTimeout(replyTimerRef.current);
        replyTimerRef.current = setTimeout(sendReply, REPLY_GRACE_MS);
      };

      // WebRTC-only events that track actual playback in the browser, not just
      // when the server finished generating audio (which happens much earlier).
      newSession.on('transport_event', (event) => {
        // Log every realtime event except the high-frequency streaming ones.
        // Server 'error' events land here too -- the most useful lines when debugging.
        if (!NOISY_EVENTS.has(event.type)) {
          logEvent(`rt.${event.type}`, summarizeTransportEvent(event));
        }

        // The student started talking again: hold any pending reply so a
        // thinking pause doesn't get answered as if it were a full sentence.
        if (event.type === 'input_audio_buffer.speech_started') {
          studentSpeakingRef.current = true;
          if (replyTimerRef.current) {
            clearTimeout(replyTimerRef.current);
            replyTimerRef.current = null;
            logEvent('client.reply_held', { reason: 'student kept talking' });
          }
        }
        if (event.type === 'input_audio_buffer.speech_stopped') {
          studentSpeakingRef.current = false;
        }

        if (event.type === 'conversation.item.input_audio_transcription.completed') {
          if (hasWords(event.transcript)) {
            awaitingReplyRef.current = true;
            logEvent('client.user_speech', { transcript: event.transcript });
            scheduleReply();
          } else {
            // Noise/silence got committed as a turn: drop it and keep waiting for you.
            logEvent('client.noise_dropped', { item_id: event.item_id, transcript: event.transcript });
            newSession.transport.sendEvent({ type: 'conversation.item.delete', item_id: event.item_id });
            // A breath or cough may have held the reply; re-arm it if words are waiting.
            if (awaitingReplyRef.current) scheduleReply();
          }
        }
        if (event.type === 'output_audio_buffer.started') {
          setTutorTalking(true);
          if (wrappingUpRef.current) goodbyeStartedRef.current = true;
        }
        if (event.type === 'output_audio_buffer.stopped' || event.type === 'output_audio_buffer.cleared') {
          setTutorTalking(false);
          // The goodbye finished playing (or was hushed): show the summary.
          if (goodbyeStartedRef.current && !summaryOpenRef.current) openSummary();
        }
      });

      // SDK-level errors (connection drops, failed sends) that aren't transport events.
      newSession.on('error', (err) => {
        console.error("Realtime session error:", err);
        logEvent('client.sdk_error', { error: String(err?.error?.message || err?.message || err) });
        flushLogs();
      });

      await newSession.connect({ apiKey: client_secret });
      logEvent('client.connected');

      navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
        localStreamRef.current = stream;
      }).catch(err => {
        console.error("Could not grab mic for muting:", err);
        logEvent('client.mic_error', { error: String(err) });
      });

      setSession(newSession);
      setIsConnected(true);
      setIsPaused(false);
      pausedRef.current = false;
      setRevealedIds(new Set()); // Reset revealed states for new session
    } catch (err) {
      console.error("Failed to connect voice session:", err);
      // Only reaches the log if /api/voice-session itself succeeded (we need its
      // session id); a failure before that is in the server log as session.*_failed.
      logEvent('client.connect_failed', { error: String(err) });
      flushLogs();
    }
  };

  const endPracticeSession = async ({ showSummary = true } = {}) => {
    logEvent('client.session_ending', { turns: userTurnsRef.current, show_summary: showSummary });
    clearTimeout(replyTimerRef.current);
    replyTimerRef.current = null;
    awaitingReplyRef.current = false;
    // Ending early still gets a summary, as long as the student said something.
    if (showSummary && !summaryOpenRef.current && userTurnsRef.current > 0) {
      showSummaryScreen();
    }

    const current = sessionRef.current;
    if (current) {
      try {
        await current.close();
      } catch (err) {
        console.error("Error closing session:", err);
        logEvent('client.close_error', { error: String(err) });
      }
    }

    // Final batch. The interval stops; the session id stays set so a feedback
    // result arriving after this is still logged (requestFeedback flushes it).
    clearInterval(logTimerRef.current);
    flushLogs();

    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach(track => track.stop());
      localStreamRef.current = null;
    }

    clearTimeout(unmuteTimerRef.current);
    tutorSpeakingRef.current = false;
    setTutorSpeaking(false);

    sessionRef.current = null;
    setSession(null);
    setIsConnected(false);
    setIsPaused(false);
  };

  // From the summary: carry on in the same session for EXTRA_TURNS more turns.
  const keepTalking = () => {
    const current = sessionRef.current;
    if (!current) return;
    summaryOpenRef.current = false;
    wrappingUpRef.current = false;
    goodbyeStartedRef.current = false;
    wrapAtRef.current = userTurnsRef.current + EXTRA_TURNS;
    setSummary(null);
    setFeedback(null);
    feedbackRequestRef.current++;
    logEvent('client.keep_talking', { turns: userTurnsRef.current, next_wrap_at: wrapAtRef.current });
    clearInterval(logTimerRef.current);
    logTimerRef.current = setInterval(flushLogs, LOG_FLUSH_MS);
    // The tutor speaks first; the mic unmutes when she finishes, as usual.
    addSystemNote(current, CONTINUE_NOTE);
    current.transport.requestResponse();
  };

  // From the summary: close the session but leave the summary on screen.
  const finishConversation = () => endPracticeSession({ showSummary: false });

  const togglePause = () => {
    const nextPausedState = !isPaused;
    setIsPaused(nextPausedState);
    pausedRef.current = nextPausedState;
    logEvent(nextPausedState ? 'client.paused' : 'client.resumed');

    if (nextPausedState && session) {
      try {
        session.interrupt();
      } catch (err) {
        console.warn("Interrupt failed:", err);
        logEvent('client.interrupt_error', { error: String(err) });
      }
    }

    if (session) {
      // Unpausing mid-sentence shouldn't unmute you over the tutor.
      setMuted(session, nextPausedState || tutorSpeakingRef.current || summaryOpenRef.current,
        nextPausedState ? 'paused' : 'resumed');
    }

    if (localStreamRef.current) {
      localStreamRef.current.getAudioTracks().forEach((track) => {
        track.enabled = !nextPausedState;
      });
    }
  };

  // Dedicated function to immediately hush the AI without pausing mic
  const stopAgentSpeaking = () => {
    if (session) {
      logEvent('client.hush_pressed');
      session.interrupt();
    }
  };

  const wordChip = (w, done) => (
    <li
      key={w.hanzi}
      style={{
        listStyle: 'none', padding: '6px 10px', borderRadius: '6px',
        background: done ? '#dcfce7' : '#f3f4f6', color: done ? '#166534' : '#4b5563',
      }}
    >
      <strong>{w.hanzi}</strong> {w.pinyin} <span style={{ opacity: 0.8 }}>· {w.english}</span>
    </li>
  );

  const feedbackData = feedback?.status === 'done' ? feedback.data : null;
  const feedbackSection = feedback && (
    <div style={{ borderTop: '1px solid #e5e7eb', marginTop: '12px', paddingTop: '12px' }}>
      <h4 style={{ margin: '0 0 8px' }}>📝 Feedback</h4>

      {feedback.status === 'loading' && <p style={{ color: '#999' }}>Reviewing your conversation…</p>}
      {feedback.status === 'error' && <p style={{ color: '#999' }}>Couldn't load feedback this time.</p>}

      {feedbackData && (
        <>
          {feedbackData.praise && <p>👍 {feedbackData.praise}</p>}

          {feedbackData.mistakes.length === 0 ? (
            <p>No mistakes to point out. Great job!</p>
          ) : (
            feedbackData.mistakes.map((m, i) => (
              <div key={i} style={{ background: '#fef9c3', borderRadius: '8px', padding: '10px', marginBottom: '8px' }}>
                <div style={{ color: '#991b1b', textDecoration: 'line-through' }}>{m.you_said}</div>
                <div style={{ color: '#166534', fontWeight: 600 }}>
                  ✓ <ClickableText text={m.correction} tags={[]} />
                </div>
                <div style={{ color: '#6b7280', fontSize: '0.9em' }}>{m.correction_pinyin}</div>
                <div style={{ marginTop: '4px' }}>{m.explanation}</div>
              </div>
            ))
          )}

          {feedbackData.possible_mishearings?.length > 0 && (
            <details style={{ marginTop: '8px' }}>
              <summary style={{ cursor: 'pointer', color: '#6b7280' }}>
                🎧 {feedbackData.possible_mishearings.length} line(s) may have been mis-heard
              </summary>
              {feedbackData.possible_mishearings.map((h, i) => (
                <div key={i} style={{ padding: '6px 0', fontSize: '0.95em' }}>
                  Heard “{h.transcribed}”, you probably said <strong>{h.probably_said}</strong>.
                  {h.pronunciation_tip && <div style={{ color: '#6b7280' }}>Tip: {h.pronunciation_tip}</div>}
                </div>
              ))}
            </details>
          )}
        </>
      )}
    </div>
  );

  const summaryCard = summary && (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: '10px', padding: '16px', margin: '12px 0' }}>
      <h3 style={{ marginTop: 0 }}>🎉 Nice conversation!</h3>
      <p>You spoke <strong>{summary.turns}</strong> times in Chinese.</p>

      {summary.said.length + summary.notYet.length > 0 && (
        <>
          <p>
            You used <strong>{summary.said.length}</strong> of{' '}
            {summary.said.length + summary.notYet.length} words from this lesson:
          </p>
          <ul style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', padding: 0 }}>
            {summary.said.map(w => wordChip(w, true))}
          </ul>
          {summary.notYet.length > 0 && (
            <>
              <p style={{ marginBottom: '4px' }}>Try these next time:</p>
              <ul style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', padding: 0 }}>
                {summary.notYet.map(w => wordChip(w, false))}
              </ul>
            </>
          )}
        </>
      )}

      {feedbackSection}

      <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', marginTop: '12px' }}>
        {isConnected ? (
          <>
            <button onClick={keepTalking} className="btn-start">Keep talking</button>
            <button onClick={finishConversation} className="btn-end">Done</button>
          </>
        ) : (
          <button onClick={startPracticeSession} className="btn-start">New conversation</button>
        )}
      </div>
    </div>
  );

  return (
    <div className="voice-practice-container">
      <h2>🗣️ Mandarin Conversation Practice</h2>

      {!isConnected && !summary && (
        <button onClick={startPracticeSession} className="btn-start">
          Start Speaking
        </button>
      )}

      {isConnected && !summary && (
        <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', flexWrap: 'wrap' }}>
          <button
            onClick={togglePause}
            className="btn-start"
            style={{ backgroundColor: isPaused ? '#f59e0b' : '#3b82f6' }}
          >
            {isPaused ? "▶️ Resume" : "⏸️ Pause"}
          </button>

          {/* Panic Button: Immediately silences the AI if it talks too long */}
          <button
            onClick={stopAgentSpeaking}
            style={{ backgroundColor: '#ef4444', color: 'white', border: 'none', padding: '10px 16px', borderRadius: '6px', cursor: 'pointer' }}
          >
            🤫 Hush AI
          </button>

          <button onClick={() => endPracticeSession()} className="btn-end">
            End Conversation
          </button>
        </div>
      )}

      {summaryCard}

      {isConnected && !isPaused && !summary && (
        <p style={{ textAlign: 'center', color: tutorSpeaking ? '#999' : '#16a34a', margin: '8px 0' }}>
          {tutorSpeaking ? '🔇 Tutor is speaking… wait for her to finish' : '🎙️ Your turn to speak'}
        </p>
      )}

      {/* Keep the transcript visible under the summary so they can review it. */}
      {isConnected || (summary && messages.length > 0) ? (
        <div className="chat-box" style={{ opacity: isPaused ? 0.6 : 1, transition: 'opacity 0.2s' }}>
          {messages.length === 0 && (
            <p className="msg-bubble assistant" style={{ alignSelf: 'center' }}>
              Listening... say something in Mandarin!
            </p>
          )}
          {messages.map((msg) => (
            <div key={msg.id} className={`msg-row ${msg.role}`}>
              <p className={`msg-bubble ${msg.role}`}>
                <strong>{msg.role === 'user' ? 'You: ' : 'Tutor: '}</strong>
                {msg.role === 'user' ? (
                  msg.pending ? (
                    <em style={{ color: '#999' }}>Transcribing…</em>
                  ) : hasChinese(msg.text) ? <ClickableText text={msg.text} tags={[]} /> : msg.text
                ) : revealedIds.has(msg.id) ? (
                  hasChinese(msg.text) ? <ClickableText text={msg.text} tags={[]} /> : msg.text
                ) : (
                  <button
                    onClick={() => toggleReveal(msg.id)}
                    style={{ background: 'none', border: '1px dashed #999', borderRadius: '4px', padding: '4px 8px', cursor: 'pointer', color: '#666' }}
                  >
                    👁️ Tap to see text
                  </button>
                )}
              </p>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}