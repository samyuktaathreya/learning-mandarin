import React, { useState, useRef } from 'react';
import { RealtimeAgent, RealtimeSession } from "@openai/agents/realtime";
import { ClickableText } from '../Components/CharacterPopup';
import { API_BASE_URL } from '../config';

const hasChinese = (str) => /[\u4e00-\u9fff]/.test(str);

export default function MandarinVoicePractice() {
  const [isConnected, setIsConnected] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]);
  
  // Track which tutor message transcripts have been revealed by the user
  const [revealedIds, setRevealedIds] = useState(new Set());

  const localStreamRef = useRef(null); 

  const toggleReveal = (id) => {
    setRevealedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const startPracticeSession = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/voice-session`, { method: 'POST' });
      const { client_secret } = await res.json();
      
      const agent = new RealtimeAgent({
        name: "MandarinTutor",
        instructions: `
          You are a monolingual Mandarin language tutor. 
          CRITICAL RULE 1: Speak ONLY in Mandarin Chinese. Never use English.
          Ask follow-up question to keep the conversation going. 
          Never speak more than two sentences at once. 
          
          VOCABULARY CONSTRAINT:
          Only use HSK1 vocabulary!
        `,
      });

      const newSession = new RealtimeSession(agent, {
        model: "gpt-realtime-2.1",
        config: {
          audio: {
            input: {
              transcription: { model: "gpt-realtime-whisper", language: "zh" },
              turnDetection: {
                type: "semantic_vad",
                eagerness: "low",
              },
            },
          },
        }
      });

      newSession.on('history_updated', (history) => {
        const formattedMessages = history
          .filter(item => item.role === 'user' || item.role === 'assistant')
          .map(item => {
            let text = "...";
            if (typeof item.content === 'string') {
              text = item.content;
            } else if (Array.isArray(item.content)) {
              text = item.content.map(c => c.text || c.transcript || '').join(' ');
            } else if (item.formatted?.transcript) {
              text = item.formatted.transcript;
            }

            return {
              id: item.id || Math.random().toString(),
              role: item.role,
              text: text || "..."
            };
          });

        setMessages(formattedMessages);
      });

      await newSession.connect({ apiKey: client_secret });

      navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
        localStreamRef.current = stream;
      }).catch(err => console.error("Could not grab mic for muting:", err));

      setSession(newSession);
      setIsConnected(true);
      setIsPaused(false);
      setRevealedIds(new Set()); // Reset revealed states for new session
    } catch (err) {
      console.error("Failed to connect voice session:", err);
    }
  };

  const endPracticeSession = async () => {
    if (session) {
      try {
        await session.close(); 
      } catch (err) {
        console.error("Error closing session:", err);
      }
    }
    
    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach(track => track.stop());
      localStreamRef.current = null;
    }

    setSession(null);
    setIsConnected(false);
    setIsPaused(false);
  };

  const togglePause = () => {
    const nextPausedState = !isPaused;
    setIsPaused(nextPausedState);

    if (nextPausedState && session) {
      try {
        session.interrupt();
      } catch (err) {
        console.warn("Interrupt failed:", err);
      }
    }

    if (session) {
      session.mute(nextPausedState); // true = muted, false = unmuted
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
      session.interrupt();
    }
  };

  return (
    <div className="voice-practice-container">
      <h2>🗣️ Mandarin Conversation Practice</h2>
      
      {!isConnected ? (
        <button onClick={startPracticeSession} className="btn-start">
          Start Speaking
        </button>
      ) : (
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

          <button onClick={endPracticeSession} className="btn-end">
            End Conversation
          </button>
        </div>
      )}

      {isConnected && (
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
                  hasChinese(msg.text) ? <ClickableText text={msg.text} tags={[]} /> : msg.text
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
      )}
    </div>
  );
}