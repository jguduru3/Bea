// Run with: node --test test/
// Cases are real Vosk outputs recorded from Windows voices (see the developer guide).
const test = require('node:test');
const assert = require('node:assert');
const { isWake, GRAMMAR } = require('../src/renderer/wake-match.js');

const r = (pairs) => ({ text: pairs.map((p) => p[0]).join(' '), result: pairs.map(([word, conf]) => ({ word, conf })) });

test('grammar is tiny and includes the name', () => {
  assert.ok(GRAMMAR.includes('bea') && GRAMMAR.includes('hey bea') && GRAMMAR.includes('[unk]'));
});

test('wakes on the name alone and greeting + name', () => {
  assert.ok(isWake(r([['bea', 0.82], ['[unk]', 0.67]])));    // "Bea" + breath noise
  assert.ok(isWake(r([['bea', 1.0]])));
  assert.ok(isWake(r([['hey', 1], ['bea', 1], ['[unk]', 1]]))); // "Hey Bea, remind me..."
  assert.ok(isWake(r([['hi', 1], ['bea', 1], ['hey', 0.52], ['[unk]', 1]])));
  assert.ok(isWake(r([['okay', 0.5], ['bea', 1], ['[unk]', 0.73]])));
});

test('ignores "be" inside sentences and other speech', () => {
  assert.ok(!isWake(r([['bea', 0.89], ['[unk]', 1.0]])));   // "Be careful with that"
  assert.ok(!isWake(r([['[unk]', 1.0], ['bea', 1.0]])));    // "Let it be"
  assert.ok(!isWake(r([['[unk]', 1.0]])));
  assert.ok(!isWake(r([['bea', 0.4]])));                    // too unsure
  assert.ok(!isWake({ text: '' }));
});
