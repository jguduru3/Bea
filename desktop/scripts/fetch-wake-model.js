#!/usr/bin/env node
// Downloads the offline speech model used for the "Hey Bea" wake word (Vosk small English,
// Apache-2.0, ~41 MB) into desktop/resources/ and checks it against a known SHA-256.
// Runs on Windows, macOS and Linux:  npm run fetch-wake-model
'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const URL = 'https://ccoreilly.github.io/vosk-browser/models/vosk-model-small-en-us-0.15.tar.gz';
const SHA256 = 'f0b24bb92a48ca575b6a96500d6b543f0f079c573dfe85bbe16001fc0404e1d8';
const OUT = path.join(__dirname, '..', 'resources', 'wake-model.tar.gz');

const hash = (buf) => crypto.createHash('sha256').update(buf).digest('hex');

(async () => {
  if (fs.existsSync(OUT) && hash(fs.readFileSync(OUT)) === SHA256) { console.log('Wake word model already present.'); return; }
  console.log('Downloading the wake word model (~41 MB)…');
  const res = await fetch(URL);
  if (!res.ok) throw new Error(`Download failed: HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  if (hash(buf) !== SHA256) throw new Error('The downloaded model does not match the expected checksum; not saving it.');
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, buf);
  console.log('Saved', OUT);
})().catch((e) => { console.error(e.message); process.exit(1); });
