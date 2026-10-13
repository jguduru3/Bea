const grid = document.getElementById('grid');
  const rigs = [];
  for (const name of PomRig.emotions) {
    const cell = document.createElement('div'); cell.className = 'cell';
    const host = document.createElement('div');
    const label = document.createElement('div'); label.textContent = name;
    cell.append(host, label); grid.appendChild(cell);
    const r = new PomRig(host, { assets: 'assets/' }); r.setEmotion(name); rigs.push(r);
  }
  addEventListener('pointermove', (e) => rigs.forEach((r) => r.lookAt(e.clientX, e.clientY)));
