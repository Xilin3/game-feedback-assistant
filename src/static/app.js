const $ = (id) => document.getElementById(id);
const labels = {pending:'待复核',confirmed:'已确认',insufficient:'信息不足',excluded:'已排除',done:'已完成',failed:'失败',running:'分析中'};
const categories = ['Bug','性能','操作体验','玩法建议','其他'];
const state = {batch:null, group:null, mode:'text', checked:new Set(), timer:null, config:null};
const escape = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let toastTimer;
function toast(message, error=false) { $('toast').textContent=message; $('toast').className=error?'error':''; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').className='hidden',6500); }
async function api(path, method='GET', data) {
  const res=await fetch(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:data===undefined?undefined:JSON.stringify(data)});
  const result=await res.json(); if(!res.ok) throw new Error(result.error || '请求失败'); return result;
}
function guarded(fn) {return async (...args)=>{try{await fn(...args);}catch(e){toast(e.message,true);}};}
async function loadList() {
  const batches=await api('/api/batches');
  $('batch-list').innerHTML=batches.length?batches.map(b=>`<button class="batch-link ${state.batch?.batch_id===b.batch_id?'active':''}" data-batch="${escape(b.batch_id)}">${escape(b.game_name)}<small>${b.count} 条评论 · ${escape(b.imported_at.slice(0,10))}</small></button>`).join(''):'<p class="muted">还没有分析批次</p>';
}
function setMode(mode){state.mode=mode;document.querySelectorAll('[data-mode]').forEach(el=>el.classList.toggle('active',el.dataset.mode===mode));$('file-label').classList.toggle('hidden',mode!=='json');$('input-label').innerHTML=mode==='json'?'JSON 内容<span class="muted">支持批次对象或评论数组</span>':'评论内容<span class="muted">每行一条评论，最多 1000 条</span>';}
function stopPoll(){clearTimeout(state.timer);state.timer=null;}
function setImportSource(source) {
  document.querySelectorAll('[data-source]').forEach(el=>{
    el.classList.toggle('active',el.dataset.source===source);
    el.setAttribute('aria-pressed',String(el.dataset.source===source));
  });
  $('steam-form').classList.toggle('hidden',source!=='steam');
  $('import-form').classList.toggle('hidden',source!=='local');
}
document.querySelectorAll('[data-source]').forEach(el=>el.onclick=()=>setImportSource(el.dataset.source));
function poll(){stopPoll();if(state.batch?.status==='running'){const bid=state.batch.batch_id;state.timer=setTimeout(async()=>{try{const batch=await api(`/api/batches/${bid}`);if(state.batch?.batch_id!==bid)return;state.batch=batch;render();poll();}catch(e){toast(e.message,true);poll();}},1200);}}
async function openBatch(bid){stopPoll();const batch=await api(`/api/batches/${bid}`);state.batch=batch;state.group=null;state.checked.clear();$('welcome').classList.add('hidden');$('workspace').classList.remove('hidden');render();await loadList();poll();}
function render(){
  const b=state.batch,s=b.stats;const running=b.status==='running';
  renderModelStatus();
  $('batch-title').textContent=b.game_name;$('batch-source').textContent=b.source_description;$('breadcrumb').textContent=b.game_name;
  ['total','groups','done','confirmed'].forEach(k=>$(`stat-${k}`).textContent=s[k]);
  $('stat-progress').textContent=`${s.failed} 条失败 · ${s.pending+s.running} 条待完成`;
  $('export').href=`/api/batches/${b.batch_id}/report`;
  $('source-export').href=`/api/batches/${b.batch_id}/source`;
  $('analysis-label').textContent=running?'正在逐条分析…':s.done===s.total?'分析完成，开始复核':s.failed?'部分评论分析失败':'准备分析';
  $('analysis-help').textContent=running?`已完成 ${s.done} / ${s.total}，结果会自动保存。`:`共 ${s.requests} 次请求尝试；重试只处理失败或未完成的评论。`;
  $('analyze').disabled=running||s.done===s.total;$('provider').disabled=running;
  document.querySelectorAll('[data-provider]').forEach(button=>{
    button.disabled=running;
    button.setAttribute('aria-pressed',String(button.dataset.provider===$('provider').value));
    button.classList.toggle('active',button.dataset.provider===$('provider').value);
  });
  $('analyze').textContent=running?'分析中…':s.failed?'重试未完成项 →':'开始分析 →';
  const demo=b.reviews.some(r=>r.analysis?.provider==='demo');
  $('mode-notice').textContent=demo?'本批次包含离线规则演示结果，仅用于体验流程。要测试大模型效果，请新建批次。':$('provider').value==='demo'?'离线演示只识别少量预设表达，用于体验流程，不能代替 AI 分析。':'评论原文和分组摘要将发送至配置的模型服务。结果需要人工复核。';
  renderGroups();renderDetail();
  $('review-count').textContent=`（${s.total} 条）`;
  $('reviews').innerHTML=b.reviews.map(r=>`<div class="review-record"><span class="badge">${escape(r.review_id)}</span> <span class="${r.status==='failed'?'error-text':'muted'}">${labels[r.status]||'待分析'} · 尝试 ${r.attempts} 次</span><p>${escape(r.text)}</p>${r.source_url?`<a href="${escape(r.source_url)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a>`:''}${r.error?`<p class="error-text">${escape(r.error)}</p>`:''}</div>`).join('');
  $('audit').innerHTML=b.audit.length?b.audit.slice().reverse().map(a=>`<div class="audit-record">${escape(a.action)} <span class="muted">${escape(a.at)}</span><details><summary>查看修改详情</summary><pre>${escape(JSON.stringify(a.detail,null,2))}</pre></details></div>`).join(''):'<p class="muted">尚无人工修正记录。</p>';
}
function renderGroups(){
  const b=state.batch;if(!b)return;
  const q=$('search').value.trim().toLowerCase(),filter=$('state-filter').value;
  const groups=b.groups.filter(g=>(filter==='all'||g.state===filter)&&(!q||`${g.title} ${g.category}`.toLowerCase().includes(q)||b.issues.some(i=>i.group_id===g.id&&i.evidence.toLowerCase().includes(q)))).sort((a,b)=>b.review_count-a.review_count);
  $('group-count').textContent=`${groups.length} 个分组`;
  $('group-list').innerHTML=groups.length?groups.map(g=>`<div class="group-row ${g.id===state.group?'selected':''}"><input type="checkbox" data-check="${g.id}" aria-label="选择 ${escape(g.title)}" ${state.checked.has(g.id)?'checked':''} ${b.status==='running'?'disabled':''}><button class="group-open" data-group="${g.id}"><strong>${escape(g.title)}</strong><span class="group-meta"><span>${escape(g.category)}</span><span class="badge ${g.state}">${labels[g.state]}</span></span></button><span class="count">${g.review_count} <small>条</small></span></div>`).join(''):`<div class="empty"><span class="empty-symbol">◎</span>${b.groups.length?'没有符合筛选条件的问题':'问题清单将在分析后出现'}<br>每个问题都会保留对应的原文证据。</div>`;
  $('merge').disabled=state.checked.size<2||b.status==='running';
}
function renderDetail(){
  const b=state.batch,g=b.groups.find(g=>g.id===state.group);
  if(!g){$('detail').innerHTML='<div class="empty"><span class="empty-symbol">≋</span>选择一个问题，查看它的来处。<br>原文证据、待核实信息和复核操作都在这里。</div>';return;}
  const members=b.issues.filter(i=>i.group_id===g.id);const disabled=b.status==='running'?'disabled':'';
  $('detail').innerHTML=`<div class="detail-heading">问题详情 / ${g.review_count} 条独立评论</div><h2>${escape(g.title)}</h2><form id="edit-form"><fieldset ${disabled} class="edit-fields"><label>问题标题<input id="edit-title" value="${escape(g.title)}" required maxlength="500"></label><div class="two-col"><label>分类<select id="edit-category">${categories.map(c=>`<option ${c===g.category?'selected':''}>${c}</option>`).join('')}</select></label><label>复核状态<select id="edit-state">${['pending','confirmed','insufficient','excluded'].map(s=>`<option value="${s}" ${s===g.state?'selected':''}>${labels[s]}</option>`).join('')}</select></label></div><label>人工备注<textarea id="edit-notes" rows="2" maxlength="5000" placeholder="记录核实结果或后续跟进事项">${escape(g.notes)}</textarea></label><div class="form-bottom"><span class="muted">修改会写入复核记录</span><button class="primary">保存修改</button></div></fieldset></form><div class="subheading"><strong>原文证据 · ${members.length} 项</strong><button id="split" class="text-button" ${disabled}>拆分所选 ↗</button></div>${members.map(i=>{const r=b.reviews.find(r=>r.review_id===i.review_id);return `<article class="evidence"><label><input type="checkbox" data-issue="${i.id}" ${disabled}>${escape(i.review_id)}</label><blockquote>${escape(i.evidence)}</blockquote>${i.trigger_context.length?`<p>触发条件：${escape(i.trigger_context.join('；'))}</p>`:''}${i.attempted_actions.length?`<p>已尝试：${escape(i.attempted_actions.join('；'))}</p>`:''}<details><summary>展开完整原文</summary><div class="original">${escape(r.text)}</div>${r.source_url?`<a target="_blank" rel="noopener noreferrer" href="${escape(r.source_url)}">查看来源 ↗</a>`:''}</details></article>`;}).join('')}<h3 class="subheading">待核实信息</h3><p class="muted">${escape(g.missing_information.join('；')||'未列出，仍需人工判断。')}</p>`;
  $('edit-form').onsubmit=guarded(async e=>{e.preventDefault();await mutate(`/groups/${g.id}`,'PATCH',{title:$('edit-title').value,category:$('edit-category').value,state:$('edit-state').value,notes:$('edit-notes').value});toast('复核修改已保存');});
  $('split').onclick=()=>{const ids=selectedIssues();if(!ids.length||ids.length===members.length){toast('请选择部分证据拆分，原分组至少保留一项',true);return;}$('split-title').value=g.title;$('split-dialog').showModal();};
}
function selectedIssues(){return [...document.querySelectorAll('[data-issue]:checked')].map(el=>el.dataset.issue);}
async function mutate(suffix,method,data){const bid=state.batch.batch_id;const updated=await api(`/api/batches/${bid}${suffix}`,method,data);if(state.batch?.batch_id!==bid)return;state.batch=updated;render();}
$('new-batch').onclick=()=>{stopPoll();state.batch=null;state.group=null;$('workspace').classList.add('hidden');$('welcome').classList.remove('hidden');$('breadcrumb').textContent='开始整理';guarded(loadList)();};
$('batch-list').onclick=guarded(async e=>{const btn=e.target.closest('[data-batch]');if(btn)await openBatch(btn.dataset.batch);});
document.querySelectorAll('[data-mode]').forEach(el=>el.onclick=()=>setMode(el.dataset.mode));
$('load-example').onclick=guarded(async()=>{const data=await api('/api/example');setMode('json');$('game-name').value=data.game_name;$('source').value=data.source_description;$('review-input').value=JSON.stringify(data,null,2);});
$('json-file').onchange=guarded(async e=>{const file=e.target.files[0];if(!file)return;if(file.size>4*1024*1024)throw new Error('文件不能超过 4 MB');const text=await file.text();const data=JSON.parse(text.replace(/^\uFEFF/,''));$('review-input').value=text;if(data.game_name)$('game-name').value=data.game_name;if(data.source_description)$('source').value=data.source_description;});
$('import-form').onsubmit=guarded(async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const payload={game_name:$('game-name').value,source_description:$('source').value,force_new:$('force-new').checked};payload[state.mode==='json'?'data':'text']=$('review-input').value;const b=await api('/api/batches','POST',payload);await openBatch(b.batch_id);toast(`已载入 ${b.stats.total} 条评论${b.duplicates_skipped?`，跳过 ${b.duplicates_skipped} 条重复 ID`:''}；相同输入默认复用已有批次`);}finally{button.disabled=false;}});
$('analyze').onclick=guarded(async()=>{$('analyze').disabled=true;try{const bid=state.batch.batch_id;await api(`/api/batches/${bid}/analyze`,'POST',{provider:$('provider').value});await openBatch(bid);}catch(e){$('analyze').disabled=false;throw e;}});
$('provider').onchange=()=>{if(state.batch)render();};
document.querySelectorAll('[data-provider]').forEach(button=>button.onclick=()=>{
  $('provider').value=button.dataset.provider;
  if(state.batch)render();
});
$('search').oninput=renderGroups;$('state-filter').onchange=renderGroups;
$('group-list').onclick=e=>{const el=e.target.closest('[data-group]');if(el){state.group=el.dataset.group;renderGroups();renderDetail();}};
$('group-list').onchange=e=>{if(e.target.dataset.check){const id=e.target.dataset.check;e.target.checked?state.checked.add(id):state.checked.delete(id);$('merge').disabled=state.checked.size<2;}};
$('merge').onclick=guarded(async()=>{const ids=[...state.checked];await mutate('/merge','POST',{group_ids:ids});state.group=ids[0];state.checked.clear();render();toast('已合并，分类采用首选分组，请重新复核');});
$('cancel-split').onclick=()=>$('split-dialog').close();
$('split-form').onsubmit=guarded(async e=>{e.preventDefault();await mutate(`/groups/${state.group}/split`,'POST',{issue_ids:selectedIssues(),title:$('split-title').value});$('split-dialog').close();toast('已拆分为新的待复核分组');});
guarded(async()=>{state.config=await api('/api/config');$('provider').value=state.config.default_provider==='compatible'?'compatible':'demo';await loadList();})();

