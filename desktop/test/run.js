// Runs every *.test.js in this folder with node:test. Works on any Node version
// (Node 20's test runner doesn't expand glob patterns).
const fs = require('fs');
const path = require('path');
for (const f of fs.readdirSync(__dirname).filter((n) => n.endsWith('.test.js')).sort()) require(path.join(__dirname, f));
