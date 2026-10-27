// Voice input — transcribes speech into the message textarea via Web Speech API.
// No backend required; uses SpeechRecognition (Chrome/Edge) or webkitSpeechRecognition.

let recognition  = null;
let isRecording  = false;
let textPrefix   = '';   // textarea content captured at recording start
let finalChunk   = '';   // accumulated final transcript segments this session

export function initVoiceInput() {
  const btn   = document.getElementById('mic-btn');
  const input = document.getElementById('user-input');
  if (!btn || !input) return;

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    btn.title    = 'Voice input not supported in this browser';
    btn.disabled = true;
    btn.classList.add('unsupported');
    return;
  }

  recognition              = new SR();
  recognition.continuous   = true;   // keep listening until manually stopped
  recognition.interimResults = true; // show words as they're spoken

  // ── Handlers ──────────────────────────────────────────────────────────────

  recognition.onstart = () => {
    isRecording = true;
    btn.classList.add('recording');
    btn.title = 'Stop recording';
  };

  recognition.onresult = e => {
    let interim = '';

    for (let i = e.resultIndex; i < e.results.length; i++) {
      const segment = e.results[i][0].transcript;
      if (e.results[i].isFinal) {
        // Add a space between final segments if needed
        if (finalChunk && !/\s$/.test(finalChunk)) finalChunk += ' ';
        finalChunk += segment;
      } else {
        interim += segment;
      }
    }

    // Show prefix + finalised text + live interim in the textarea
    input.value = textPrefix + finalChunk + interim;
    input.dispatchEvent(new Event('input'));
  };

  recognition.onend = () => {
    // Commit: strip trailing interim, keep only finalised text
    input.value = (textPrefix + finalChunk).trimEnd();
    input.dispatchEvent(new Event('input'));
    _resetState(btn);
  };

  recognition.onerror = e => {
    // 'aborted' fires when we call recognition.stop() ourselves — not an error
    if (e.error !== 'aborted') console.warn('Speech recognition error:', e.error);
    // onend commits the final text before resetting the session.
  };

  // ── Click to toggle ────────────────────────────────────────────────────────

  btn.addEventListener('click', () => {
    if (isRecording) {
      recognition.stop();   // triggers onend → _resetState
    } else {
      // Capture whatever is already in the textarea as a prefix so we append
      textPrefix = input.value;
      if (textPrefix && !/\s$/.test(textPrefix)) textPrefix += ' ';
      finalChunk = '';
      isRecording = true;
      try {
        recognition.start();
      } catch (error) {
        _resetState(btn);
        console.warn('Speech recognition could not start:', error);
      }
    }
  });
}

function _resetState(btn) {
  isRecording = false;
  finalChunk  = '';
  textPrefix  = '';
  btn.classList.remove('recording');
  btn.title = 'Voice input';
}