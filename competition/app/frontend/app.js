const $ = (id) => document.getElementById(id);
const json = (value) => JSON.stringify(value, null, 2);
let lastRun = null;

async function get(path) { const response = await fetch(path); if (!response.ok) throw new Error(await response.text()); return response.json(); }
async function post(path, body) { const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); if (!response.ok) throw new Error(await response.text()); return response.json(); }

function showTrace(run) {
  const rows = (run?.verified_candidates || []).flatMap(item => item.verification.stages.map(stage => `<div class="card"><b>${item.candidate.id} · ${stage.name.toUpperCase()}</b><div class="${stage.status}">${stage.status}</div></div>`));
  $('trace').innerHTML = `<div class="cards">${rows.join('')}</div>`;
}

async function loadCases() {
  const data = await get('/api/cases'); const select = $('case-select');
  select.innerHTML = data.cases.map(c => `<option value="${c.case_id}">${c.title}</option>`).join('');
  const update = () => { const c = data.cases.find(row => row.case_id === select.value); $('case-description').textContent = `${c.description} Recommended lens: ${c.recommended_lens}.`; };
  select.addEventListener('change', update); update();
}

async function runDemo() {
  $('run-demo').disabled = true; $('run-demo').textContent = 'Running EDA gates…';
  try {
    lastRun = await post('/api/run-case', {case_id:$('case-select').value, mode:'demo'});
    $('diagnosis').textContent = json(lastRun.proposals.diagnosis.evidence);
    $('candidates').innerHTML = lastRun.verified_candidates.map(item => {
      const result = item.verification; const status = result.accepted ? 'pass' : 'fail';
      return `<div class="card"><h3>${item.candidate.id} · ${item.candidate.lens}</h3><p>${item.candidate.edit}</p><div class="${status}">${result.accepted ? 'ACCEPT' : 'REJECT'}</div><small>${result.authority}</small></div>`;
    }).join('');
    showTrace(lastRun);
  } catch (error) { $('diagnosis').textContent = String(error); }
  $('run-demo').disabled = false; $('run-demo').textContent = 'Run guided demo';
}

async function init() {
  const h = await get('/api/health'); $('health').textContent = h.status === 'pass' ? 'EDA ready' : 'EDA not ready';
  await loadCases();
  $('run-demo').addEventListener('click', runDemo);
  $('evolution-data').textContent = json(await get('/api/evolution'));
  $('memory-data').textContent = json(await get('/api/memory'));
  $('benchmark-data').textContent = json(await get('/api/benchmarks'));
}

document.querySelectorAll('.tabs button').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.tabs button,.page').forEach(node => node.classList.remove('active'));
  button.classList.add('active'); $(button.dataset.page).classList.add('active');
}));
init().catch(error => { $('health').textContent = `Backend error: ${error}`; });
