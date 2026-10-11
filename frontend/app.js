/* Sin datos simulados: las tarjetas y evidencias se alimentan de la API. */
const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value ?? '—').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const sedeLabel = (sede) => ({tunja:'Tunja',duitama:'Duitama',sogamoso:'Sogamoso',aguazul:'Aguazul'}[sede] || sede);
const configuredNodes = [
  {nombre:'tunja-academico',etiqueta:'Tunja',sede:'tunja',rol:'academico'},
  {nombre:'duitama-academico',etiqueta:'Duitama',sede:'duitama',rol:'academico'},
  {nombre:'sogamoso-academico',etiqueta:'Sogamoso',sede:'sogamoso',rol:'academico'},
  {nombre:'aguazul-academico',etiqueta:'Aguazul',sede:'aguazul',rol:'academico'},
  {nombre:'tunja-central',etiqueta:'Central · Tunja',sede:'tunja',rol:'central'},
];
const state = {view:'distribucion',offset:0,limit:25,total:0,refreshing:false,students:[],nodes:[],listRequest:0,detailRequest:0};
const viewLabels = {distribucion:'Distribución',estudiantes:'Estudiantes',registro:'Registro',fragmentos:'Fragmentos',validacion:'Validación'};

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(path, {...options, signal:controller.signal, headers:{'Content-Type':'application/json', ...options.headers}});
    const data = await response.json();
    if (!response.ok) {
      let message = data.error?.mensaje || data.detail?.message || data.detail;
      if (Array.isArray(message)) message = message.map((item) => `${item.loc?.slice(1).join('.')}: ${item.msg}`).join(' · ');
      throw new Error(typeof message === 'string' ? message : `No se pudo completar la consulta (HTTP ${response.status}).`);
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('La consulta superó el tiempo límite. Comprueba los nodos del clúster.');
    if (error instanceof TypeError) throw new Error('No se pudo conectar con la API. Comprueba que el backend esté ejecutándose.');
    throw error;
  } finally { clearTimeout(timeout); }
}

function showNotice(message, isError = true) {
  const notice = $('#notice');
  notice.textContent = message;
  notice.classList.toggle('error', isError);
  notice.hidden = false;
}
function clearNotice() { $('#notice').hidden = true; }
function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toast.timeout);
  toast.timeout = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}

function switchView(view) {
  if (!viewLabels[view]) return;
  state.view = view;
  for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== `view-${view}`;
  for (const button of document.querySelectorAll('.nav-item')) {
    const active = button.dataset.view === view;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  }
  $('#section-label').textContent = viewLabels[view];
  clearNotice();
  if (view === 'estudiantes' && !state.students.length) loadStudents();
  $('#main').focus({preventScroll:true});
  window.scrollTo({top:0, behavior:'instant'});
}

async function busy(button, action, text = 'Consultando…') {
  if (button.disabled) return;
  const original = button.innerHTML;
  button.disabled = true;
  button.textContent = text;
  clearNotice();
  try { await action(); } catch (error) { showNotice(error.message); }
  finally { button.disabled = false; button.innerHTML = original; }
}

function evidenceDetails(data) {
  return `<details class="evidence-details"><summary>Ver respuesta de la API</summary><pre>${escapeHTML(JSON.stringify(data,null,2))}</pre></details>`;
}
function gatewayText(evidence) {
  const node = evidence?.nodo_conexion;
  if (!node) return 'Nodo de conexión no informado';
  const known = state.nodes.find((item) => item.node_id === node.node_id);
  return `${known?.etiqueta || 'Nodo'} · ID ${node.node_id ?? '—'} (${node.host || '—'}:${node.port || '—'})`;
}

function executionNodeLabel(id, fallbackName) {
  const match = String(id ?? '').match(/^n?(\d+)$/);
  if (!match) return 'Nodo no informado';
  const number = Number(match[1]);
  const known = state.nodes.find((node) => node.node_id != null && Number(node.node_id) === number);
  const name = known?.etiqueta || configuredNodes.find((node) => node.nombre === fallbackName)?.etiqueta;
  return name ? `${name} (n${number})` : `Nodo n${number} (nombre no disponible)`;
}

