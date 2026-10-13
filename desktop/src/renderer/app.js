/* Bea desktop companion: behaviour, physics, chat, agent tasks, voice and wake word. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const pom = window.pom;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const pick = (a) => a[(Math.random() * a.length) | 0];
  const now = () => performance.now();

  const petEl = $('pet'), hit = $('hit'), bubble = $('bubble'), menu = $('menu'), panel = $('panel'), log = $('log');
  const input = $('input'), composer = $('composer'), micBtn = $('micBtn'), sendBtn = $('sendBtn'), speakBtn = $('speakBtn');
  const langSel = $('lang'), statusEl = $('status'), suggestEl = $('suggest'), chip = $('focusChip');

  const rig = new PomRig($('rigHost'), { assets: 'assets/', reducedMotion: reduced });
  const shadow = document.createElement('div'); shadow.className = 'ground-shadow'; petEl.appendChild(shadow);
  // Emotion-driven walking, jumping and dancing (see motion.js). Created in start() once layout is known.
  let motion = null;

  const local = {
    get(k, d) { try { const v = localStorage.getItem('pom.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('pom.' + k, JSON.stringify(v)); } catch {} },
  };

  // ------------------------------------------------------------------ state
  let settings = {}, server = null, serverError = '';
  let S = 170, PH = 196, W = innerWidth, H = innerHeight;
  const P = { x: innerWidth * 0.8, y: -260, vx: 0, vy: 0, mode: 'fall', target: null, phase: 0, landed: false };
  const cursor = { x: -9999, y: -9999, t: 0, speed: 0, inside: false };
  let lastInput = now(), sleeping = false, panelOpen = false, interactive = false, hidden = false;
  let affection = local.get('affection', 10);
  let lookOverride = null; // {x, y, until}
  const firstRun = !local.get('met', false);

  // ------------------------------------------------------------------ mood manager
  // Higher priority moods (chat, voice, showcase) can't be replaced by ambient ones until they expire.
  const mood = {
    pri: 0, until: 0,
    set(name, ms = 2500, pri = 1) {
      const t = now();
      if (t < this.until && pri < this.pri) return false;
      this.pri = pri; this.until = t + ms;
      if (rig.emotion !== name) {
        rig.setEmotion(name);
        if (pri >= 1 && motion && P.mode === 'ground' && !sleeping && !hidden) motion.react(name);
      }
      $('menuMood').textContent = name;
      return true;
    },
    clear(pri = 9) { if (pri >= this.pri) this.until = 0; },
    tick(t) {
      if (t < this.until) return;
      this.pri = 0;
      const b = baseline(t);
      if (rig.emotion !== b) { rig.setEmotion(b); $('menuMood').textContent = b; }
    },
  };

  function baseline(t) {
    if (sleeping) return 'sleeping';
    if (voice.playing) return 'talking';
    const idle = t - lastInput;
    if (!focus.end && !panelOpen) {
      if (idle > 240000) { goSleep(); return 'sleeping'; }
      if (idle > 130000) return 'sleepy';
      if (idle > 70000) return 'bored';
    }
    return affection > 70 ? 'happy' : 'neutral';
  }

  function touch() { lastInput = now(); }
  function love(n) { affection = clamp(affection + n, 0, 100); local.set('affection', affection); $('hearts').textContent = '♥ ' + Math.round(affection); }
  love(0);

  // ------------------------------------------------------------------ layout
  function applySize(px) {
    S = clamp(px || 170, 110, 320); PH = S * 600 / 520;
    document.documentElement.style.setProperty('--pom-size', S + 'px');
  }
  const groundY = () => H - PH - S * 0.03;
  addEventListener('resize', () => { W = innerWidth; H = innerHeight; P.x = clamp(P.x, 0, W - S); if (P.mode === 'ground') P.y = groundY(); });

  // ------------------------------------------------------------------ speech bubble
  let bubbleTimer = 0;
  function say(text, ms = 3600, sub) {
    if (hidden) return;
    bubble.textContent = text;
    if (sub) { const s = document.createElement('small'); s.textContent = sub; bubble.appendChild(s); }
    bubble.classList.add('show');
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => bubble.classList.remove('show'), ms);
  }
  const hush = () => { clearTimeout(bubbleTimer); bubble.classList.remove('show'); };

  // ------------------------------------------------------------------ click-through control
  function updateHit(x, y) {
    const el = document.elementFromPoint(x, y);
    const over = !!(el && el.closest('[data-hit]'));
    const want = over || P.mode === 'drag';
    if (want !== interactive) { interactive = want; pom.setInteractive(want); }
    return el && el.closest('#hit');
  }

  // ------------------------------------------------------------------ cursor sensing
  const pet = { pets: 0, petT: 0, lastPetAt: 0, angle: null, spin: 0, spinT: 0, wasFar: true, farSince: 0 };
  function petCenter() { return { x: P.x + S / 2, y: P.y + PH * 0.6 }; }

  function onCursor(x, y) {
    const t = now();
    const dt = Math.max(1, t - cursor.t);
    const d = Math.hypot(x - cursor.x, y - cursor.y);
    cursor.speed = cursor.speed * 0.6 + (d / dt) * 1000 * 0.4;
    cursor.x = x; cursor.y = y; cursor.t = t;
    if (hidden) return;
    const overPom = updateHit(x, y);
    if (!lookOverride) rig.lookAt(x, y);
    if (d > 2) touch();

    const c = petCenter();
    const dist = Math.hypot(x - c.x, y - c.y);

    if (sleeping) { if (dist < S * 0.9 && cursor.speed > 400) wake(); return; }
    if (P.mode === 'drag') return;

    // Petting: rubbing the cursor over Bea without clicking.
    if (overPom && !mouseDown && d < 200) {
      if (t - pet.petT > 1800) { pet.pets = 0; pet.petT = t; }
      pet.pets += d;
      if (pet.pets > 650 && t - pet.lastPetAt > 1400) {
        pet.lastPetAt = t; pet.pets = 0; pet.petT = t;
        love(2);
        const m = affection > 85 && Math.random() < 0.35 ? 'shy' : 'love';
        mood.set(m, 2600, 1);
        rig.emit('heart'); rig.emit('heart');
        if (Math.random() < 0.55) say(pick(m === 'shy'
          ? ['You\'re making me blush…', 'Eep! So many pets.', 'Okay okay, you\'re my favourite.']
          : ['Hehe, that tickles!', 'More pets please!', 'So soft, right?', 'Purr… wait, I\'m not a cat.', 'Best. Day. Ever.']), 2200);
      }
    }

    // Circling the cursor around Bea makes her dizzy.
    if (dist < S * 1.6 && !overPom) {
      const a = Math.atan2(y - c.y, x - c.x);
      if (pet.angle != null) {
        let da = a - pet.angle;
        if (da > Math.PI) da -= 2 * Math.PI; if (da < -Math.PI) da += 2 * Math.PI;
        if (t - pet.spinT > 2600) { pet.spin = 0; pet.spinT = t; }
        pet.spin += da;
        if (Math.abs(pet.spin) > Math.PI * 4.5) {
          pet.spin = 0; pet.spinT = t;
          mood.set('dizzy', 2600, 1); rig.play('squish');
          say(pick(['Woooah… the room is spinning!', 'Stop, stop, I\'m dizzy!', 'Round and round…']), 2200);
        }
      }
      pet.angle = a;
    } else pet.angle = null;

    // Noticing the cursor arrive.
    if (dist > S * 2.2) { if (!pet.wasFar) { pet.wasFar = true; pet.farSince = t; } }
    else if (pet.wasFar && dist < S * 1.1) {
      pet.wasFar = false;
      if (t - pet.farSince > 6000 && !panelOpen) {
        if (cursor.speed > 2600) { mood.set('scared', 900, 1); }
        else { mood.set('curious', 1500, 1); if (Math.random() < 0.3) say(pick(['Oh! Hello there.', 'Hi hi!', 'Need something?']), 1800); }
      }
    }
  }
  pom.onCursor((p) => onCursor(p.x, p.y));
  addEventListener('pointermove', (e) => { if (pom.preview) return; onCursor(e.clientX, e.clientY); });

  // ------------------------------------------------------------------ drag, throw, poke
  let mouseDown = null; const clicks = [];
  hit.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    hideMenu(); touch();
    mouseDown = { sx: e.clientX, sy: e.clientY, ox: e.clientX - P.x, oy: e.clientY - P.y, moved: false, hist: [], shakes: 0, lastDir: 0 };
    hit.setPointerCapture(e.pointerId);
  });
  hit.addEventListener('pointermove', (e) => {
    const m = mouseDown; if (!m) return;
    if (!m.moved && Math.hypot(e.clientX - m.sx, e.clientY - m.sy) > 6) {
      m.moved = true; P.mode = 'drag'; petEl.classList.add('dragging'); motion.clear();
      if (sleeping) wake(true);
      mood.set('excited', 60000, 2); hush();
      say(pick(['Wheee!', 'Up we go!', 'Where are we going?', 'I can fly!']), 1600);
    }
    if (!m.moved) return;
    const nx = clamp(e.clientX - m.ox, -S * 0.3, W - S * 0.7), ny = clamp(e.clientY - m.oy, -PH * 0.3, groundY());
    const vx = nx - P.x;
    const dir = Math.sign(vx);
    if (Math.abs(vx) > 14 && dir && dir !== m.lastDir) { m.shakes++; m.lastDir = dir; }
    if (m.shakes > 9) { m.shakes = 0; mood.set('dizzy', 60000, 2); say('Too much shaking!', 1500); }
    rig.nudge(vx * 2);
    P.x = nx; P.y = ny;
    m.hist.push({ x: nx, y: ny, t: now() }); if (m.hist.length > 6) m.hist.shift();
  });
  const release = (e) => {
    const m = mouseDown; mouseDown = null;
    if (!m) return;
    petEl.classList.remove('dragging');
    if (m.moved) {
      const h = m.hist;
      if (h.length > 1) { const a = h[0], b = h[h.length - 1], dt = Math.max(16, b.t - a.t) / 1000; P.vx = clamp((b.x - a.x) / dt, -2600, 2600) * 0.7; P.vy = clamp((b.y - a.y) / dt, -2600, 2600) * 0.7; }
      P.mode = 'fall';
      mood.clear(2);
      if (rig.emotion === 'dizzy') mood.set('dizzy', 2400, 2);
      else if (Math.hypot(P.vx, P.vy) > 1200) mood.set('scared', 900, 2);
      updateHit(e.clientX, e.clientY);
      return;
    }
    poke();
  };
  hit.addEventListener('pointerup', release);
  hit.addEventListener('pointercancel', release);
  hit.addEventListener('dblclick', () => openPanel());
  hit.addEventListener('contextmenu', (e) => { e.preventDefault(); showMenu(); });

  function poke() {
    const t = now();
    if (sleeping) { wake(); return; }
    clicks.push(t); while (clicks.length && t - clicks[0] > 1500) clicks.shift();
    rig.play('squish');
    if (clicks.length >= 7) {
      clicks.length = 0; love(-3);
      mood.set('angry', 2600, 2); rig.emit('steam');
      say(pick(['Hey! That\'s too many pokes!', 'Grr! I\'m a plush, not a button!', 'Okay, I\'m officially grumpy.']), 2400);
      setTimeout(() => { if (rig.emotion === 'angry') { mood.set('smug', 1600, 1); say('…fine, I forgive you.', 1600); } }, 2800);
      return;
    }
    if (clicks.length >= 4) { mood.set('joyful', 1500, 1); say(pick(['Hehe, stop it!', 'Boop boop boop!', 'Haha, that tickles!']), 1500); return; }
    love(1);
    const r = pick([
      ['happy', ['Hi hi!', 'You found me!', 'Boop!', 'Double-click me to chat!', 'I like it here.']],
      ['wink', ['Hey there!', 'Looking good today.']],
      ['laughing', ['Hehe!', 'Again! Again!']],
      ['surprised', ['Oh! You startled me.', 'Eep!']],
      ['love', ['Aww, hello!', 'You\'re the best.']],
    ]);
    mood.set(r[0], 1500, 1);
    if (r[0] === 'love') rig.emit('heart');
    if (!firstRun || Math.random() < 0.6) say(pick(r[1]), 1700);
  }

  // ------------------------------------------------------------------ sleep
  function goSleep(msg) {
    if (sleeping) return;
    sleeping = true; if (motion) motion.clear();
    mood.set('sleeping', 1e9, 1);
    if (msg) say(msg, 2400);
    $('sleepItem').textContent = 'Wake up';
  }
  function wake(quiet) {
    if (!sleeping) return;
    sleeping = false; touch();
    mood.clear(); mood.set('surprised', 900, 1);
    $('sleepItem').textContent = 'Nap time';
    if (!quiet) setTimeout(() => { mood.set('happy', 1600, 1); say(pick(['I\'m awake! Just resting my eyes.', 'Huh? Oh, hi!', '*yawn* …I wasn\'t sleeping.']), 2400); }, 900);
  }

  // ------------------------------------------------------------------ idle life
  let nextIdle = now() + 5000;
  function idleAct(t) {
    if (t < nextIdle || P.mode !== 'ground' || panelOpen || sleeping || focus.end || hidden || motion.busy() || t < mood.until && mood.pri > 0) return;
    nextIdle = t + 5000 + Math.random() * 7000;
    const idle = t - lastInput;
    const r = Math.random();
    if (r < 0.34 && !reduced) {
      // Wander with a gait that fits her mood.
      const gait = idle > 70000 ? 'trudge' : affection > 70 ? 'skip' : Math.random() < 0.15 ? 'strut' : 'walk';
      motion.wander(clamp(P.x + (Math.random() - 0.5) * W * 0.5, 0, W - S), gait);
    } else if (r < 0.5) {
      lookOverride = { x: Math.random() * W, y: Math.random() * H * 0.7, until: t + 1600 };
      rig.lookAt(lookOverride.x, lookOverride.y);
    } else if (r < 0.6) {
      rig.play('stretch'); mood.set('happy', 1200, 0);
    } else if (r < 0.7) {
      mood.set('happy', 2200, 0); rig.emit('note'); setTimeout(() => rig.emit('note'), 600); setTimeout(() => rig.emit('note'), 1200);
    } else if (r < 0.78 && idle > 30000) {
      mood.set('sleepy', 1800, 0); say('*yawn*', 1400);
    } else if (r < 0.84 && !reduced) {
      motion.perform(Math.random() < 0.5 ? 'jump' : 'bounce'); mood.set('excited', 900, 0);
    } else if (r < 0.95) {
      say(ambientLine(), 3200);
    }
  }

  function ambientLine() {
    const h = new Date().getHours();
    const name = settings.userName ? ', ' + settings.userName : '';
    const timed = h < 5 ? [`It\'s really late${name}. Rest soon?`] : h < 12 ? [`Good morning${name}!`, 'Morning coffee time?'] :
      h < 17 ? ['Remember to drink some water.', 'How\'s your afternoon going?'] : h < 22 ? [`Good evening${name}!`, 'Almost done for today?'] : ['Getting late… don\'t forget to rest.'];
    return pick([...timed, 'Double-click me if you need anything.', 'Stretch your shoulders!', 'I\'m keeping you company.', 'Ask me anything, I mean it!', 'You\'re doing great today.', '*hums a little tune*']);
  }

  // ------------------------------------------------------------------ main loop
  let last = now();
  function frame(t) {
    const dt = Math.min(0.05, (t - last) / 1000); last = t;
    const g = groundY();
    let hopY = 0, tilt = 0;
    if (P.mode === 'fall') {
      P.vy += 2600 * dt; P.x += P.vx * dt; P.y += P.vy * dt; P.vx *= 0.995;
      if (P.x < 0) { P.x = 0; P.vx = -P.vx * 0.5; rig.play('squish'); }
      if (P.x > W - S) { P.x = W - S; P.vx = -P.vx * 0.5; rig.play('squish'); }
      if (P.y < -PH * 0.3) { P.y = -PH * 0.3; P.vy = Math.abs(P.vy) * 0.3; }
      if (P.y >= g) {
        const impact = P.vy;
        P.y = g;
        if (impact > 320) {
          P.vy = -impact * 0.3; P.vx *= 0.6; rig.play('land');
          if (impact > 1500) { mood.set('dizzy', 2200, 2); rig.emit('stars'); say(pick(['Ouch! I\'m okay!', 'Soft landing… sort of.', 'Again! Wait, no.']), 2000); }
        } else {
          P.vy = 0; P.vx = 0; P.mode = 'ground';
          if (!P.landed) { P.landed = true; onFirstLanding(); }
        }
      }
      rig.nudge(P.vx * dt * 30);
    } else if (P.mode === 'ground') {
      P.y = g;
    }
    let sx = 1, sy = 1, ox = 0;
    if (P.mode === 'ground') {
      const pose = motion.update(dt);
      hopY = pose.jump + pose.hop; tilt = pose.tilt; sx = pose.sx; sy = pose.sy; ox = pose.ox;
    } else if (motion.busy()) motion.clear();
    if (lookOverride && t > lookOverride.until) { lookOverride = null; rig.lookAt(cursor.x, cursor.y); }

    const top = P.y + hopY;
    petEl.style.transform = `translate(${(P.x + ox).toFixed(1)}px, ${top.toFixed(1)}px) rotate(${tilt.toFixed(2)}deg) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})`;
    const lift = clamp((g - top) / 300, 0, 1);
    shadow.style.transform = `translateY(${((g - top) / sy).toFixed(1)}px) scale(${(1 - lift * 0.6).toFixed(3)})`;
    shadow.style.opacity = (1 - lift * 0.7).toFixed(2);

    placeBubble(); placeChip(); if (panelOpen) placePanel();
    lipSync();
    mood.tick(t);
    idleAct(t);
    focusTick();
    requestAnimationFrame(frame);
  }

  function placeBubble() {
    if (!bubble.classList.contains('show')) return;
    const bw = bubble.offsetWidth, bh = bubble.offsetHeight;
    let bx = clamp(P.x + S / 2 - bw / 2, 8, W - bw - 8);
    let by = P.y + (motion ? Math.min(0, motion.pose.jump) : 0) + PH * 0.12 - bh - 10;
    if (by < 8) by = P.y + PH + 10;
    bubble.style.left = bx + 'px'; bubble.style.top = by + 'px';
    bubble.style.setProperty('--tail', clamp(P.x + S / 2 - bx, 18, bw - 18) + 'px');
  }
  function placeChip() {
    if (chip.hidden) return;
    chip.style.left = (P.x + S / 2 - chip.offsetWidth / 2) + 'px';
    chip.style.top = (P.y + PH * 0.08 - 34) + 'px';
  }

  function onFirstLanding() {
    if (!settings.hasToken && !pom.preview) {
      mood.set('shy', 6000, 2);
      say('Hi! I\'m Bea. Open Settings and connect me to your Bea server so I can answer questions.', 9000);
    } else if (firstRun) {
      local.set('met', true);
      mood.set('excited', 3500, 2);
      say('Hi! I\'m Bea! Double-click me to chat, drag me around, or rub my head.', 7000, 'Right-click me for more.');
    } else {
      mood.set('happy', 2500, 1);
      say(ambientLine(), 3500);
    }
  }

  // ------------------------------------------------------------------ context menu
  function showMenu() {
    menu.hidden = false;
    const mw = menu.offsetWidth, mh = menu.offsetHeight;
    menu.style.left = clamp(P.x + S * 0.6, 8, W - mw - 8) + 'px';
    menu.style.top = clamp(P.y + PH * 0.3 - mh, 8, H - mh - 8) + 'px';
    menu.querySelector('button').focus();
  }
  function hideMenu() { menu.hidden = true; }
  menu.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return; hideMenu();
    if (b.dataset.move) move(b.dataset.move); else command(b.dataset.cmd);
  });
  addEventListener('pointerdown', (e) => { if (!menu.hidden && !menu.contains(e.target)) hideMenu(); });
  $('kbdChat').textContent = pom.platform === 'darwin' ? '⌘ ⇧ Space' : 'Ctrl ⇧ Space';

  function command(cmd) {
    touch();
    if (cmd.startsWith('move:')) return move(cmd.slice(5));
    switch (cmd) {
      case 'chat': panelOpen ? closePanel() : openPanel(); break;
      case 'talk': openPanel(); startMic(); break;
      case 'wake': onWake(); break;
      case 'focus': focus.end ? stopFocus() : startFocus(25); break;
      case 'dance': dance(); break;
      case 'sleep': sleeping ? wake() : goSleep('Nap time. Click me to wake me up.'); break;
      case 'showcase': showcase(); break;
      case 'settings': pom.openSettings(); break;
      case 'hide': hideFor(10 * 60000); break;
    }
  }
  pom.onCommand(command);

  // Moves picked from the menu or tray. Each move comes with a fitting face.
  const MOVE_MOODS = { dance: 'joyful', celebrate: 'excited', jump: 'excited', run: 'excited', spin: 'happy', wave: 'happy', hide: 'shy', stretch: 'sleepy', bounce: 'happy', nod: 'happy', shake_head: 'confused', sit: 'neutral', walk: 'happy' };
  function move(name) {
    touch();
    if (name === 'dance') return dance();
    if (sleeping) wake(true);
    if (P.mode !== 'ground') return;
    if (MOVE_MOODS[name]) mood.set(MOVE_MOODS[name], ['run', 'hide', 'celebrate'].includes(name) ? 6500 : 3000, 2);
    motion.perform(name); // replaces the reaction mood.set may have queued
    if (name === 'hide') say('Peekaboo!', 1600);
    if (name === 'run') say('Zoomies!', 1400);
  }

  function dance() {
    if (sleeping) wake(true);
    mood.set('joyful', 9000, 2);
    motion.perform('dance');
    say(pick(['Look at my moves!', 'Dance party!', 'Shake it, shake it!']), 2200);
    let n = 0; const iv = setInterval(() => { rig.emit('note'); if (++n > 12 || !motion.busy()) clearInterval(iv); }, 600);
  }

  function showcase() {
    const list = PomRig.emotions.filter((e) => e !== 'talking');
    let i = 0;
    if (sleeping) wake(true);
    const step = () => {
      if (i >= list.length) { mood.clear(); say('That\'s all my faces!', 2000); return; }
      const e = list[i++];
      mood.set(e, 1500, 3); say(e.charAt(0).toUpperCase() + e.slice(1), 1400);
      setTimeout(step, 1500);
    };
    step();
  }

  function hideFor(ms) {
    hidden = true; closePanel(); hush();
    document.body.style.visibility = 'hidden'; pom.setInteractive(false); interactive = false;
    setTimeout(() => { hidden = false; document.body.style.visibility = ''; P.y = -PH; P.vy = 0; P.mode = 'fall'; P.landed = false; }, ms);
  }

  // ------------------------------------------------------------------ focus timer
  const focus = { end: 0 };
  function startFocus(min) {
    focus.end = Date.now() + min * 60000; chip.hidden = false;
    $('focusItem').textContent = 'Stop focus timer';
    mood.set('proud', 2000, 2); say(`${min} minutes of focus. I'll stay quiet, promise.`, 2600);
  }
  function stopFocus(done) {
    focus.end = 0; chip.hidden = true; $('focusItem').textContent = 'Focus timer · 25 min';
    if (done) {
      love(5); mood.set('joyful', 6000, 2); motion.perform('celebrate');
      say('Focus session complete! Time for a stretch break.', 6000);
      speakLine('Focus session complete! Time for a stretch break.');
    } else say('Timer stopped. We\'ll try again later.', 2000);
  }
  function focusTick() {
    if (!focus.end) return;
    const left = focus.end - Date.now();
    if (left <= 0) { stopFocus(true); return; }
    const s = Math.ceil(left / 1000);
    chip.textContent = 'Focus ' + String((s / 60) | 0).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
  }

  // ------------------------------------------------------------------ chat panel
  const SUGGESTIONS = ['What can you do?', 'Tell me a joke', 'Plan my day in 5 steps', 'Explain APIs simply', 'हिंदी में बात करो'];
  const AGENT_SUGGESTIONS = ['What can you do for me?', 'Research our top 3 competitors and write a brief', 'What needs my attention in my inbox today?',
    'Remind me every weekday at 9:30 to check project reports', 'Plan my week and track it as a goal'];
  function placePanel() {
    const pw = panel.offsetWidth, ph = panel.offsetHeight;
    let left = P.x + S / 2 > W / 2 ? P.x - pw - 10 : P.x + S + 10;
    let top = P.y + PH - ph;
    if (left < 8 || left + pw > W - 8) { left = clamp(P.x + S / 2 - pw / 2, 8, W - pw - 8); top = P.y - ph - 12; }
    panel.style.left = clamp(left, 8, W - pw - 8) + 'px';
    panel.style.top = clamp(top, 8, H - ph - 8) + 'px';
  }
  function openPanel() {
    if (hidden) return;
    if (sleeping) wake(true);
    hideMenu(); hush();
    if (motion && motion.name === 'wander') motion.clear();
    panel.hidden = false; panelOpen = true; placePanel();
    pom.focus(); setTimeout(() => input.focus(), 30);
    if (!log.children.length) renderSuggestions();
    if (!chatId) mood.set('happy', 1500, 1);
    refreshServer();
  }
  function closePanel() { panel.hidden = true; panelOpen = false; stopMic(true); }
  $('closeBtn').addEventListener('click', closePanel);
  $('newBtn').addEventListener('click', () => {
    stopSpeaking(); if (chatId) pom.chatCancel(chatId);
    log.innerHTML = ''; settings.conversationId = null; pom.chatReset();
    if (run) { run.active = false; run = null; }
    settings.agentThread = null; pom.saveSettings({ agentThread: null });
    renderSuggestions();
    mood.set('happy', 1200, 1); setStatus('New conversation');
    input.focus();
  });
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { if (!menu.hidden) hideMenu(); else if (voice.playing) stopSpeaking(); else if (panelOpen) closePanel(); }
  });

  function renderSuggestions() {
    suggestEl.innerHTML = '';
    suggestEl.hidden = log.children.length > 0;
    for (const s of (useAgent() ? AGENT_SUGGESTIONS : SUGGESTIONS)) {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = s;
      b.addEventListener('click', () => send(s));
      suggestEl.appendChild(b);
    }
  }

  function setStatus(text) { statusEl.textContent = text; }
  const EMO_STATUS = { happy: 'Happy', joyful: 'Delighted', laughing: 'Laughing', love: 'Feeling the love', excited: 'Excited!', surprised: 'Surprised', curious: 'Curious', thinking: 'Thinking…', sad: 'A little sad', crying: 'Teary', angry: 'Grumpy', scared: 'Nervous', shy: 'Shy', confused: 'Confused', smug: 'Pleased with herself', proud: 'Proud', bored: 'Bored', sleepy: 'Sleepy', dizzy: 'Dizzy', wink: 'Playful', hungry: 'Hungry', neutral: 'Here for you' };

  input.addEventListener('input', () => {
    input.style.height = 'auto'; input.style.height = Math.min(120, input.scrollHeight) + 'px';
    touch();
    if (!chatId && !voice.playing) mood.set('listening', 1600, 1);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); composer.requestSubmit(); }
  });
  composer.addEventListener('submit', (e) => { e.preventDefault(); const v = input.value; input.value = ''; input.style.height = 'auto'; send(v); });

  // ---- message rendering (small, safe markdown subset)
  function esc(s) { return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function inline(s) {
    return s.replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>')
      .replace(/\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function md(text) {
    const parts = esc(text).split(/```(?:[a-zA-Z0-9_-]*)\n?([\s\S]*?)(?:```|$)/);
    let html = '';
    parts.forEach((p, i) => {
      if (i % 2) { html += `<pre><code>${p}</code></pre>`; return; }
      for (const block of p.split(/\n{2,}/)) {
        if (!block.trim()) continue;
        const lines = block.split('\n');
        if (lines.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l) || !l.trim())) {
          html += '<ul>' + lines.filter((l) => l.trim()).map((l) => '<li>' + inline(l.replace(/^\s*([-*•]|\d+\.)\s+/, '')) + '</li>').join('') + '</ul>';
        } else html += '<p>' + inline(block).replace(/\n/g, '<br>') + '</p>';
      }
    });
    return html;
  }
  function addMsg(kind, text, html) {
    suggestEl.hidden = true;
    const el = document.createElement('div');
    el.className = 'msg ' + kind;
    if (html) el.innerHTML = html; else el.textContent = text;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  // ---- sending
  let chatId = null, current = null, currentText = '', sentWithVoice = false;
  function send(text, opts = {}) {
    text = (text || '').trim();
    if (!text) return;
    touch();
    if (!panelOpen) openPanel();
    if (!settings.hasToken && !pom.preview) { notConfigured(); return; }
    stopSpeaking();
    if (useAgent()) { agentSend(text, opts); return; }
    if (chatId) pom.chatCancel(chatId);
    addMsg('me', text);
    current = addMsg('pom', '', '<span class="typing"><i></i><i></i><i></i></span>');
    currentText = '';
    chatId = (crypto.randomUUID && crypto.randomUUID()) || String(Math.random());
    sentWithVoice = speakingEnabled();
    mood.set('thinking', 90000, 2); setStatus('Thinking…');
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    pom.chatStart(chatId, {
      message: text,
      conversation_id: settings.conversationId || null,
      language: settings.language,
      client_time: new Date().toLocaleString('en-GB', { dateStyle: 'full', timeStyle: 'short' }) + ` (${tz})`,
      voice: sentWithVoice || !!opts.voice,
    });
  }

  pom.onChatEvent(({ id, type, data }) => {
    if (id !== chatId) return;
    if (type === 'meta') settings.conversationId = data.conversation_id;
    else if (type === 'emotion') {
      mood.set(data.emotion, 90000, 2);
      setStatus(EMO_STATUS[data.emotion] || 'Here for you');
      if (['excited', 'joyful', 'love'].includes(data.emotion)) rig.play('jump');
    } else if (type === 'action') {
      if (P.mode === 'ground' && !sleeping) {
        if (data.action === 'dance' || data.action === 'celebrate') mood.set('joyful', 9000, 2);
        motion.perform(data.action);
      }
    } else if (type === 'delta') {
      if (!currentText && rig.emotion === 'thinking') { mood.set('talking', 90000, 2); setStatus('Here for you'); }
      currentText += data.text;
      current.innerHTML = md(currentText);
      log.scrollTop = log.scrollHeight;
      if (sentWithVoice) speakPush(data.text);
    } else if (type === 'done') {
      currentText = data.text || currentText;
      current.innerHTML = md(currentText);
      if (sentWithVoice) speakFlush();
      mood.set(data.emotion || 'happy', voice.playing || voice.queue.length ? 60000 : 4500, 2);
      if (!voice.playing && !voice.queue.length) setStatus(EMO_STATUS[data.emotion] || 'Here for you');
      love(0.5);
      if (!panelOpen) say(currentText.length > 160 ? currentText.slice(0, 157) + '…' : currentText, 7000);
    } else if (type === 'error') {
      chatId = null;
      showError(data.message || 'Something went wrong.', data.status === 401 || data.status === 0);
    } else if (type === 'end') {
      chatId = null;
      if (current && !currentText && !data.cancelled && current.querySelector('.typing')) current.remove();
    }
  });

  function showError(message, settingsHint) {
    if (current && !currentText) current.remove();
    const el = addMsg('err', message);
    const b = document.createElement('button');
    b.textContent = settingsHint ? 'Open settings' : 'Retry';
    b.addEventListener('click', () => {
      if (settingsHint) pom.openSettings();
      else { const lastMe = [...log.querySelectorAll('.msg.me')].pop(); el.remove(); if (lastMe) { const t = lastMe.textContent; lastMe.remove(); send(t); } }
    });
    el.appendChild(b);
    mood.set(settingsHint ? 'confused' : 'sad', 3500, 2);
    setStatus(settingsHint ? 'Not connected' : 'Something went wrong');
  }

  function notConfigured() {
    const el = addMsg('pom', '', md('I need to be connected to your **Bea server** before I can answer. Add the server address and your device token in Settings.'));
    const b = document.createElement('button'); b.textContent = 'Open settings';
    b.style.cssText = 'margin-top:8px;border:0;border-radius:9px;padding:6px 12px;font-weight:700;background:var(--accent);color:#fff';
    b.addEventListener('click', () => pom.openSettings());
    el.appendChild(b);
    mood.set('shy', 3000, 2);
  }

  // ------------------------------------------------------------------ agent mode
  // Bea plans, uses her tools (web, her own browser, mail, calendar, company APIs) and asks before
  // anything that acts on the outside world. Steps stream in live; approvals appear as cards.
  let agentStatus = null, run = null;
  const agentBtn = $('agentBtn');
  function useAgent() { return !!(settings.agentMode && agentStatus && agentStatus.available && !pom.preview); }
  function syncAgentBtn() {
    agentBtn.setAttribute('aria-pressed', String(!!settings.agentMode));
    agentBtn.title = !agentStatus ? 'Agent mode (connecting…)'
      : !agentStatus.available ? 'Agent mode unavailable: ' + agentStatus.reason
      : settings.agentMode ? 'Agent mode is on: Bea plans and uses her tools and apps' : 'Agent mode is off: quick chat only';
    input.placeholder = useAgent() ? 'Give Bea a task…' : 'Ask Bea anything…';
    if (!log.children.length) renderSuggestions();
  }
  agentBtn.addEventListener('click', async () => {
    if (agentStatus && !agentStatus.available) { showError(agentStatus.reason, true); return; }
    settings = { ...settings, ...(await pom.saveSettings({ agentMode: !settings.agentMode })) };
    syncAgentBtn();
    say(settings.agentMode ? 'Agent mode on. Give me a task!' : 'Quick chat mode.', 1800);
  });
  $('hubBtn').addEventListener('click', () => pom.openHub());

  async function agentSend(text, opts) {
    if (run && run.active) { showError('I\'m still working on the last task. Stop it first, or wait a moment.'); return; }
    addMsg('me', text);
    const el = addMsg('pom agent', '');
    el.innerHTML = '<div class="plan" hidden></div><ul class="steps"></ul><div class="answer"><span class="typing"><i></i><i></i><i></i></span></div>'
      + '<div class="run-bar"><span class="info">Working on it…</span><button type="button" class="stop">Stop</button></div>';
    run = { id: null, el, plan: el.querySelector('.plan'), steps: el.querySelector('.steps'), answer: el.querySelector('.answer'),
      info: el.querySelector('.info'), text: '', seq: 0, active: true, voice: speakingEnabled() || !!opts.voice, stepEls: {} };
    const mine = run;
    el.querySelector('.stop').addEventListener('click', () => mine.id && pom.api('POST', `/v1/agent/runs/${mine.id}/cancel`).catch(() => {}));
    mood.set('thinking', 180000, 2); setStatus('Working on it…');
    try {
      run.id = await pom.agentStart({
        goal: text, thread_id: settings.agentThread || null, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        language: settings.language, user_name: settings.userName || null, voice: run.voice,
      });
    } catch (e) {
      mine.active = false; el.remove(); run = null;
      showError(e.message, e.status === 401 || e.status === 0);
    }
  }

  function scrollLog() { log.scrollTop = log.scrollHeight; }
  function renderPlan(steps) {
    run.plan.hidden = !steps.length;
    run.plan.innerHTML = '';
    for (const st of steps) {
      const d = document.createElement('div');
      d.className = st.status === 'done' || st.status === 'skipped' ? 'done' : st.status === 'in_progress' ? 'now' : '';
      d.textContent = (st.status === 'done' ? '✓ ' : st.status === 'in_progress' ? '▸ ' : '○ ') + st.text;
      run.plan.appendChild(d);
    }
    scrollLog();
  }
  function addStep(d) {
    if (d.name === 'update_plan') return; // shown as the plan instead
    const li = document.createElement('li');
    const b = document.createElement('b'); b.textContent = d.label || d.name;
    const sp = document.createElement('span'); sp.textContent = d.summary && d.summary !== d.name ? ' ' + d.summary : '';
    li.append(b, sp); li.title = d.summary || '';
    run.steps.appendChild(li); run.stepEls[d.id] = li;
    run.info.textContent = (d.label || 'Working') + '…';
    scrollLog();
  }
  function finishStep(d) {
    const li = run.stepEls[d.id]; if (!li) return;
    li.classList.add(d.ok ? 'ok' : 'bad');
    if (!d.ok && d.preview) li.title = d.preview;
  }
  function approvalCard(a) {
    const id = a.approval_id || a.id;
    const card = document.createElement('div'); card.className = 'approval'; card.dataset.id = id;
    card.innerHTML = '<div class="what"></div><details><summary>Details</summary><pre></pre></details>'
      + '<div class="row"><button type="button" class="yes">Approve</button><button type="button" class="no">Deny</button>'
      + '<input type="text" maxlength="300" placeholder="Note for Bea (optional)"></div>';
    card.querySelector('.what').textContent = '✋ ' + a.summary;
    card.querySelector('pre').textContent = JSON.stringify(a.details, null, 2).slice(0, 4000);
    const note = card.querySelector('input');
    const decide = async (approve) => {
      card.querySelectorAll('button').forEach((b) => (b.disabled = true));
      try {
        const r = await pom.api('POST', `/v1/agent/approvals/${id}`, { approve, note: note.value.trim() || null });
        markDecided(id, r.status);
        mood.set(approve ? 'happy' : 'neutral', 1800, 2);
      } catch (e) { card.querySelectorAll('button').forEach((b) => (b.disabled = false)); showError(e.message); }
    };
    card.querySelector('.yes').addEventListener('click', () => decide(true));
    card.querySelector('.no').addEventListener('click', () => decide(false));
    return card;
  }
  function markDecided(id, status) {
    for (const card of log.querySelectorAll(`.approval[data-id="${id}"]`)) {
      if (card.classList.contains('decided')) continue;
      card.classList.add('decided');
      card.querySelector('.row').textContent = { approved: '✓ Approved', denied: '✗ Denied', expired: 'Expired (no answer in time)' }[status] || status;
    }
  }
  function askApproval(a) {
    if (log.querySelector(`.approval[data-id="${a.approval_id || a.id}"]`)) return;
    const card = approvalCard(a);
    if (run && run.id === a.run_id && run.el.isConnected) run.el.insertBefore(card, run.answer);
    else addMsg('note', '').appendChild(card); // an approval from a background task
    if (!panelOpen) openPanel();
    mood.set('curious', 8000, 2); rig.play('jump');
    say('I need your OK for this one.', 3500);
    speakLine('I need your okay before I do that.');
    scrollLog();
  }
  function docLink(d) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'doclink';
    b.textContent = '📄 ' + d.title;
    b.addEventListener('click', () => pom.openHub('documents/' + d.document_id));
    run.el.insertBefore(b, run.el.querySelector('.run-bar'));
  }
  function finishRun(d) {
    run.active = false;
    run.el.querySelector('.stop').remove();
    if (!run.text && d.result) { run.text = d.result; run.answer.innerHTML = md(d.result); }
    if (!run.text) run.answer.textContent = '';
    const label = { done: 'Done', failed: 'Didn\'t finish', cancelled: 'Stopped' }[d.status] || d.status;
    const tokens = d.usage ? Math.round((d.usage.input_tokens + d.usage.output_tokens) / 100) / 10 : 0;
    run.info.textContent = label + (tokens ? ` · ${tokens}k tokens` : '');
    if (d.status === 'done') { settings.agentThread = run.id; pom.saveSettings({ agentThread: run.id }); }
    if (run.voice) speakFlush();
    const emo = d.emotion || (d.status === 'done' ? 'happy' : 'sad');
    mood.set(emo, voice.playing || voice.queue.length ? 60000 : 4500, 2);
    if (!voice.playing && !voice.queue.length) setStatus(EMO_STATUS[emo] || 'Here for you');
    for (const a of d.actions || []) if (P.mode === 'ground' && !sleeping) motion.perform(a);
    if (d.status === 'done') love(1);
    if (!panelOpen && run.text) say(run.text.length > 160 ? run.text.slice(0, 157) + '…' : run.text, 7000);
    scrollLog();
  }

  pom.onAgentEvent(({ runId, type, data }) => {
    if (!run || runId !== run.id) return;
    if (data && data.seq) run.seq = data.seq;
    switch (type) {
      case 'plan': renderPlan(data.steps || []); break;
      case 'tool': addStep(data); break;
      case 'tool_result': finishStep(data); break;
      case 'thinking': mood.set('thinking', 180000, 2); break;
      case 'approval': askApproval({ ...data, run_id: runId }); break;
      case 'approval_decided': markDecided(data.approval_id, data.status); mood.set('thinking', 180000, 2); break;
      case 'delta':
        if (!run.text) { run.answer.innerHTML = ''; if (rig.emotion === 'thinking') mood.set('talking', 90000, 2); }
        run.text += data.text; run.answer.innerHTML = md(run.text); scrollLog();
        if (run.voice) speakPush(data.text);
        break;
      case 'emotion': mood.set(data.emotion, 90000, 2); setStatus(EMO_STATUS[data.emotion] || 'Here for you'); break;
      case 'action': if (P.mode === 'ground' && !sleeping) motion.perform(data.action); break;
      case 'document': docLink(data); break;
      case 'client_action':
        if (data.action === 'open_url') pom.openExternal(data.url).catch(() => {});
        else if (data.action === 'copy') pom.copy(data.text).then(() => say('Copied to your clipboard.', 1800));
        break;
      case 'message': run.text = data.text; run.answer.innerHTML = md(data.text); scrollLog(); break;
      case 'error': run.info.textContent = data.message; break;
      case 'done': finishRun(data); break;
      case 'stream_error': case 'stream_end':
        if (run.active) { const id = run.id; run.info.textContent = 'Reconnecting…'; setTimeout(() => run && run.id === id && run.active && pom.agentWatch(id, run.seq), 2500); }
        break;
    }
  });

  // ------------------------------------------------------------------ notifications (background tasks, reminders, approvals)
  pom.onNotify(async (n) => {
    if (n.kind === 'approval') {
      if (log.querySelector(`.approval[data-id="${n.approval_id}"]`)) return;
      try {
        const pending = await pom.api('GET', '/v1/agent/approvals');
        const a = pending.find((x) => x.id === n.approval_id);
        if (a) askApproval({ ...a, approval_id: a.id });
      } catch {}
      return;
    }
    if (sleeping) wake(true);
    const el = addMsg('note', '', '<b></b>' + md(n.body || ''));
    el.querySelector('b').textContent = (n.kind === 'reminder' ? '⏰ ' : n.kind === 'task_done' ? '✅ ' : '🔔 ') + n.title;
    if (n.kind === 'reminder') { mood.set('excited', 6000, 2); rig.play('jump'); say(n.title + (n.body && n.body !== n.title ? ': ' + n.body : ''), 9000); speakLine(n.body || n.title); }
    else if (n.kind === 'task_done') { mood.set('proud', 5000, 2); say(n.title, 6000); speakLine((n.body || '').slice(0, 600)); }
    else { mood.set('curious', 3000, 2); say(n.title, 5000); }
  });

  // ------------------------------------------------------------------ wake word ("Bea", "Hey Bea")
  function onWake() {
    if (hidden || mic.rec) return;
    touch();
    if (sleeping) wake(true);
    stopSpeaking();
    openPanel();
    mood.set('excited', 1200, 2); rig.play('jump');
    say('Yes?', 1200);
    startMic();
  }
  pom.onWakeStatus((st) => { if (st.state === 'error') setStatus('Wake word: ' + st.message); });

  // ------------------------------------------------------------------ voice out (Sarvam TTS + lip sync)
  const voice = { ctx: null, analyser: null, data: null, queue: [], playing: false, buf: '', gen: 0, src: null, warned: false };
  function ensureAudio() {
    if (!voice.ctx) {
      voice.ctx = new AudioContext();
      voice.analyser = voice.ctx.createAnalyser(); voice.analyser.fftSize = 1024;
      voice.analyser.connect(voice.ctx.destination);
      voice.data = new Uint8Array(voice.analyser.fftSize);
    }
    if (voice.ctx.state === 'suspended') voice.ctx.resume();
  }
  function speakingEnabled() { return !!(settings.speakReplies && !settings.muted && server && server.voice && !pom.preview); }
  function cleanForSpeech(s) {
    return s.replace(/```[\s\S]*?```/g, ' The code is on screen. ').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/[*_#>`|~]/g, '').replace(/https?:\/\/\S+/g, 'the link').replace(/\s+/g, ' ').trim();
  }
  function speakPush(text) {
    voice.buf += text;
    // Speak sentence by sentence so Bea starts talking before the whole answer arrives.
    const re = /[.!?।…]+["')\]]?\s+|\n{2,}/g;
    let m, cut = -1;
    while ((m = re.exec(voice.buf))) cut = m.index + m[0].length;
    const min = voice.queue.length || voice.playing ? 120 : 30;
    if (cut > 0 && cut >= min && !/```/.test(voice.buf.slice(0, cut)) ) { enqueue(voice.buf.slice(0, cut)); voice.buf = voice.buf.slice(cut); }
  }
  function speakFlush() { if (voice.buf.trim()) enqueue(voice.buf); voice.buf = ''; }
  function speakLine(text) { if (speakingEnabled()) { enqueue(text); } }
  function enqueue(seg) {
    const clean = cleanForSpeech(seg).slice(0, 2400);
    if (!clean) return;
    ensureAudio();
    const gen = voice.gen;
    const p = pom.tts(clean, settings.language).catch((e) => ({ error: e }));
    voice.queue.push({ p, gen });
    if (!voice.playing) playQueue();
  }
  async function playQueue() {
    voice.playing = true;
    pom.wakePause(true);
    while (voice.queue.length) {
      const item = voice.queue.shift();
      const r = await item.p;
      if (item.gen !== voice.gen) continue;
      if (r.error) { if (!voice.warned) { voice.warned = true; setStatus('Voice unavailable: ' + r.error.message); } continue; }
      try {
        const bytes = r.data;
        const buf = await voice.ctx.decodeAudioData(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
        if (item.gen !== voice.gen) continue;
        const src = voice.ctx.createBufferSource();
        src.buffer = buf; src.connect(voice.analyser); voice.src = src;
        setStatus('Speaking…');
        await new Promise((res) => { src.onended = res; src.start(); });
      } catch (e) { /* skip a clip that can't be decoded */ }
    }
    voice.playing = false; voice.src = null; rig.setTalk(0);
    if (!mic.rec) pom.wakePause(false);
    if (!chatId) { setStatus(EMO_STATUS[rig.emotion] || 'Here for you'); mood.set(rig.emotion === 'talking' ? 'happy' : rig.emotion, 3000, 2); }
  }
  function stopSpeaking() {
    voice.gen++; voice.queue = []; voice.buf = '';
    try { voice.src && voice.src.stop(); } catch {}
    voice.src = null; rig.setTalk(0);
  }
  function lipSync() {
    if (!voice.playing || !voice.src) { if (!mic.rec) rig.setTalk(0); return; }
    voice.analyser.getByteTimeDomainData(voice.data);
    let sum = 0; for (let i = 0; i < voice.data.length; i++) { const v = (voice.data[i] - 128) / 128; sum += v * v; }
    const rms = Math.sqrt(sum / voice.data.length);
    rig.setTalk(clamp((rms - 0.015) * 7, 0, 1));
  }
  speakBtn.addEventListener('click', async () => {
    const on = !(settings.speakReplies && !settings.muted);
    if (on) ensureAudio(); else stopSpeaking();
    settings = { ...settings, ...(await pom.saveSettings({ speakReplies: on, muted: !on })) };
    syncSpeakBtn();
  });
  function syncSpeakBtn() {
    const available = !!(server && server.voice) && !pom.preview;
    speakBtn.disabled = !available;
    speakBtn.setAttribute('aria-pressed', String(available && settings.speakReplies && !settings.muted));
    speakBtn.title = available ? 'Speak replies' : 'Voice is not set up on the server';
    micBtn.title = available ? 'Talk to Bea' : 'Voice is not set up on the server';
  }

  // ------------------------------------------------------------------ voice in (mic + Sarvam STT)
  const mic = { rec: null, stream: null, chunks: [], an: null, data: null, heard: false, quietSince: 0, started: 0, cancel: false, raf: 0 };
  micBtn.addEventListener('click', () => (mic.rec ? stopMic() : startMic()));
  async function startMic() {
    if (mic.rec) return;
    if (pom.preview) { say('Voice works in the desktop app.', 2200); return; }
    if (!(server && server.voice)) { setStatus('Voice is not set up on the server'); say('To talk with me, add a Sarvam AI key in Settings > API keys.', 5000); mood.set('confused', 2000, 2); pom.wakePause(false); return; }
    try {
      ensureAudio(); stopSpeaking();
      if (!(await pom.ensureMic())) throw new Error('Microphone permission was denied');
      mic.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      setStatus('Mic unavailable: ' + e.message); mood.set('sad', 2500, 2); return;
    }
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
    mic.chunks = []; mic.heard = false; mic.quietSince = 0; mic.started = now(); mic.cancel = false;
    mic.rec = new MediaRecorder(mic.stream, { mimeType: mime });
    mic.rec.ondataavailable = (e) => e.data.size && mic.chunks.push(e.data);
    mic.rec.onstop = finishMic;
    mic.rec.start(250);
    const src = voice.ctx.createMediaStreamSource(mic.stream);
    mic.an = voice.ctx.createAnalyser(); mic.an.fftSize = 1024; src.connect(mic.an);
    mic.data = new Uint8Array(mic.an.fftSize);
    pom.wakePause(true);
    micBtn.classList.add('rec'); mood.set('listening', 120000, 3); setStatus('Listening… tap the mic when you\'re done');
    monitorMic();
  }
  function monitorMic() {
    if (!mic.rec) return;
    mic.an.getByteTimeDomainData(mic.data);
    let sum = 0; for (let i = 0; i < mic.data.length; i++) { const v = (mic.data[i] - 128) / 128; sum += v * v; }
    const rms = Math.sqrt(sum / mic.data.length);
    $('micLevel').style.setProperty('--lv', (1 + clamp(rms * 6, 0, 0.5)).toFixed(2));
    rig.nudge(rms * 120);
    const t = now();
    if (rms > 0.035) { mic.heard = true; mic.quietSince = 0; }
    else if (mic.heard && !mic.quietSince) mic.quietSince = t;
    if (mic.heard && mic.quietSince && t - mic.quietSince > 1400) return stopMic();
    if (!mic.heard && t - mic.started > 9000) return stopMic(true, 'I didn\'t hear anything. Try again?');
    if (t - mic.started > 30000) return stopMic();
    mic.raf = requestAnimationFrame(monitorMic);
  }
  function stopMic(cancel, why) {
    if (!mic.rec) return;
    mic.cancel = !!cancel; mic.why = why;
    cancelAnimationFrame(mic.raf);
    try { mic.rec.stop(); } catch {}
  }
  async function finishMic() {
    const rec = mic.rec; mic.rec = null;
    micBtn.classList.remove('rec');
    mic.stream && mic.stream.getTracks().forEach((t) => t.stop());
    mood.clear(3);
    if (!voice.playing) pom.wakePause(false);
    if (mic.cancel) { if (mic.why) { setStatus(mic.why); mood.set('confused', 1800, 2); } else setStatus('Here for you'); return; }
    const blob = new Blob(mic.chunks, { type: rec.mimeType || 'audio/webm' });
    if (blob.size < 1500) { setStatus('That was too short. Try again?'); return; }
    mood.set('thinking', 30000, 2); setStatus('Understanding you…');
    try {
      const r = await pom.stt(new Uint8Array(await blob.arrayBuffer()), 'audio/webm', settings.language);
      if (r.transcript) send(r.transcript, { voice: true });
      else { mood.set('confused', 2200, 2); setStatus('Sorry, I didn\'t catch that.'); }
    } catch (e) { showError(e.message, e.status === 401); }
  }

  // ------------------------------------------------------------------ settings & server
  const DEFAULT_LANGS = [['en-IN', 'English'], ['hi-IN', 'Hindi'], ['te-IN', 'Telugu'], ['ta-IN', 'Tamil'], ['kn-IN', 'Kannada'], ['ml-IN', 'Malayalam'], ['mr-IN', 'Marathi'], ['bn-IN', 'Bengali'], ['gu-IN', 'Gujarati'], ['pa-IN', 'Punjabi'], ['od-IN', 'Odia']];
  function fillLanguages(list) {
    langSel.innerHTML = '';
    for (const [code, name] of list) {
      const o = document.createElement('option'); o.value = code; o.textContent = name.slice(0, 3) === 'Eng' ? 'EN' : code.slice(0, 2).toUpperCase(); o.title = name;
      langSel.appendChild(o);
    }
    langSel.value = settings.language || 'en-IN';
  }
  langSel.addEventListener('change', async () => { settings = { ...settings, ...(await pom.saveSettings({ language: langSel.value })) }; say('Okay! ' + langSel.selectedOptions[0].title + ' it is.', 1600); });

  let lastServerCheck = 0;
  async function refreshServer(force) {
    if (!force && now() - lastServerCheck < 30000) return;
    lastServerCheck = now();
    if (!settings.hasToken && !pom.preview) { server = null; syncSpeakBtn(); return; }
    try { server = await pom.serverConfig(); serverError = ''; }
    catch (e) { server = null; serverError = e.message; }
    try { agentStatus = server ? await pom.api('GET', '/v1/agent/status') : null; } catch { agentStatus = null; }
    syncAgentBtn();
    if (server && server.languages) fillLanguages(server.languages.map((l) => [l.code, l.name]));
    if (server && server.assistant_name) $('pomName').textContent = server.assistant_name;
    setStatus(serverError ? 'Not connected' : (EMO_STATUS[rig.emotion] || 'Here for you'));
    syncSpeakBtn();
  }

  pom.onSettings(async (s) => {
    settings = { ...settings, ...s };
    applySize(settings.petSize);
    const fresh = await pom.getSettings(); settings.hasToken = fresh.hasToken;
    refreshServer(true);
  });

  // ------------------------------------------------------------------ start
  (async function start() {
    try { settings = await pom.getSettings(); } catch { settings = {}; }
    applySize(settings.petSize);
    fillLanguages(DEFAULT_LANGS);
    W = innerWidth; H = innerHeight;
    P.x = clamp(W - S - W * 0.08, 0, W - S); P.y = -PH; P.mode = 'fall';
    motion = new PomMotion({
      P, rig, reducedMotion: reduced,
      env: () => ({ W, S, PH, ground: groundY(), cx: cursor.x < -1000 ? P.x + S / 2 : cursor.x, cy: cursor.y }),
      canRoam: () => !panelOpen && !focus.end && !sleeping && !hidden,
      look: (x, y, ms) => { lookOverride = { x, y, until: now() + ms }; rig.lookAt(x, y); },
    });
    window.__pom = { motion, mood, rig, command }; // for debugging in DevTools
    refreshServer(true);
    requestAnimationFrame(frame);
  })();
})();
