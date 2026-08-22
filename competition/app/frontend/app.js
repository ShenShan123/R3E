const $ = (id) => document.getElementById(id);
const json = (value) => JSON.stringify(value, null, 2);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let lastRun = null;

async function get(path) { const response = await fetch(path); if (!response.ok) throw new Error(await response.text()); return response.json(); }
async function post(path, body) { const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); if (!response.ok) throw new Error(await response.text()); return response.json(); }

function showTrace(run) {
  const rows = (run?.verified_candidates || []).flatMap(item => item.verification.stages.map(stage => `<div class="card"><b>${escapeHtml(item.candidate.id)} · ${escapeHtml(stage.name.toUpperCase())}</b><div class="${escapeHtml(stage.status)}">${escapeHtml(stage.status)}</div></div>`));
  $('trace').innerHTML = `<div class="cards">${rows.join('')}</div>`;
}

function showAuthority(id, data, label) {
  const target = $(id);
  if (!data.available) {
    target.textContent = data.message || `${label} evidence unavailable.`;
    return;
  }
  if (label === 'Evolution') {
    target.innerHTML = `<div class="timeline">${(data.events || []).map(event => `<div class="card"><b>${escapeHtml(event.stage || event.name || 'event')}</b><p>${escapeHtml(event.message || event.status || '')}</p></div>`).join('')}</div>`;
    return;
  }
  if (label === 'Memory') {
    target.innerHTML = `<div class="cards">${(data.memories || []).map(memory => `<div class="card"><b>${escapeHtml(memory.memory_id || 'memory')}</b><p>Status: ${escapeHtml(memory.status || 'unknown')}</p></div>`).join('')}</div>`;
    return;
  }
  target.textContent = json(data.metrics || data);
}

async function loadCases() {
  const data = await get('/api/cases'); const select = $('case-select');
  select.innerHTML = data.cases.map(c => `<option value="${escapeHtml(c.case_id)}">${escapeHtml(c.title)}</option>`).join('');
  const update = () => { const c = data.cases.find(row => row.case_id === select.value); $('case-description').textContent = `${c.description} Demo annotation: ${c.demo_annotation.recommended_lens}; runtime routing is computed from oracle evidence.`; };
  select.addEventListener('change', update); update();
}

async function runDemo() {
  $('run-demo').disabled = true; $('run-demo').textContent = 'Running EDA gates…';
  try {
    lastRun = await post('/api/run-case', {case_id:$('case-select').value, mode:'demo'});
    const diagnosis = lastRun.proposals.diagnosis;
    const first = diagnosis.first_divergence || {};
    $('diagnosis').textContent = [
      `Failure family: ${diagnosis.failure_family}`,
      `First mismatch: cycle ${first.cycle ?? 'n/a'} · ${first.signal ?? 'n/a'}`,
      `Expected / observed: ${first.expected ?? 'n/a'} / ${first.observed ?? 'n/a'}`,
      `Router allocation: ${(diagnosis.router_receipt?.allocated_lens_ids || []).join(', ')}`,
      `Evidence source: runtime oracle + RTL context`,
    ].join('\n');
    $('candidates').innerHTML = lastRun.verified_candidates.map(item => {
      const result = item.verification; const status = result.accepted ? 'pass' : 'fail';
      const scope = item.candidate.scope || {};
      return `<div class="card"><h3>${escapeHtml(item.candidate.id)} · ${escapeHtml(item.candidate.lens)}</h3><p>${escapeHtml(item.candidate.edit)}</p><pre class="diff">${escapeHtml(item.candidate.diff || '')}</pre><p>Patch scope: ${escapeHtml(scope.changed_lines)} lines / ${escapeHtml(scope.ast_edit_count)} AST nodes · Interface preserved: ${escapeHtml(scope.interface_preserved)}</p><div class="${escapeHtml(status)}">${result.accepted ? 'ACCEPT' : 'REJECT'}</div><small>${escapeHtml(result.authority)}</small></div>`;
    }).join('');
    showTrace(lastRun);
  } catch (error) { $('diagnosis').textContent = String(error); }
  $('run-demo').disabled = false; $('run-demo').textContent = 'Run guided demo';
}

async function init() {
  const h = await get('/api/health'); $('health').textContent = h.status === 'pass' ? 'EDA ready' : 'EDA NOT READY'; $('run-demo').disabled = h.status !== 'pass';
  await loadCases();
  $('run-demo').addEventListener('click', runDemo);
  showAuthority('evolution-data', await get('/api/evolution'), 'Evolution');
  showAuthority('memory-data', await get('/api/memory'), 'Memory');
  showAuthority('benchmark-data', await get('/api/benchmarks'), 'Benchmark');
}

document.querySelectorAll('.tabs button').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.tabs button,.page').forEach(node => node.classList.remove('active'));
  button.classList.add('active'); $(button.dataset.page).classList.add('active');
}));
init().catch(error => { $('health').textContent = `Backend error: ${error}`; });