function executionEvidenceHTML(evidence) {
  // Todas las sedes se consultan por separado: cada plan conserva su gateway.
  const queries = evidence?.consultas_por_sede || [evidence];
  const executed = queries.filter((query) => query?.plan);
  if (!executed.length) return '';
  return `<div class="execution-evidence"><strong>EXPLAIN ANALYZE · Lectura ejecutada</strong>${executed.map((query) => {
    const plan = query.plan;
    const gateway = query.nodo_conexion;
    const readers = (plan.kv_nodes || []).map((id) => executionNodeLabel(id)).join(', ') || 'No informados por el plan';
    const sqlNodes = (plan.sql_nodes || []).map((id) => executionNodeLabel(id)).join(', ') || 'No informados por el plan';
    return `<div class="execution-query">${queries.length > 1 ? `<span class="execution-campus">${escapeHTML(sedeLabel(query.sede))}</span>` : ''}<p class="execution-summary"><strong>Conexión:</strong> ${escapeHTML(executionNodeLabel(gateway?.node_id, gateway?.nombre))} <span aria-hidden="true">·</span> <strong>Datos leídos en:</strong> ${escapeHTML(readers)}</p><p class="execution-sql">Nodos SQL del plan: ${escapeHTML(sqlNodes)}</p><details class="execution-plan"><summary>Ver plan ejecutado${queries.length > 1 ? ` · ${escapeHTML(sedeLabel(query.sede))}` : ''}</summary><pre>${escapeHTML((plan.texto || []).join('\n'))}</pre></details></div>`;
  }).join('')}</div>`;
}

function renderNodes(nodes, known = false) {
  $('#node-grid').innerHTML = nodes.map((node, index) => {
    const central = node.rol === 'central';
    const available = known && node.disponible;
    const status = known ? (available ? 'Disponible' : 'Sin conexión') : 'Sin comprobar';
    return `<article class="node-card ${central ? 'central' : ''} ${known ? (available ? '' : 'offline') : 'unknown'}"><div class="node-top"><span class="node-number">NODO ${node.node_id != null ? String(node.node_id).padStart(2,'0') : '—'}</span><span class="node-state"><i class="status-dot ${known ? (available ? '' : 'offline') : 'unknown'}"></i>${status}</span></div><h3>${escapeHTML(node.etiqueta)}</h3><div class="node-role">${central ? 'Sede central · Contactos' : 'Sede · Datos académicos'}</div><div class="node-icon" aria-hidden="true">▤</div><span class="node-fragment">${central ? 'V2 · Contacto de todas las sedes' : 'V1 · Fragmento académico'}</span><div class="node-meta"><span>ID <b>${node.node_id ?? '—'}</b></span><span>Registros <b>${node.registros ?? '—'}</b></span></div>${node.error ? `<p class="node-error">${escapeHTML(node.error)}</p>` : node.error_conteo ? `<p class="node-error">Conteo no disponible</p>` : ''}</article>`;
  }).join('');
}

async function refreshCluster() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const data = await request('/api/nodos');
    state.nodes = data.nodos;
    renderNodes(data.nodos, true);
    const available = data.nodos.filter((node) => node.disponible).length;
    $('#metric-nodes').innerHTML = `${available}<small>/ ${data.nodos.length}</small>`;
    $('#metric-node-caption').textContent = available === data.nodos.length ? 'Todos los nodos responden a /health' : 'Hay nodos sin respuesta a /health';
    const academic = data.nodos.filter((node) => node.rol === 'academico');
    const countAvailable = academic.length === 4 && academic.every((node) => Number.isInteger(node.registros));
    $('#metric-students').textContent = countAvailable ? academic.reduce((sum,node) => sum+node.registros,0).toLocaleString('es-CO') : '—';
    $('#metric-student-caption').textContent = countAvailable ? 'Conteo real de fragmentos académicos' : 'No se pudo completar el conteo de todas las sedes';
    $('#api-status').textContent = available === data.nodos.length ? 'API conectada al clúster' : 'API activa · clúster parcial';
    $('#api-indicator').className = 'status-dot';
    $('#last-update').textContent = `Actualizado ${new Date(data.consultado_en || Date.now()).toLocaleTimeString('es-CO',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}`;
  } catch (error) {
    // Una API inaccesible no demuestra que los nodos estén caídos.
    state.nodes = [];
    renderNodes(configuredNodes, false);
    $('#metric-nodes').innerHTML = '—<small>/ 5</small>';
    $('#metric-students').textContent = '—';
    $('#metric-node-caption').textContent = 'Estado no comprobado';
    $('#metric-student-caption').textContent = 'Conteo no comprobado';
    $('#api-status').textContent = 'Estado del clúster no comprobado';
    $('#api-indicator').className = 'status-dot unknown';
    $('#last-update').textContent = 'La última comprobación no se completó';
  } finally { state.refreshing = false; }
}

