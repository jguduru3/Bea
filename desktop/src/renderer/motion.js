/* Pom locomotion: emotion-driven walking, jumping and dancing.
 *
 * Every emotion maps to a short behaviour made of primitive moves (walk with a gait,
 * jump, slide, sway, stomp, squat, spin, hide). Moves are queued and run frame by frame.
 * The engine moves P.x itself and reports extra body pose in `pose` (jump height,
 * tilt and squash/stretch) for the app to apply to Pom's window position.
 */
(function (global) {
  'use strict';
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const rnd = (a, b) => a + Math.random() * (b - a);
  const pick = (a) => a[(Math.random() * a.length) | 0];
  const G = 3000; // gravity for jumps, px/s²

  // Gaits: speed in px/s, hop as a fraction of Pom's size, steps per second (freq), lean in degrees.
  const GAITS = {
    walk:    { speed: 95,  hop: 0.04,  freq: 11,  lean: 3,  squash: 0.05 },
    skip:    { speed: 135, hop: 0.12,  freq: 8.5, lean: 5,  squash: 0.10 },
    run:     { speed: 430, hop: 0.05,  freq: 22,  lean: 10, squash: 0.06 },
    trudge:  { speed: 38,  hop: 0.012, freq: 4.5, lean: 1,  squash: 0.03, sy: 0.96 },
    stomp:   { speed: 75,  hop: 0.08,  freq: 5.5, lean: 2,  squash: 0.18, shake: 1 },
    strut:   { speed: 72,  hop: 0.03,  freq: 7,   lean: 2,  squash: 0.04, sway: 8 },
    stagger: { speed: 58,  hop: 0.02,  freq: 6,   lean: 0,  squash: 0.04, wobble: 14 },
    tiptoe:  { speed: 48,  hop: 0.02,  freq: 14,  lean: 2,  squash: 0.03 },
  };

  class PomMotion {
    /**
     * @param {object} o
     * @param {object} o.P      shared position object {x, y}
     * @param {PomRig} o.rig
     * @param {() => {W:number,S:number,PH:number,ground:number,cx:number,cy:number}} o.env
     * @param {() => boolean} o.canRoam  false keeps Pom in place (chat open, focus timer…)
     * @param {(x:number,y:number,ms:number) => void} o.look  temporary gaze target
     */
    constructor(o) {
      Object.assign(this, o);
      this.queue = [];
      this.cur = null;
      this.name = null;
      this.pose = { jump: 0, hop: 0, tilt: 0, sx: 1, sy: 1, ox: 0 };
      this.pulse = 0;       // landing / step squash, decays
      this.pulseAmt = 0;
      this.lastReact = 0;
      this.lastHide = 0;
      this.reduced = !!o.reducedMotion;
    }

    busy() { return !!(this.cur || this.queue.length); }
    clear() { this.queue = []; this.cur = null; this.name = null; this.pose.jump = 0; }

    // ------------------------------------------------------------ public API
    /** React to a new emotion with a matching movement. */
    react(emotion) {
      const t = performance.now();
      if (t - this.lastReact < 900) return;
      const plan = this._emotionPlan(emotion);
      if (!plan) return;
      this.lastReact = t;
      this._start(emotion, plan);
    }

    /** Idle stroll to x with a given gait. */
    wander(x, gait = 'walk') { if (this._roam()) this._start('wander', [walk(x, gait)]); }

    /** Perform a named action (from the menu or from the AI). */
    perform(action) {
      const plan = this._actionPlan(action);
      if (plan) this._start(action, plan);
    }

    // ------------------------------------------------------------ planning
    _env() { return this.env(); }
    _roam() { return this.canRoam() && !this.reduced; }

    _towardCursor(maxDist, stopAt) {
      const e = this._env(), x = this.P.x + e.S / 2;
      let d = e.cx - x;
      if (Math.abs(d) < stopAt) return null;
      d = Math.sign(d) * Math.min(Math.abs(d) - stopAt, maxDist);
      return clamp(this.P.x + d, 0, e.W - e.S);
    }
    _awayFromCursor(dist) {
      const e = this._env(), x = this.P.x + e.S / 2;
      const dir = e.cx > x ? -1 : 1;
      return clamp(this.P.x + dir * dist, 0, e.W - e.S);
    }
    _randomNear(dist) { const e = this._env(); return clamp(this.P.x + rnd(-dist, dist), 0, e.W - e.S); }

    _emotionPlan(emo) {
      const roam = this._roam(), S = this._env().S;
      const A = [];
      switch (emo) {
        case 'happy':
          if (roam && Math.random() < 0.6) A.push(walk(this._randomNear(260), 'skip'));
          A.push(jump(0.22 * S));
          break;
        case 'joyful':
          A.push(jump(0.3 * S), jump(0.42 * S, { spin: true }));
          break;
        case 'laughing':
          A.push(squat(5, 0.16, 0.13));
          break;
        case 'love': {
          const tx = roam ? this._towardCursor(320, S * 0.9) : null;
          if (tx != null) A.push(walk(tx, 'skip'));
          A.push(sway(1.6, 7, 2.2), jump(0.15 * S));
          break;
        }
        case 'excited':
          if (roam) { const e = this._env(); A.push(walk(clamp(this.P.x + 220, 0, e.W - e.S), 'run'), walk(clamp(this.P.x - 220, 0, e.W - e.S), 'run')); }
          A.push(jump(0.4 * S), jump(0.25 * S));
          break;
        case 'surprised':
          A.push(jump(0.45 * S, { quick: true }));
          break;
        case 'curious': {
          const tx = roam ? this._towardCursor(180, S * 1.1) : null;
          if (tx != null) A.push(walk(tx, 'tiptoe'));
          A.push(leanToCursor(1.4));
          break;
        }
        case 'thinking':
          if (roam) { const x0 = this.P.x; A.push(walk(clamp(x0 - 90, 0, 1e5), 'walk'), wait(0.5), walk(x0, 'walk'), wait(0.4)); }
          else A.push(sway(2.4, 3, 0.8));
          break;
        case 'sad':
          if (roam) A.push(walk(this._awayFromCursor(140), 'trudge'));
          A.push(sit(2.2));
          break;
        case 'angry':
          if (roam) { const tx = this._towardCursor(160, S * 1.2); if (tx != null) A.push(walk(tx, 'stomp')); }
          A.push(stomp(3));
          break;
        case 'scared':
          if (roam && performance.now() - this.lastHide > 9000) { this.lastHide = performance.now(); A.push(...this._hidePlan()); }
          else A.push(jump(0.18 * S, { quick: true }), shiver(1.2));
          break;
        case 'shy':
          if (roam) A.push(walk(this._awayFromCursor(70), 'tiptoe'));
          A.push(sway(1.4, 5, 1.6));
          break;
        case 'confused':
          A.push(lookSide(-1, 0.5), lookSide(1, 0.5));
          if (roam) A.push(walk(this._randomNear(50), 'tiptoe'));
          break;
        case 'smug':
          if (roam) A.push(walk(this._randomNear(200), 'strut'));
          else A.push(sway(1.8, 5, 1.1));
          break;
        case 'proud':
          A.push(jump(0.28 * S), sway(1.2, 6, 1.5));
          break;
        case 'sleepy':
          if (roam) A.push(walk(this._randomNear(80), 'trudge'));
          break;
        case 'dizzy':
          if (roam) A.push(walk(this._randomNear(90), 'stagger'), walk(this._randomNear(90), 'stagger'));
          break;
        case 'wink':
          A.push(jump(0.14 * S));
          break;
        case 'hungry':
          A.push(jump(0.12 * S), jump(0.12 * S));
          break;
        default:
          return null;
      }
      return A;
    }

    _actionPlan(action) {
      const roam = this._roam(), S = this._env().S, e = this._env();
      switch (action) {
        case 'dance': return this._dancePlan();
        case 'celebrate': this.rig.emit('confetti'); return [jump(0.45 * S, { spin: true }), ...this._dancePlan(1)];
        case 'jump': return [jump(0.45 * S)];
        case 'bounce': return [jump(0.2 * S), jump(0.25 * S), jump(0.3 * S)];
        case 'spin': return [spin(0.8)];
        case 'wave': return [jump(0.12 * S), sway(1.0, 12, 4)];
        case 'nod': return [squat(2, 0.08, 0.22)];
        case 'shake_head': return [sway(0.9, 9, 5)];
        case 'walk': return roam ? [walk(this._randomNear(e.W * 0.35), 'walk')] : [squat(2, 0.06, 0.2)];
        case 'run': return roam ? [walk(e.W - e.S - 10, 'run'), walk(10, 'run'), walk(clamp(this.P.x, 0, e.W - e.S), 'run')] : [jump(0.3 * S), jump(0.3 * S)];
        case 'hide': return roam ? this._hidePlan() : [shiver(1.0)];
        case 'sit': return [sit(2.5)];
        case 'stretch': return [stretch()];
      }
      return null;
    }

    _hidePlan() {
      const e = this._env();
      const left = e.cx > this.P.x + e.S / 2;
      const edge = left ? 0 : e.W - e.S;
      const hidden = left ? -e.S * 0.48 : e.W - e.S * 0.52;
      return [walk(edge, 'run'), walk(hidden, 'tiptoe', true), shiver(0.6), wait(1.6), lookSide(left ? 1 : -1, 0.8), walk(edge + (left ? 30 : -30), 'tiptoe', true)];
    }

    _dancePlan(phrases = 2) {
      const S = this._env().S, beat = 0.46;
      const side = (dir) => slide(dir * S * 0.13, beat);
      const PH = {
        shuffle: () => [side(-1), side(1), side(1), side(-1), jump(0.3 * S, { spin: true }), squat(2, 0.12, beat / 2)],
        wiggle: () => [sway(beat * 4, 14, 1 / (beat * 2)), jump(0.18 * S), jump(0.18 * S), spin(beat * 2)],
        moon: () => [slide(-S * 0.25, beat * 2, true), slide(S * 0.25, beat * 2, true), jump(0.42 * S)],
        bounce: () => [squat(4, 0.14, beat / 2), jump(0.25 * S, { spin: true }), sway(beat * 2, 10, 1 / beat)],
      };
      const out = [note()];
      const keys = Object.keys(PH);
      for (let i = 0; i < phrases; i++) out.push(...PH[pick(keys)](), note());
      return out;
    }

    _start(name, plan) { this.name = name; this.queue = plan.slice(); this.cur = null; }

    // ------------------------------------------------------------ frame
    update(dt) {
      const p = this.pose;
      p.hop = 0; p.ox = 0;
      let tilt = 0, sx = 1, sy = 1;
      if (!this.cur && this.queue.length) { this.cur = this.queue.shift(); this.cur.t = 0; this.cur.init && this.cur.init(this); }
      const c = this.cur;
      if (c) {
        c.t += dt;
        const r = c.step(this, dt) || {};
        tilt = r.tilt || 0; sx = r.sx || 1; sy = r.sy || 1;
        if (r.done) { this.cur = null; if (!this.queue.length) this.name = null; }
      }
      // landing / footstep squash pulse
      this.pulse = Math.max(0, this.pulse - dt * 6);
      const q = Math.sin(this.pulse * Math.PI) * this.pulseAmt;
      sy *= 1 - q; sx *= 1 + q * 0.8;
      p.tilt += (tilt - p.tilt) * Math.min(1, dt * 14);
      p.sx = sx; p.sy = sy;
      return p;
    }
    _impact(amount) { this.pulse = 1; this.pulseAmt = amount; }
  }

  // ---------------------------------------------------------------- primitives
  function wait(sec) { return { step: (m) => ({ done: m.cur.t >= sec }) }; }

  function walk(toX, gaitName, allowOff) {
    const g = GAITS[gaitName] || GAITS.walk;
    return {
      init(m) { this.phase = 0; this.lastSin = 0; this.wob = 0; this.to = toX; if (!allowOff) { const e = m._env(); this.to = clamp(toX, 0, e.W - e.S); } },
      step(m, dt) {
        const e = m._env();
        const d = this.to - m.P.x, dir = Math.sign(d) || 1;
        const v = g.speed * dt;
        this.phase += g.freq * dt * Math.PI;
        const s = Math.sin(this.phase);
        if (Math.sign(s) !== Math.sign(this.lastSin) && this.lastSin !== 0) {
          m._impact(g.squash);
          if (g.shake) m.pose.ox = rnd(-3, 3);
        }
        this.lastSin = s;
        m.pose.hop = -Math.abs(s) * g.hop * e.S;
        let tilt = dir * g.lean;
        if (g.sway) tilt += Math.sin(this.phase / 2) * g.sway;
        if (g.wobble) { this.wob += (rnd(-1, 1) * g.wobble - this.wob) * dt * 4; tilt += this.wob; }
        m.look(m.P.x + e.S / 2 + dir * 500, m.P.y + e.PH * 0.55, 200);
        if (Math.abs(d) <= v) { m.P.x = this.to; m._impact(g.squash * 1.5); return { done: true }; }
        m.P.x += dir * v;
        m.rig.nudge(dir * g.speed * 0.02);
        return { tilt, sy: g.sy || 1 };
      },
    };
  }

  function jump(height, opts = {}) {
    const crouch = opts.quick ? 0.05 : 0.13;
    return {
      init() { this.y = 0; this.v = 0; this.phase = 'crouch'; },
      step(m, dt) {
        if (this.phase === 'crouch') {
          const k = Math.min(1, m.cur.t / crouch);
          if (k >= 1) { this.phase = 'air'; this.v = Math.sqrt(2 * G * height); if (opts.spin) m.rig.play('spin'); else m.rig.play('stretch'); }
          return { sy: 1 - 0.16 * k, sx: 1 + 0.12 * k };
        }
        this.v -= G * dt; this.y += this.v * dt;
        if (this.y <= 0) { this.y = 0; m.pose.jump = 0; m._impact(clamp(height / 400, 0.08, 0.25)); m.rig.play('land'); return { done: true }; }
        m.pose.jump = -this.y;
        const st = clamp(this.v / 900, -0.4, 0.4);
        return { sy: 1 + st * 0.18, sx: 1 - st * 0.12 };
      },
    };
  }

  function slide(dx, dur, moon) {
    return {
      init(m) { this.x0 = m.P.x; const e = m._env(); this.x1 = clamp(m.P.x + dx, 0, e.W - e.S); },
      step(m) {
        const k = Math.min(1, m.cur.t / dur), ease = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        m.P.x = this.x0 + (this.x1 - this.x0) * ease;
        m.pose.hop = moon ? 0 : -Math.sin(k * Math.PI) * m._env().S * 0.06;
        if (k >= 1) { m._impact(0.08); return { done: true }; }
        return { tilt: moon ? -Math.sign(dx) * 10 : Math.sign(dx) * 6 };
      },
    };
  }

  function sway(dur, amp, freq) {
    return { step: (m) => { const t = m.cur.t; return { done: t >= dur, tilt: Math.sin(t * freq * Math.PI * 2) * amp * Math.min(1, (dur - t) * 4) }; } };
  }

  function squat(n, depth, half) {
    return { step: (m) => { const t = m.cur.t, k = Math.abs(Math.sin((t / half) * Math.PI / 2)); return { done: t >= n * half * 2, sy: 1 - depth * k, sx: 1 + depth * 0.7 * k }; } };
  }

  function stomp(n) {
    const per = 0.42;
    return {
      init() { this.hits = 0; },
      step(m) {
        const t = m.cur.t, k = (t % per) / per, i = Math.floor(t / per);
        if (i > this.hits) { this.hits = i; m._impact(0.2); m.pose.ox = rnd(-5, 5); m.rig.emit('steam'); }
        m.pose.hop = -Math.sin(k * Math.PI) * m._env().S * 0.08;
        return { done: t >= n * per, tilt: (i % 2 ? 1 : -1) * 4 };
      },
    };
  }

  function sit(dur) {
    return { step: (m) => { const t = m.cur.t, k = Math.min(1, t * 5, (dur - t) * 5); return { done: t >= dur, sy: 1 - 0.08 * k, sx: 1 + 0.07 * k }; } };
  }

  function shiver(dur) { return { step: (m) => { m.pose.ox = rnd(-2.2, 2.2); return { done: m.cur.t >= dur, sy: 0.97 }; } }; }

  function spin(dur) { return { init: (m) => m.rig.play('spin'), step: (m) => ({ done: m.cur.t >= dur }) }; }

  function stretch() { return { init: (m) => m.rig.play('stretch'), step: (m) => ({ done: m.cur.t >= 0.6 }) }; }

  function note() { return { init: (m) => { m.rig.emit('note'); m.rig.emit('note'); }, step: () => ({ done: true }) }; }

  function leanToCursor(dur) {
    return { step: (m) => { const e = m._env(); const dir = Math.sign(e.cx - (m.P.x + e.S / 2)); return { done: m.cur.t >= dur, tilt: dir * 7 }; } };
  }

  function lookSide(dir, dur) {
    return {
      init: (m) => { const e = m._env(); m.look(m.P.x + e.S / 2 + dir * 600, m.P.y + e.PH * 0.5, dur * 1000); },
      step: (m) => ({ done: m.cur.t >= dur, tilt: dir * 5 }),
    };
  }

  PomMotion.GAITS = GAITS;
  global.PomMotion = PomMotion;
})(window);
