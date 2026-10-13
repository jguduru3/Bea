/* Wake word rules, shared by the wake listener and its tests.
 *
 * The recogniser is limited to a tiny grammar, so everything else someone says comes back as
 * "[unk]". "Bea" sounds exactly like "be", so a bare "Bea" only counts when it is the whole
 * utterance (said on its own, with a pause), while "hey / hi / okay Bea" may start a sentence
 * ("Hey Bea, what's on my calendar?").
 */
(function (global) {
  'use strict';

  const NAMES = ['bea'];
  const GREETINGS = ['hey', 'hi', 'okay', 'ok', 'hello'];
  const GRAMMAR = [...NAMES, ...GREETINGS.flatMap((g) => NAMES.map((n) => `${g} ${n}`)), '[unk]'];
  const MIN_CONF = 0.6;
  const NOISE_CONF = 0.8; // an [unk] below this is background noise, not more speech

  /** result: a Vosk final result ({text, result: [{word, conf}]}). Returns true for a wake word. */
  function isWake(result) {
    const words = (result && result.result) || [];
    let toks = ((result && result.text) || '').toLowerCase().trim().split(/\s+/).filter(Boolean);
    let confs = toks.map((t, i) => (words[i] && words[i].conf !== undefined ? words[i].conf : 1));
    // Drop low-confidence [unk] (noise), so "Bea" plus a cough still counts but "be careful" doesn't.
    const keep = toks.map((t, i) => !(t === '[unk]' && confs[i] < NOISE_CONF));
    toks = toks.filter((_, i) => keep[i]);
    confs = confs.filter((_, i) => keep[i]);
    if (!toks.length) return false;
    const confOk = (i) => confs[i] >= MIN_CONF;
    if (toks.length === 1) return NAMES.includes(toks[0]) && confOk(0);
    // the greeting only needs to be roughly right; the name is what matters
    if (GREETINGS.includes(toks[0]) && NAMES.includes(toks[1])) return confs[0] >= 0.45 && confOk(1);
    return false;
  }

  const api = { GRAMMAR, isWake, MIN_CONF };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else global.WakeMatch = api;
})(typeof window !== 'undefined' ? window : globalThis);