async function loadDistribution() {
  const data = await request('/api/distribucion');
  const rows = data.fragmentos.flatMap((fragment) => fragment.rangos.map((range) => ({...range,tabla:fragment.tabla,tipo:fragment.tabla === 'estudiantes_academicos' ? 'academico' : 'contacto'})));
  const container = $('#distribution-content');
  container.className = 'panel';
  container.innerHTML = `<div class="distribution-header">${escapeHTML(rows.length)} rangos de fragmentos · Conexión: ${escapeHTML(gatewayText(data.evidencia))}</div><div class="table-scroll"><table><thead><tr><th>Fragmento</th><th>Nodo esperado</th><th>Nodo observado</th><th>Estado / filas</th></tr></thead><tbody>${data.fragmentos.map((fragment) => `<tr><td>${escapeHTML(fragment.particion ? sedeLabel(fragment.particion) : 'Contacto · todas las sedes')}</td><td>${escapeHTML(fragment.nodo_esperado)}</td><td>${escapeHTML(fragment.nodo_real)}<span class="student-id">${fragment.ubicacion_correcta ? 'Ubicación correcta' : 'Revisar ubicación'}</span></td><td>${fragment.disponible ? 'Disponible' : 'No disponible'} · ${fragment.filas ?? '—'}</td></tr>`).join('')}</tbody></table></div><details class="evidence-details" style="padding:15px 20px"><summary>Ver rangos e índices</summary><div class="table-scroll"><table><thead><tr><th>Tabla / índice</th><th>Rango</th><th>Réplicas</th><th>Localidades</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHTML(row.tabla)}<span class="student-id">${escapeHTML(row.index_name)}</span></td><td>${escapeHTML(row.range_id)}</td><td>${escapeHTML((row.replicas || []).join(', '))}</td><td>${escapeHTML((row.replica_localities || []).join(' · '))}</td></tr>`).join('')}</tbody></table></div></details><div style="padding:0 20px 15px">${evidenceDetails(data)}</div>`;
}

function renderStudentRows(rows) {
  $('#student-rows').innerHTML = rows.map((student) => `<tr><td><span class="student-name">${escapeHTML(student.nombre)}</span><span class="student-id">${escapeHTML(student.id)}</span></td><td><span class="sede-tag">${escapeHTML(sedeLabel(student.sede))}</span></td><td>${escapeHTML(student.programa)}</td><td>${escapeHTML(student.semestre)}</td><td><button class="text-button student-detail" data-id="${escapeHTML(student.id)}" data-sede="${escapeHTML(student.sede)}">Ver fragmentos ↗</button></td></tr>`).join('') || '<tr><td colspan="5" class="table-empty">No hay estudiantes en esta consulta.</td></tr>';
}

async function loadStudents() {
  const requestId = ++state.listRequest;
  clearNotice();
  $('#student-rows').innerHTML = '<tr><td colspan="5" class="table-empty">Consultando el fragmento académico…</td></tr>';
  $('#previous-page').disabled = true;
  $('#next-page').disabled = true;
  $('#list-evidence').hidden = true;
  const query = new URLSearchParams({limite:state.limit,desplazamiento:state.offset});
  const sede = $('#filter-sede').value;
  if (sede) query.set('sede',sede);
  const programa = $('#filter-program').value.trim();
  if (programa) query.set('programa',programa);
  if ($('#filter-explain').checked) query.set('explicar','true');
  try {
    if ($('#filter-explain').checked) await refreshCluster();
    const data = await request(`/api/estudiantes?${query}`);
    if (requestId !== state.listRequest) return;
    state.students = data.estudiantes;
    state.total = data.total;
    renderStudentRows(data.estudiantes);
    $('#list-count').textContent = `${data.total} estudiantes ${sede ? `de ${sedeLabel(sede)}` : 'en todas las sedes'}`;
    if (data.parcial) showNotice(`Listado parcial. Sedes no disponibles: ${data.sedes_no_disponibles.map(sedeLabel).join(', ')}.`, false);
    $('#page-label').textContent = `Página ${Math.floor(state.offset/state.limit)+1}`;
    $('#previous-page').disabled = state.offset === 0;
    $('#next-page').disabled = state.offset + state.limit >= data.total;
    $('#list-evidence').hidden = false;
    $('#list-evidence').innerHTML = `${executionEvidenceHTML(data.evidencia) || `<strong>Consulta al fragmento académico.</strong> Conexión: ${escapeHTML(gatewayText(data.evidencia))}. Marca «Incluir EXPLAIN ANALYZE» para ver qué nodos leyeron los datos.`}${evidenceDetails(data)}`;
  } catch (error) {
    if (requestId !== state.listRequest) return;
    state.students = [];
    $('#list-count').textContent = 'Consulta no completada';
    $('#student-rows').innerHTML = `<tr><td colspan="5" class="table-empty">${escapeHTML(error.message)}</td></tr>`;
    showNotice(error.message);
  }
}

function locationText(location) {
  if (!location) return 'Ubicación no comprobada';
  const ids = location.replicas || [];
  const labels = ids.map((id) => state.nodes.find((node) => node.node_id === id)?.etiqueta || `Nodo ${id}`);
  return `Réplicas: ${labels.join(', ') || 'no informadas'} · Rango ${location.range_id ?? '—'}`;
}
function fieldsHTML(record) {
  const labels = {id:'ID',sede:'Sede',nombre:'Nombre',programa:'Programa',semestre:'Semestre',correo:'Correo',telefono:'Teléfono',direccion:'Dirección'};
  return `<dl class="fields">${Object.entries(record).map(([key,value]) => `<dt>${escapeHTML(labels[key] || key)}</dt><dd ${['id','sede'].includes(key) ? 'class="key"' : ''}>${escapeHTML(key === 'sede' ? sedeLabel(value) : value)}</dd>`).join('')}</dl>`;
}

async function loadDetail(id,sede) {
  const requestId = ++state.detailRequest;
  $('#detail-id').value = id;
  $('#detail-sede').value = sede;
  const target = $('#detail-result');
  target.className = 'panel empty-state';
  target.innerHTML = '<span class="empty-symbol">⋈</span><h3>Consultando los dos fragmentos…</h3><p>El motor ejecuta un JOIN por sede e identificador.</p>';
  try {
    const data = await request(`/api/estudiantes/${encodeURIComponent(sede)}/${encodeURIComponent(id)}`);
    if (requestId !== state.detailRequest) return;
    const locations = data.evidencia?.ubicaciones;
    target.className = 'detail-shell';
    if (!data.completo) {
      target.innerHTML = `<div class="fragment-grid"><article class="panel fragment-card"><header><span class="fragment-number academic">V1</span><div><h3>Fragmento académico disponible</h3></div></header>${fieldsHTML(data.academico)}</article><article class="panel fragment-card"><header><span class="fragment-number contact">V2</span><div><h3>Contacto no disponible</h3></div></header><p>La consulta de reconstrucción superó el tiempo límite. Se muestran los datos académicos; el registro completo no pudo reconstruirse.</p></article></div><div class="query-evidence">Detalle parcial · Conexión: ${escapeHTML(gatewayText(data.evidencia))}.${evidenceDetails(data)}</div>`;
      return;
    }
    target.innerHTML = `<div class="fragment-grid"><article class="panel fragment-card"><header><span class="fragment-number academic">V1</span><div><h3>Fragmento académico</h3><p>${escapeHTML(locationText(locations?.academico))}</p></div></header>${fieldsHTML(data.fragmentos.academico)}</article><article class="panel fragment-card"><header><span class="fragment-number contact">V2</span><div><h3>Fragmento de contacto</h3><p>${escapeHTML(locationText(locations?.contacto))}</p></div></header>${fieldsHTML(data.fragmentos.contacto)}</article></div><div class="join-banner"><span>CLAVE COMPARTIDA</span><code>a.sede = c.sede AND a.id = c.id</code><span>↓ JOIN ↓</span></div><article class="panel reconstructed"><h3>Registro reconstruido</h3>${fieldsHTML(data.estudiante)}${evidenceDetails(data)}</article><div class="query-evidence">Reconstrucción ejecutada mediante SQL JOIN. Conexión: ${escapeHTML(gatewayText(data.evidencia))}.</div>`;
  } catch (error) {
    if (requestId !== state.detailRequest) return;
    target.className = 'panel empty-state';
    target.innerHTML = `<span class="empty-symbol">!</span><h3>Reconstrucción no disponible</h3><p>${escapeHTML(error.message)}</p>`;
    showNotice(error.message);
  }
}

async function registerStudent() {
  const form = $('#register-form');
  const fields = Object.fromEntries(new FormData(form));
  fields.semestre = Number(fields.semestre);
  $('#register-result').hidden = true;
  const data = await request('/api/estudiantes',{method:'POST',body:JSON.stringify(fields)});
  const result = $('#register-result');
  result.hidden = false;
  result.innerHTML = `<div class="success-heading"><span class="success-mark">✓</span><h3>Estudiante registrado</h3></div><p class="muted small">La transacción se confirmó con los dos fragmentos. ID: <code>${escapeHTML(data.estudiante.id)}</code></p><p class="small">Académico: ${escapeHTML(locationText(data.evidencia?.ubicaciones?.academico))}<br>Contacto: ${escapeHTML(locationText(data.evidencia?.ubicaciones?.contacto))}</p>${data.evidencia?.advertencia ? `<p class="muted small">${escapeHTML(data.evidencia.advertencia)}</p>` : ''}<button class="button secondary student-detail" data-id="${escapeHTML(data.estudiante.id)}" data-sede="${escapeHTML(data.estudiante.sede)}">Ver registro reconstruido ↗</button>${evidenceDetails(data)}`;
  form.reset();
  toast('Transacción confirmada. Estudiante registrado.');
  refreshCluster();
}

async function runValidation() {
  $('#validation-results').innerHTML = '';
  $('#validation-summary').innerHTML = '<span class="validation-symbol">…</span><div><h2>Comprobando el estado actual</h2><p>Las reglas se consultan en el motor; un fallo de consulta se identifica como no comprobado.</p></div>';
  let data;
  try {
    data = await request('/api/validaciones');
  } catch (error) {
    $('#validation-summary').innerHTML = '<span class="validation-symbol">!</span><div><h2>Validación no completada</h2><p>No se pudieron obtener los resultados actuales de la API.</p></div>';
    throw error;
  }
  const passed = data.resultados.filter((item) => item.estado === 'ok').length;
  $('#validation-summary').innerHTML = `<span class="validation-symbol">${data.correcto ? '✓' : '!'}</span><div><h2>${passed} de ${data.resultados.length} comprobaciones correctas</h2><p>${escapeHTML(data.nota || 'Resultados obtenidos de consultas reales al motor.')}</p><p>Consulta: ${escapeHTML(new Date(data.consultado_en).toLocaleString('es-CO'))}</p></div>`;
  $('#validation-results').innerHTML = data.resultados.map((item) => `<article class="panel validation-card"><header><h3>${escapeHTML(item.nombre)}</h3><span class="result-badge ${item.estado === 'ok' ? '' : item.estado === 'fallo' ? 'fail' : 'unknown'}">${item.estado === 'ok' ? 'Correcto' : item.estado === 'fallo' ? 'Revisar' : 'No comprobado'}</span></header><p>${escapeHTML(item.detalle)}</p>${item.incidencias != null ? `<p>Incidencias: <strong>${escapeHTML(item.incidencias)}</strong></p>` : ''}${item.problemas?.length ? `<p>${escapeHTML(item.problemas.join(' '))}</p>` : ''}${evidenceDetails(item)}</article>`).join('');
}

document.addEventListener('click', (event) => {
  const nav = event.target.closest('[data-view]');
  const link = event.target.closest('[data-go]');
  const detail = event.target.closest('.student-detail');
  if (nav) switchView(nav.dataset.view);
  if (link) switchView(link.dataset.go);
  if (detail) { switchView('fragmentos'); loadDetail(detail.dataset.id,detail.dataset.sede); }
});
$('#refresh-cluster').addEventListener('click', () => busy($('#refresh-cluster'),refreshCluster));
$('#load-distribution').addEventListener('click', () => busy($('#load-distribution'),loadDistribution));
$('#list-form').addEventListener('submit',(event) => { event.preventDefault();state.offset=0;loadStudents(); });
$('#previous-page').addEventListener('click', () => {state.offset=Math.max(0,state.offset-state.limit);loadStudents();});
$('#next-page').addEventListener('click', () => {state.offset+=state.limit;loadStudents();});
$('#detail-form').addEventListener('submit',(event) => {event.preventDefault();loadDetail($('#detail-id').value.trim(),$('#detail-sede').value);});
$('#register-form').addEventListener('submit',(event) => {event.preventDefault();busy($('#register-submit'),registerStudent,'Guardando…');});
$('#run-validation').addEventListener('click',() => busy($('#run-validation'),runValidation,'Validando…'));

renderNodes(configuredNodes);
refreshCluster();
setInterval(() => {if (!document.hidden) refreshCluster();},8000);
