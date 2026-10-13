/* Pom character rig.
 * The body is a painted plush image with the face removed; every facial feature
 * (eyes, lids, brows, mouth, blush, tears, effects) is drawn live in SVG so the
 * face can track the cursor and blend smoothly between emotions.
 * Coordinates are in the 520 x 600 artwork space.
 */
(function (global) {
  'use strict';

  const ART = { w: 520, h: 600 };
  const EYE = { L: { x: 182, y: 336 }, R: { x: 347, y: 336 }, rx: 39, ry: 47 };
  const MOUTH = { x: 264, y: 412 };
  const CHEEK = { L: { x: 132, y: 404 }, R: { x: 388, y: 404 } };
  const ANT = { x: 278, y: 156 };
  const INK = '#121a2b';

  // ---------------------------------------------------------------- emotions
  // Every emotion is a set of target parameters. Anything left out falls back to BASE.
  const BASE = {
    eyes: 'open', lid: 0.04, lidL: null, lidR: null, tilt: 0, low: 0, eyeScale: 1, wet: 0,
    browOp: 0, browY: 0, browTilt: 0, browAsym: 0,
    mw: 44, mc: 9, mo: 0, wave: 0, skew: 0, mx: 0, tongue: 0,
    blush: 0.8, blushScale: 1, shyLines: 0,
    tears: 0, sweat: 0, vein: 0,
    motion: 'breathe', antenna: 0, tiltBody: 0,
    gazeLock: null, fx: null, fxEvery: 0,
  };

  const EMOTIONS = {
    neutral: {},
    happy: { low: 0.28, mw: 54, mc: 16, mo: 9, blush: 0.75, motion: 'bob', antenna: 'wiggle' },
    joyful: { eyes: 'squeeze', mw: 62, mc: 20, mo: 30, tongue: 1, blush: 0.9, motion: 'bounce', antenna: 'wiggle', fx: 'sparkle', fxEvery: 700 },
    laughing: { eyes: 'arc', mw: 66, mc: 22, mo: 34, tongue: 1, blush: 0.95, motion: 'giggle', antenna: 'wiggle', fx: 'note', fxEvery: 900 },
    love: { eyes: 'heart', mw: 50, mc: 16, mo: 6, blush: 1, blushScale: 1.15, motion: 'sway', antenna: 'wiggle', fx: 'heart', fxEvery: 520 },
    excited: { eyes: 'star', mw: 58, mc: 18, mo: 26, tongue: 0.7, blush: 0.85, motion: 'hop', antenna: 'wiggle', fx: 'sparkle', fxEvery: 420 },
    surprised: { lid: 0, eyeScale: 1.14, browOp: 1, browY: -12, mw: 24, mc: -2, mo: 24, blush: 0.4, motion: 'pop', antenna: 'perk', fx: 'exclaim', fxEvery: 99999 },
    curious: { lid: 0.04, eyeScale: 1.06, browOp: 0.9, browAsym: -9, mw: 26, mc: 5, mo: 4, motion: 'tilt', tiltBody: 7, antenna: 'perk', fx: 'question', fxEvery: 99999 },
    thinking: { lid: 0.22, browOp: 0.9, browAsym: -7, browTilt: -6, mw: 24, mc: -1, mx: 10, skew: 4, gazeLock: { x: 0.65, y: -0.75 }, motion: 'tilt', tiltBody: -5, fx: 'dots', fxEvery: 99999 },
    listening: { lid: 0, eyeScale: 1.06, browOp: 0.6, browY: -6, mw: 22, mc: 2, mo: 7, motion: 'sway', antenna: 'perk', fx: 'wave', fxEvery: 650 },
    talking: { low: 0.12, mw: 46, mc: 10, mo: 4, blush: 0.65, motion: 'bob', antenna: 'wiggle' },
    sad: { lid: 0.32, tilt: -16, wet: 1, browOp: 1, browTilt: -14, browY: -2, mw: 38, mc: -12, blush: 0.3, motion: 'droop', antenna: 'droop' },
    crying: { eyes: 'closed', tears: 1, browOp: 1, browTilt: -18, mw: 46, mc: -14, mo: 16, wave: 1, blush: 0.6, motion: 'sob', antenna: 'droop' },
    angry: { lid: 0.36, tilt: 22, browOp: 1, browTilt: 22, browY: 6, mw: 42, mc: -10, mo: 7, blush: 0.2, vein: 1, motion: 'shake', antenna: 'stiff', fx: 'steam', fxEvery: 700 },
    scared: { lid: 0, eyeScale: 0.86, wet: 1, browOp: 1, browTilt: -16, browY: -8, mw: 40, mc: -4, mo: 10, wave: 1.2, blush: 0.15, sweat: 1, motion: 'shiver', antenna: 'droop' },
    shy: { lid: 0.3, low: 0.15, gazeLock: { x: -0.7, y: 0.65 }, mw: 26, mc: 6, wave: 0.6, blush: 1, blushScale: 1.3, shyLines: 1, motion: 'sway', tiltBody: -4, antenna: 'droop' },
    confused: { lidL: 0.05, lidR: 0.38, browOp: 1, browAsym: -12, browTilt: 8, mw: 34, mc: -2, wave: 1, skew: 6, motion: 'tilt', tiltBody: -8, fx: 'question', fxEvery: 1400 },
    smug: { lid: 0.48, low: 0.12, browOp: 0.8, browAsym: -6, mw: 40, mc: 8, skew: 9, mx: 6, blush: 0.6, motion: 'sway', antenna: 'wiggle' },
    proud: { eyes: 'arc', mw: 52, mc: 16, mo: 4, blush: 0.75, motion: 'puff', antenna: 'perk', fx: 'sparkle', fxEvery: 900 },
    bored: { lid: 0.52, mw: 34, mc: 0, motion: 'breathe', antenna: 'droop' },
    sleepy: { lid: 0.66, mw: 22, mc: 2, mo: 6, motion: 'nod', antenna: 'droop' },
    sleeping: { eyes: 'closed', mw: 16, mc: 0, mo: 9, blush: 0.65, motion: 'snore', antenna: 'droop', fx: 'zzz', fxEvery: 1300 },
    dizzy: { eyes: 'spiral', mw: 40, mc: -4, mo: 12, wave: 1.4, blush: 0.45, motion: 'wobble', antenna: 'wiggle', fx: 'stars', fxEvery: 99999 },
    wink: { lidL: 0.1, lidR: 1, low: 0.2, mw: 50, mc: 16, mo: 8, blush: 0.8, motion: 'bob', antenna: 'wiggle', fx: 'sparkle', fxEvery: 99999 },
    hungry: { lid: 0.2, mw: 30, mc: 6, mo: 14, tongue: 1, skew: -5, motion: 'bob', fx: 'drool', fxEvery: 99999 },
  };

  const NUMERIC = ['lid', 'tilt', 'low', 'eyeScale', 'wet', 'browOp', 'browY', 'browTilt', 'browAsym', 'mw', 'mc', 'mo', 'wave', 'skew', 'mx', 'tongue', 'blush', 'blushScale', 'shyLines', 'tears', 'sweat', 'vein', 'tiltBody'];
  const EYE_MODES = ['open', 'arc', 'closed', 'squeeze', 'heart', 'star', 'spiral'];

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  // ---------------------------------------------------------------- markup
  function eyeMarkup(side, uid) {
    const s = side === 'L' ? 1 : -1; // mirror sign for asymmetric shapes
    return `
    <g class="eye" data-side="${side}" transform="translate(${EYE[side].x} ${EYE[side].y})">
      <g class="brow"><path d="M-19 2 Q0 -8 19 2" stroke="#2a70bb" stroke-width="6" stroke-linecap="round" fill="none"/></g>
      <defs><mask id="${uid}-mask${side}" maskUnits="userSpaceOnUse" x="-120" y="-160" width="240" height="320">
        <rect x="-120" y="-160" width="240" height="320" fill="#fff"/>
        <g class="lidrot"><path class="lid" fill="#000"/></g>
        <path class="lowlid" fill="#000"/>
      </mask></defs>
      <g class="m-open">
        <g mask="url(#${uid}-mask${side})">
          <g class="ball">
            <ellipse rx="${EYE.rx}" ry="${EYE.ry}" fill="url(#${uid}-eyeGrad)"/>
            <ellipse rx="${EYE.rx}" ry="${EYE.ry}" fill="none" stroke="#0a0f1a" stroke-width="2.5"/>
            <g class="shine">
              <circle cx="-11" cy="-17" r="12.5" fill="#fff"/>
              <circle cx="13" cy="14" r="5.5" fill="#fff"/>
              <ellipse class="wet" cx="0" cy="30" rx="24" ry="7" fill="#9fd6ff" opacity="0"/>
            </g>
          </g>
        </g>
        <g class="lidg"><path class="lash" fill="none" stroke="${INK}" stroke-width="3.4" stroke-linecap="round"/></g>
      </g>
      <path class="m-arc" d="M-25 9 Q0 -24 25 9" fill="none" stroke="${INK}" stroke-width="6.5" stroke-linecap="round" opacity="0"/>
      <path class="m-closed" d="M-25 -2 Q0 19 25 -2" fill="none" stroke="${INK}" stroke-width="5.5" stroke-linecap="round" opacity="0"/>
      <path class="m-squeeze" d="${s > 0 ? 'M-19 -17 L17 0 L-19 17' : 'M19 -17 L-17 0 L19 17'}" fill="none" stroke="${INK}" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round" opacity="0"/>
      <g class="m-heart" opacity="0"><g class="pulse">
        <path d="M0 22 C-34 -2 -30 -30 -12 -30 C-4 -30 0 -22 0 -18 C0 -22 4 -30 12 -30 C30 -30 34 -2 0 22 Z" fill="#ff4f7b" stroke="#d62d5b" stroke-width="2"/>
        <ellipse cx="-12" cy="-18" rx="7" ry="5" fill="#fff" opacity=".75"/></g></g>
      <g class="m-star" opacity="0"><g class="spin">
        <path d="M0 -32 L9 -10 L32 -9 L14 6 L20 29 L0 16 L-20 29 L-14 6 L-32 -9 L-9 -10 Z" fill="#ffd23f" stroke="#e79a00" stroke-width="2.5" stroke-linejoin="round"/>
        <circle cx="-6" cy="-8" r="4.5" fill="#fff" opacity=".8"/></g></g>
      <g class="m-spiral" opacity="0"><path class="spin" d="M0 0 m0 -2 a2 2 0 1 1 -2 2 a5 5 0 1 1 5 5 a9 9 0 1 1 -9 -9 a14 14 0 1 1 14 14 a20 20 0 1 1 -20 -20 a27 27 0 1 1 27 27" fill="none" stroke="${INK}" stroke-width="4" stroke-linecap="round"/></g>
      <g class="tear" opacity="0"><path class="stream" d="M${-6 * s} 24 C${-14 * s} 60 ${-4 * s} 90 ${-10 * s} 120" fill="none" stroke="#7cc8ff" stroke-width="8" stroke-linecap="round" opacity=".85"/></g>
    </g>`;
  }

  function buildSVG(uid) {
    return `
<svg class="face" viewBox="0 0 ${ART.w} ${ART.h}" overflow="visible" aria-hidden="true">
  <defs>
    <radialGradient id="${uid}-eyeGrad" cx="45%" cy="35%" r="75%"><stop offset="0" stop-color="#2b3a5c"/><stop offset=".55" stop-color="#0d1424"/><stop offset="1" stop-color="#03060c"/></radialGradient>
    <radialGradient id="${uid}-blushGrad"><stop offset="0" stop-color="#ff7fa3" stop-opacity=".95"/><stop offset=".6" stop-color="#ff8aa9" stop-opacity=".55"/><stop offset="1" stop-color="#ff9ab4" stop-opacity="0"/></radialGradient>
    <clipPath id="${uid}-mouthClip"><path class="mclip"/></clipPath>
  </defs>
  <g class="cheeks">
    <ellipse class="cheek" data-side="L" cx="${CHEEK.L.x}" cy="${CHEEK.L.y}" rx="40" ry="27" fill="url(#${uid}-blushGrad)"/>
    <ellipse class="cheek" data-side="R" cx="${CHEEK.R.x}" cy="${CHEEK.R.y}" rx="40" ry="27" fill="url(#${uid}-blushGrad)"/>
    <g class="shylines" opacity="0" stroke="#ff6f96" stroke-width="3.5" stroke-linecap="round">
      <path d="M${CHEEK.L.x - 20} ${CHEEK.L.y + 6} l12 -14 M${CHEEK.L.x - 4} ${CHEEK.L.y + 6} l12 -14 M${CHEEK.L.x + 12} ${CHEEK.L.y + 6} l12 -14"/>
      <path d="M${CHEEK.R.x - 24} ${CHEEK.R.y + 6} l12 -14 M${CHEEK.R.x - 8} ${CHEEK.R.y + 6} l12 -14 M${CHEEK.R.x + 8} ${CHEEK.R.y + 6} l12 -14"/>
    </g>
  </g>
  ${eyeMarkup('L', uid)}${eyeMarkup('R', uid)}
  <g class="mouth" transform="translate(${MOUTH.x} ${MOUTH.y})">
    <path class="mfill" fill="#5a1630"/>
    <g clip-path="url(#${uid}-mouthClip)"><ellipse class="tongue" cx="0" cy="22" rx="20" ry="13" fill="#ff7d93"/></g>
    <path class="mline" fill="none" stroke="${INK}" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/>
  </g>
  <g class="sweat" opacity="0" transform="translate(430 230)">
    <path d="M0 -26 C10 -8 16 2 16 10 A16 16 0 0 1 -16 10 C-16 2 -10 -8 0 -26 Z" fill="#bfe6ff" stroke="#6fbef5" stroke-width="2.5"/>
    <ellipse cx="-5" cy="8" rx="4" ry="6" fill="#fff" opacity=".8"/>
  </g>
  <g class="vein" opacity="0" transform="translate(398 214)">
    <g stroke="#e8304f" stroke-width="7" stroke-linecap="round" fill="none">
      <path d="M-18 -6 Q-8 -8 -6 -18"/><path d="M18 -6 Q8 -8 6 -18"/><path d="M-18 6 Q-8 8 -6 18"/><path d="M18 6 Q8 8 6 18"/>
    </g>
  </g>
</svg>`;
  }

  // ---------------------------------------------------------------- mouth geometry
  function mouthPaths(p, talk) {
    const w = Math.max(10, p.mw + talk * 6);
    const open = Math.max(0, p.mo + talk * 24);
    const N = 14, up = [], lo = [];
    for (let i = 0; i <= N; i++) {
      const t = i / N, u = 2 * t - 1, bell = 1 - u * u;
      const x = -w / 2 + w * t + p.mx;
      const wave = p.wave * Math.sin(t * Math.PI * 4) * 3.2;
      const skew = p.skew * u * 0.5;
      const yu = p.mc * bell + wave - skew;
      const yl = yu + open * Math.pow(bell, 0.75);
      up.push([x, yu]); lo.push([x, yl]);
    }
    const f = (pt) => pt[0].toFixed(1) + ' ' + pt[1].toFixed(1);
    const line = 'M' + up.map(f).join(' L');
    const shape = line + ' L' + lo.slice().reverse().map(f).join(' L') + ' Z';
    return { line: open > 1.2 ? shape : line, fill: shape, open };
  }

  // ---------------------------------------------------------------- rig class
  class PomRig {
    constructor(host, opts = {}) {
      this.host = host;
      this.assets = opts.assets || 'assets/';
      this.reduced = !!opts.reducedMotion;
      this.uid = 'pom' + (++PomRig._n);
      host.classList.add('pom-rig');
      host.innerHTML = `
        <div class="rig-motion">
          <img class="antenna" src="${this.assets}antenna.png" alt="" draggable="false">
          <img class="body" src="${this.assets}body.png" alt="" draggable="false">
          ${buildSVG(this.uid)}
        </div>
        <div class="rig-fx"></div>`;
      this.motionEl = host.querySelector('.rig-motion');
      this.antEl = host.querySelector('.antenna');
      this.fxEl = host.querySelector('.rig-fx');
      const q = (s) => host.querySelector(s);
      const eyes = {};
      for (const side of ['L', 'R']) {
        const g = q(`.eye[data-side="${side}"]`);
        eyes[side] = {
          g, ball: g.querySelector('.ball'), shine: g.querySelector('.shine'), wet: g.querySelector('.wet'),
          lidg: g.querySelector('.lidg'), lidrot: g.querySelector('.lidrot'), lid: g.querySelector('.lid'), lash: g.querySelector('.lash'), low: g.querySelector('.lowlid'),
          brow: g.querySelector('.brow'), tear: g.querySelector('.tear'), stream: g.querySelector('.stream'),
          modes: Object.fromEntries(EYE_MODES.map((m) => [m, g.querySelector('.m-' + m)])),
          pulse: g.querySelector('.m-heart .pulse'), spin: g.querySelector('.m-star .spin'), spiral: g.querySelector('.m-spiral .spin'),
        };
      }
      this.eyes = eyes;
      this.mouth = { fill: q('.mfill'), line: q('.mline'), clip: q('.mclip'), tongue: q('.tongue') };
      this.cheeks = [...host.querySelectorAll('.cheek')];
      this.shyLines = q('.shylines');
      this.sweat = q('.sweat');
      this.vein = q('.vein');

      this.p = { ...BASE };
      this.target = { ...BASE };
      this.modeW = Object.fromEntries(EYE_MODES.map((m) => [m, m === 'open' ? 1 : 0]));
      this.lidSide = { L: BASE.lid, R: BASE.lid };
      this.emotion = 'neutral';
      this.gaze = { x: 0, y: 0 }; this.gazeT = { x: 0, y: 0 };
      this.blink = 0; this.nextBlink = performance.now() + 1800;
      this.talk = 0; this.talkT = 0;
      this.ant = { a: 0, v: 0 };
      this.lastFx = 0;
      this.t0 = performance.now();
      this.motionOverride = null;
      this.hold = 0;
      this.setEmotion('neutral');
      this._raf = requestAnimationFrame((t) => this._frame(t));
    }

    static get emotions() { return Object.keys(EMOTIONS); }

    /** Switch emotion. holdMs keeps it from being replaced by lower-priority moods. */
    setEmotion(name, holdMs = 0) {
      if (!EMOTIONS[name]) name = 'neutral';
      this.emotion = name;
      this.target = { ...BASE, ...EMOTIONS[name] };
      this.hold = holdMs ? performance.now() + holdMs : 0;
      this.lastFx = 0;
      this.fxEl.querySelectorAll('.fx-dots,.fx-question,.fx-orbit').forEach((el) => el.remove());
      if (this.target.fxEvery === 99999) this.emit(this.target.fx);
      this.host.dataset.emotion = name;
    }
    isHeld() { return this.hold && performance.now() < this.hold; }

    /** Look at a point given in viewport pixels (or null to relax the gaze). */
    lookAt(px, py) {
      if (px == null) { this.gazeT.x = 0; this.gazeT.y = 0; return; }
      const r = this.host.getBoundingClientRect();
      const sc = r.width / ART.w;
      const ex = r.left + ((EYE.L.x + EYE.R.x) / 2) * sc;
      const ey = r.top + EYE.L.y * sc;
      const dx = px - ex, dy = py - ey;
      const d = Math.hypot(dx, dy) || 1;
      const reach = clamp(d / (r.width * 1.4), 0, 1);
      this.gazeT.x = (dx / d) * reach;
      this.gazeT.y = (dy / d) * reach;
    }

    /** Lip-sync level 0..1 from the audio analyser. */
    setTalk(level) { this.talkT = clamp(level, 0, 1); }

    /** Push the antenna (e.g. when the body moves sideways). */
    nudge(v) { this.ant.v += clamp(v, -40, 40) * 0.02; }

    /** Force a short body animation: squish, jump, land, spin. */
    play(kind) { this.motionOverride = { kind, t: performance.now() }; }

    emit(kind) {
      if (!kind || this.reduced && kind !== 'zzz') return;
      const make = (cls, html, x, y, extra = {}) => {
        const el = document.createElement('span');
        el.className = 'fx fx-' + cls;
        el.innerHTML = html;
        el.style.left = x + '%'; el.style.top = y + '%';
        for (const k in extra) el.style.setProperty(k, extra[k]);
        this.fxEl.appendChild(el);
        el.addEventListener('animationend', () => el.remove());
        setTimeout(() => el.remove(), 4000);
      };
      const rnd = (a, b) => a + Math.random() * (b - a);
      switch (kind) {
        case 'heart': make('heart', heartSVG(), rnd(25, 70), rnd(10, 30), { '--dx': rnd(-40, 40) + 'px', '--s': rnd(0.7, 1.2) }); break;
        case 'sparkle': make('sparkle', sparkleSVG(), rnd(5, 90), rnd(5, 55), { '--s': rnd(0.6, 1.2) }); break;
        case 'note': make('note', '♪', rnd(10, 85), rnd(5, 30), { '--dx': rnd(-30, 30) + 'px' }); break;
        case 'zzz': make('zzz', 'z', rnd(62, 72), rnd(14, 20), { '--s': rnd(0.8, 1.3) }); break;
        case 'question': make('question', '?', 76, 4); break;
        case 'exclaim': make('exclaim', '!', 76, 2); break;
        case 'steam': make('steam', steamSVG(), rnd(20, 70), rnd(8, 18)); break;
        case 'wave': make('wave', '', 50, 45); break;
        case 'dots': make('dots', '<i></i><i></i><i></i>', 72, 2); break;
        case 'drool': make('drool', '', 58, 72); break;
        case 'stars': for (let i = 0; i < 3; i++) make('orbit', '★', 50, 22, { '--d': (i * -0.6) + 's' }); break;
        case 'confetti': for (let i = 0; i < 14; i++) make('confetti', '', rnd(20, 80), rnd(20, 40), { '--dx': rnd(-120, 120) + 'px', '--h': Math.floor(rnd(0, 360)), '--r': rnd(-360, 360) + 'deg' }); break;
      }
    }

    destroy() { cancelAnimationFrame(this._raf); }

    // -------------------------------------------------------------- frame
    _frame(now) {
      const dt = Math.min(0.05, (now - (this._last || now)) / 1000); this._last = now;
      const t = (now - this.t0) / 1000;
      const k = 1 - Math.pow(0.0008, dt); // smoothing
      const T = this.target, P = this.p;
      for (const key of NUMERIC) P[key] = lerp(P[key], T[key], k);
      for (const m of EYE_MODES) this.modeW[m] = lerp(this.modeW[m], T.eyes === m ? 1 : 0, 1 - Math.pow(0.00001, dt));
      this.lidSide.L = lerp(this.lidSide.L, T.lidL ?? T.lid, k);
      this.lidSide.R = lerp(this.lidSide.R, T.lidR ?? T.lid, k);

      // blink
      if (now > this.nextBlink && T.eyes === 'open') {
        this.blinkStart = now; this.nextBlink = now + 2200 + Math.random() * 4200;
        if (Math.random() < 0.18) this.nextBlink = now + 260; // double blink
      }
      const bt = this.blinkStart ? (now - this.blinkStart) / 150 : 9;
      this.blink = bt < 1 ? Math.sin(bt * Math.PI) : 0;

      // gaze (locked gaze for thinking/shy, plus idle micro-saccades)
      const gl = T.gazeLock;
      let gx = gl ? gl.x : this.gazeT.x, gy = gl ? gl.y : this.gazeT.y;
      if (!gl && Math.abs(gx) + Math.abs(gy) < 0.02) { gx = Math.sin(t * 0.7) * 0.15; gy = Math.sin(t * 0.43) * 0.08; }
      if (T.motion === 'shiver') { gx += (Math.random() - 0.5) * 0.25; gy += (Math.random() - 0.5) * 0.25; }
      this.gaze.x = lerp(this.gaze.x, gx, 1 - Math.pow(0.0002, dt));
      this.gaze.y = lerp(this.gaze.y, gy, 1 - Math.pow(0.0002, dt));

      this.talk = lerp(this.talk, this.talkT, 1 - Math.pow(0.0000001, dt));

      this._drawEyes(t);
      this._drawMouth();
      this._drawExtras(t);
      this._drawMotion(t, dt, now);

      // ambient effects
      if (T.fx && T.fxEvery && T.fxEvery < 99999 && now - this.lastFx > T.fxEvery) { this.lastFx = now; this.emit(T.fx); }
      if (P.tears > 0.5 && now - (this._lastDrop || 0) > 380) { this._lastDrop = now; this._drop(); }

      this._raf = requestAnimationFrame((tt) => this._frame(tt));
    }

    _drawEyes(t) {
      const P = this.p, W = this.modeW;
      const gx = this.gaze.x * 6, gy = this.gaze.y * 6;
      for (const side of ['L', 'R']) {
        const e = this.eyes[side];
        const sign = side === 'L' ? 1 : -1;
        const lid = clamp(this.lidSide[side] + (1 - this.lidSide[side]) * this.blink, 0, 1);
        const sc = P.eyeScale;
        e.ball.setAttribute('transform', `translate(${gx.toFixed(2)} ${gy.toFixed(2)}) scale(${sc.toFixed(3)})`);
        const tremble = P.wet > 0.5 ? Math.sin(t * 40) * 0.6 : 0;
        e.shine.setAttribute('transform', `translate(${(this.gaze.x * 3 + tremble).toFixed(2)} ${(this.gaze.y * 3).toFixed(2)}) scale(${(1 + P.wet * 0.18).toFixed(3)})`);
        e.wet.setAttribute('opacity', (P.wet * 0.85).toFixed(2));
        // upper lid: edge position from -ry (open) to +ry (closed)
        const ry = EYE.ry * sc + 4;
        const yE = -ry + lid * (2 * ry + 6);
        const bulge = 10 + lid * 6;
        e.lid.setAttribute('d', `M-70 -140 L70 -140 L70 ${yE.toFixed(1)} Q0 ${(yE + bulge).toFixed(1)} -70 ${yE.toFixed(1)} Z`);
        e.lash.setAttribute('d', `M-46 ${yE.toFixed(1)} Q0 ${(yE + bulge).toFixed(1)} 46 ${yE.toFixed(1)}`);
        e.lash.setAttribute('opacity', clamp((lid - 0.12) * 5, 0, 1).toFixed(2));
        const lr = `rotate(${(P.tilt * sign).toFixed(2)})`;
        e.lidg.setAttribute('transform', lr); e.lidrot.setAttribute('transform', lr);
        // lower lid (happy squint)
        const yB = ry - P.low * ry * 1.25;
        e.low.setAttribute('d', `M-70 140 L70 140 L70 ${yB.toFixed(1)} Q0 ${(yB - 16 * P.low).toFixed(1)} -70 ${yB.toFixed(1)} Z`);
        // brows
        const asym = side === 'L' ? P.browAsym : 0;
        e.brow.setAttribute('transform', `translate(0 ${(-EYE.ry - 20 + P.browY + asym).toFixed(1)}) rotate(${(P.browTilt * sign).toFixed(1)})`);
        e.brow.setAttribute('opacity', P.browOp.toFixed(2));
        // alternative eye shapes
        for (const m of EYE_MODES) e.modes[m].setAttribute('opacity', (m === 'open' ? W.open : W[m]).toFixed(3));
        e.pulse.setAttribute('transform', `scale(${(1 + Math.sin(t * 8) * 0.08).toFixed(3)})`);
        e.spin.setAttribute('transform', `rotate(${(Math.sin(t * 3) * 12).toFixed(1)}) scale(${(1 + Math.sin(t * 10) * 0.06).toFixed(3)})`);
        e.spiral.setAttribute('transform', `rotate(${((t * 360 * sign) % 360).toFixed(1)})`);
        e.tear.setAttribute('opacity', (P.tears * 0.9).toFixed(2));
        e.stream.setAttribute('stroke-dasharray', '14 10');
        e.stream.setAttribute('stroke-dashoffset', (-t * 60).toFixed(1));
      }
    }

    _drawMouth() {
      const m = mouthPaths(this.p, this.talk);
      this.mouth.line.setAttribute('d', m.line);
      this.mouth.fill.setAttribute('d', m.fill);
      this.mouth.clip.setAttribute('d', m.fill);
      this.mouth.fill.setAttribute('opacity', clamp(m.open / 3, 0, 1).toFixed(2));
      this.mouth.tongue.setAttribute('opacity', clamp(this.p.tongue * m.open / 14, 0, 1).toFixed(2));
      this.mouth.tongue.setAttribute('cy', (this.p.mc + m.open * 0.85).toFixed(1));
    }

    _drawExtras(t) {
      const P = this.p;
      for (const c of this.cheeks) {
        c.setAttribute('opacity', P.blush.toFixed(2));
        c.setAttribute('rx', (40 * P.blushScale).toFixed(1));
        c.setAttribute('ry', (27 * P.blushScale).toFixed(1));
      }
      this.shyLines.setAttribute('opacity', P.shyLines.toFixed(2));
      this.sweat.setAttribute('opacity', P.sweat.toFixed(2));
      this.sweat.setAttribute('transform', `translate(430 ${(228 + ((t * 30) % 40) * P.sweat).toFixed(1)})`);
      this.vein.setAttribute('opacity', P.vein.toFixed(2));
      this.vein.setAttribute('transform', `translate(398 214) scale(${(1 + Math.sin(t * 9) * 0.12 * P.vein).toFixed(3)})`);
    }

    _drop() {
      for (const side of ['L', 'R']) {
        const el = document.createElement('span');
        el.className = 'fx fx-tear';
        el.style.left = ((EYE[side].x + (side === 'L' ? -14 : 14)) / ART.w * 100) + '%';
        el.style.top = ((EYE[side].y + 40) / ART.h * 100) + '%';
        el.style.setProperty('--dx', (side === 'L' ? -1 : 1) * (10 + Math.random() * 30) + 'px');
        this.fxEl.appendChild(el);
        el.addEventListener('animationend', () => el.remove());
      }
    }

    _drawMotion(t, dt, now) {
      const T = this.target, P = this.p;
      let y = 0, rot = P.tiltBody, sx = 1, sy = 1, x = 0;
      const R = this.reduced ? 0.3 : 1;
      switch (T.motion) {
        case 'breathe': sy = 1 + Math.sin(t * 2) * 0.012; sx = 1 - Math.sin(t * 2) * 0.008; break;
        case 'bob': y = -Math.abs(Math.sin(t * 4)) * 6 * R; sy = 1 + Math.sin(t * 8) * 0.015; break;
        case 'bounce': { const b = Math.abs(Math.sin(t * 6)); y = -b * 22 * R; sy = 1 + (b < 0.2 ? (0.2 - b) * 0.5 : 0) * R; sx = 2 - sy; break; }
        case 'hop': { const b = Math.max(0, Math.sin(t * 7)); y = -b * 30 * R; rot += Math.sin(t * 3.5) * 5 * R; break; }
        case 'giggle': y = -Math.abs(Math.sin(t * 14)) * 5 * R; rot += Math.sin(t * 14) * 3 * R; sy = 1 + Math.sin(t * 28) * 0.02; break;
        case 'sway': rot += Math.sin(t * 2.2) * 5 * R; break;
        case 'tilt': rot += Math.sin(t * 1.3) * 1.5; break;
        case 'pop': sy = 1 + Math.max(0, Math.sin(t * 3)) * 0.04; y = -4; break;
        case 'droop': sy = 0.96 + Math.sin(t * 1.2) * 0.008; sx = 1.03; y = 3; break;
        case 'sob': sy = 0.97 + Math.abs(Math.sin(t * 9)) * 0.03; x = Math.sin(t * 18) * 1.2; break;
        case 'shake': x = Math.sin(t * 45) * 3.2 * R; break;
        case 'shiver': x = Math.sin(t * 60) * 1.8 * R; sy = 0.97; sx = 0.98; break;
        case 'puff': sy = 1.03 + Math.sin(t * 2) * 0.01; sx = 1.04; y = -3; break;
        case 'nod': { const n = Math.sin(t * 0.9); rot += n > 0.7 ? (n - 0.7) * 25 : 0; sy = 1 + Math.sin(t * 1.6) * 0.012; break; }
        case 'snore': sy = 1 + Math.sin(t * 1.4) * 0.03; sx = 1 - Math.sin(t * 1.4) * 0.018; break;
        case 'wobble': rot += Math.sin(t * 4) * 9 * R; x = Math.sin(t * 2) * 4 * R; break;
      }
      // one-shot overrides
      const o = this.motionOverride;
      if (o) {
        const e = (now - o.t) / 1000;
        if (o.kind === 'squish' && e < 0.5) { const a = Math.exp(-e * 7) * Math.cos(e * 26); sy *= 1 - a * 0.16; sx *= 1 + a * 0.12; }
        else if (o.kind === 'land' && e < 0.6) { const a = Math.exp(-e * 6) * Math.cos(e * 20); sy *= 1 - a * 0.25; sx *= 1 + a * 0.2; }
        else if (o.kind === 'jump' && e < 0.7) { y -= Math.sin(Math.min(1, e / 0.7) * Math.PI) * 60; }
        else if (o.kind === 'spin' && e < 0.8) { rot += (e / 0.8) * 360; }
        else if (o.kind === 'stretch' && e < 0.6) { const a = Math.exp(-e * 5) * Math.cos(e * 18); sy *= 1 + a * 0.18; sx *= 1 - a * 0.1; }
        else this.motionOverride = null;
      }
      this.motionEl.style.transform = `translate(${x.toFixed(2)}px, ${y.toFixed(2)}px) rotate(${rot.toFixed(2)}deg) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`;

      // antenna: damped spring toward an emotion-specific rest angle
      let rest = 0;
      if (T.antenna === 'droop') rest = 22;
      else if (T.antenna === 'perk') rest = -4 + Math.sin(t * 10) * 2;
      else if (T.antenna === 'wiggle') rest = Math.sin(t * 6) * 9;
      else if (T.antenna === 'stiff') rest = Math.sin(t * 40) * 3;
      else rest = Math.sin(t * 1.3) * 3;
      const a = this.ant;
      const lastRot = this._lastRot ?? rot; this._lastRot = rot;
      a.v += ((rest - a.a) * 60 - a.v * 7 - (rot - lastRot) * 40) * dt;
      a.a += a.v * dt * 1.0;
      a.a = clamp(a.a, -45, 45);
      this.antEl.style.transform = `rotate(${a.a.toFixed(2)}deg)`;
    }
  }

  function heartSVG() { return '<svg viewBox="-20 -20 40 40" width="26" height="26"><path d="M0 14 C-22 0 -18 -18 -8 -18 C-3 -18 0 -13 0 -10 C0 -13 3 -18 8 -18 C18 -18 22 0 0 14 Z" fill="#ff5c86"/></svg>'; }
  function sparkleSVG() { return '<svg viewBox="-20 -20 40 40" width="24" height="24"><path d="M0 -18 C2 -4 4 -2 18 0 C4 2 2 4 0 18 C-2 4 -4 2 -18 0 C-4 -2 -2 -4 0 -18 Z" fill="#ffd84a"/></svg>'; }
  function steamSVG() { return '<svg viewBox="-20 -20 40 40" width="30" height="30"><circle cx="-6" cy="4" r="9" fill="#f2f5fa"/><circle cx="6" cy="-2" r="11" fill="#f2f5fa"/><circle cx="2" cy="8" r="8" fill="#f2f5fa"/></svg>'; }

  PomRig._n = 0;
  PomRig.ART = ART; PomRig.EYE = EYE; PomRig.EMOTIONS = EMOTIONS;
  global.PomRig = PomRig;
})(window);