function renderModelStatus() {
  $('model-status').textContent=state.config?.configured?`已配置模型：${state.config.model}`:'尚未配置真实 AI 模型';
}
document.querySelectorAll('[data-model-settings]').forEach(button=>button.onclick=guarded(async()=>{
  state.config=await api('/api/config');
  $('model-base').value=state.config.api_base;
  $('model-name').value=state.config.model;
  $('model-key').value='';
  $('model-key').required=!state.config.has_api_key;
  $('model-key-status').textContent=state.config.has_api_key?'密钥已配置；留空保留原密钥。':'尚未配置密钥。';
  $('model-feedback').textContent='';
  $('model-dialog').showModal();
}));
$('model-cancel').onclick=()=>$('model-dialog').close();
$('model-dialog').addEventListener('close',()=>{$('model-key').value='';});
$('model-form').onsubmit=async e=>{
  e.preventDefault();
  $('model-save').disabled=true;
  $('model-feedback').textContent='正在保存…';
  try {
    state.config=await api('/api/config','POST',{api_base:$('model-base').value.trim(),
      api_key:$('model-key').value,model:$('model-name').value.trim()});
    if(state.batch?.status!=='running')$('provider').value='compatible';
    $('model-dialog').close();
    renderModelStatus();
    if(state.batch)render();
    toast('模型配置已保存；新分析立即生效（尚未验证服务连接）');
  } catch(e) { $('model-feedback').textContent=e.message; }
  finally { $('model-save').disabled=false; }
};

$('steam-form').onsubmit=guarded(async e=>{
  e.preventDefault();
  $('steam-submit').disabled=true;
  $('steam-progress').textContent='正在分页获取，请稍候。完成前请勿关闭页面。';
  try {
    const b=await api('/api/steam/import','POST',{
      appid:$('steam-appid').value,count:Number($('steam-count').value),
      game_name:$('steam-name').value,language:$('steam-language').value,
      review_type:$('steam-type').value,refresh:$('steam-refresh').checked
    });
    await openBatch(b.batch_id);
    toast(`已导入 ${b.stats.total} 条 Steam 真实评论，尚未分析`);
    $('steam-progress').textContent=`已获取 ${b.stats.total} 条评论`;
  } catch(e) {
    $('steam-progress').textContent=e.message;
    throw e;
  } finally { $('steam-submit').disabled=false; }
});
