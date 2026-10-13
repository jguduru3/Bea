/* Wake word listener ("Bea", "Hey Bea"). Runs in a hidden window.
 *
 * Everything happens on this computer: the Vosk speech engine is limited to a tiny grammar
 * (see wake-match.js) and nothing is sent anywhere. When the wake word is heard, the main process
 * tells Bea to start listening, and Bea records the actual request with the normal microphone flow
 * (Sarvam speech-to-text). Bea pauses this listener while she records or speaks, so her own voice
 * can't wake her.
 */
(async () => {
  'use strict';
  const pom = window.pom;
  const COOLDOWN_MS = 2500;
  const AUTO_RESUME_MS = 20000; // in case Bea never sends "resume"
  let model = null, ctx = null, stream = null, node = null, rec = null;
  let paused = false, resumeTimer = 0, lastWake = 0;

  function setPaused(p) {
    paused = p;
    clearTimeout(resumeTimer);
    if (p) resumeTimer = setTimeout(() => setPaused(false), AUTO_RESUME_MS);
    pom.wakeState(p ? 'paused' : 'listening', p ? 'Paused while Bea talks or listens' : 'Listening for "Bea"');
  }

  async function start() {
    pom.wakeState('loading', 'Loading the wake word model…');
    if (!model) model = await Vosk.createModel('bea://app/wake-model.tar.gz');
    if (!(await pom.ensureMic())) throw new Error('Microphone permission was denied');
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
    ctx = new AudioContext();
    rec = new model.KaldiRecognizer(ctx.sampleRate, JSON.stringify(WakeMatch.GRAMMAR));
    rec.setWords(true);
    rec.on('result', (m) => {
      if (paused || !WakeMatch.isWake(m.result)) return;
      const t = Date.now();
      if (t - lastWake < COOLDOWN_MS) return;
      lastWake = t;
      setPaused(true);
      pom.wakeDetected();
    });
    // ScriptProcessorNode is deprecated but is what vosk-browser accepts and works everywhere Electron runs.
    node = ctx.createScriptProcessor(4096, 1, 1);
    node.onaudioprocess = (e) => { if (!paused) { try { rec.acceptWaveform(e.inputBuffer); } catch { /* skip a bad buffer */ } } };
    ctx.createMediaStreamSource(stream).connect(node);
    node.connect(ctx.destination); // the node only runs when connected; it outputs silence
    stream.getAudioTracks().forEach((tr) => { tr.onended = () => restart('The microphone was disconnected'); });
    pom.wakeState('listening', 'Listening for "Bea"');
  }

  function stopAudio() {
    try { node && node.disconnect(); } catch {}
    try { rec && rec.remove(); } catch {}
    stream && stream.getTracks().forEach((tr) => tr.stop());
    ctx && ctx.close().catch(() => {});
    node = rec = stream = ctx = null;
  }

  let restarting = false;
  async function restart(why) {
    if (restarting) return;
    restarting = true;
    stopAudio();
    pom.wakeState('loading', why || 'Restarting…');
    await new Promise((r) => setTimeout(r, 1500));
    try { await start(); } catch (e) { pom.wakeState('error', e.message || String(e)); }
    restarting = false;
  }

  pom.onWakeControl((c) => setPaused(!!c.paused));
  navigator.mediaDevices.addEventListener('devicechange', () => restart('Microphone changed'));
  try { await start(); } catch (e) { pom.wakeState('error', e.message || String(e)); }
})();
