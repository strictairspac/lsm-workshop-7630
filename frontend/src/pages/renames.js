import{renames}from'../stores/rename.js';
import{datasets}from'../stores/dataset.js';
import{create,liveWrite,decide,requestSwitch,readKey}from'../api/rename.js';
import{header}from'../components/common/header.js';
import{table}from'../components/common/table.js';
import{badge}from'../components/common/badge.js';
import{esc,json}from'../utils/html.js';
import{submit,notify}from'../hooks/feedback.js';
import{session}from'../stores/session.js';
import{RENAME_JOB,RENAME_GROUP}from'../constants/enums.js';

const rbadge=s=>`<span class="badge ${esc(s==='switched'?'passed':(s==='failed'||s==='stale'?'failed':''))}">${esc(RENAME_JOB[s]||RENAME_GROUP[s]||s)}</span>`;
const attempt=()=>'att-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8);

export async function renamesPage(el){
  const sub=location.hash.split('/')[1];
  if(sub)return renameDetail(el,Number(sub));
  const rows=await renames.load();const ds=await datasets.load();
  const writable=session.get().role!=='viewer';
  el.innerHTML=header('04','把多个旧叫法一次性归到同一新名','在工坊选演练批次，配置旧名→新名规则。多个活键归到同一新名时由值班员裁决保留哪个值；裁决期间的并发改值或删键会让旧选择失效，必须重判。切换后新旧两种叫法读到同一个值。')
    +`<div class="split"><section class="panel"><span class="material-tag">RENAME SPECIMENS / ${rows.length}</span>${
      rows.length?table(['演练','规则/撞名','状态','版本','操作'],rows.map(x=>[
        esc(x.name)+'<br><small>#'+x.id+' / 活键 '+x.live_key_count+' / 共 '+x.key_count+'</small>',
        x.rules.length+' 条规则 · '+x.groups.filter(g=>g.needs_choice).length+' 个撞名组',
        rbadge(x.status),
        'v'+x.state_version,
        `<a href="#renames/${x.id}">进入裁决台 →</a>`])):'<div class="empty">还没有改名演练。先在右侧配置规则并选择演练批次。</div>'}</section>
    <aside class="panel"><h2>${writable?'配置改名规则':'规则契约'}</h2>${writable?`<form id="rename-form">
      <label>演练名称</label><input name="name" required maxlength="120" value="rename-${Date.now().toString().slice(-6)}">
      <label>演练批次（可选，取其 put 序列作为初始活键）</label><select name="dataset_id"><option value="">不选批次，稍后注入并发写入</option>${ds.map(x=>`<option value="${x.id}">${esc(x.name)} / ${x.operation_count}条</option>`).join('')}</select>
      <label>改名规则 · JSON 数组（old_key → new_key）</label><textarea name="rules" rows="8" required>[
  {"old_key":"temp-north","new_key":"temperature"},
  {"old_key":"temp_beiling","new_key":"temperature"},
  {"old_key":"hum-01","new_key":"humidity"}
]</textarea><div class="actions"><button class="primary" type="submit">创建改名演练</button></div></form>`:''}
      <p class="helper">1–500 条规则；同一旧名只能出现一次；不支持 A→B、B→C 的链路改名。多个旧名指向同一新名即形成撞名组，组内超过一个活键就需要值班员裁决。</p></aside></div>`;
  if(!writable)return;
  el.querySelector('#rename-form').onsubmit=e=>{e.preventDefault();submit(e.target,async()=>{
    const d=Object.fromEntries(new FormData(e.target));
    let rules;try{rules=JSON.parse(d.rules)}catch{throw Error('规则JSON格式错误')}
    const payload={name:d.name,rules,source_note:'页面创建',entries:[]};
    if(d.dataset_id){
      const set=ds.find(x=>String(x.id)===d.dataset_id);
      payload.entries=(set?.entries||[]).filter(x=>x.op==='put').map(x=>({op:'put',key:x.key,value:x.value}));
      payload.source_note=set?set.name:'';
    }
    const j=await create(payload);notify('改名演练已创建：'+(j.pending_groups.length?'有撞名组待裁决':'无撞名，可直接切换'));
    location.hash='renames/'+j.id;
  })};
}

async function renameDetail(el,id){
  const job=await renames.open(id);const writable=session.get().role!=='viewer'&&job.status!=='switching'&&job.status!=='switched';
  const members=g=>g.members.map(m=>{
    const dead=m.state==='tombstone'||m.state==='missing';
    const win=job.groups.find(x=>x.group_ref===g.group_ref)?.decision?.winner_key===m.key;
    return `<tr><td class="mono">${esc(m.key)}${m.key===g.new_key?' <small>(新名自身)</small>':''}</td>
      <td>${dead?'<span class="muted">'+(m.state==='missing'?'不存在':'已删除（墓碑）')+'</span>':esc(m.value)}</td>
      <td>v${m.version}</td><td>${win?'<span class="badge passed">已选保留</span>':''}</td></tr>`;
  }).join('');
  el.innerHTML=header('04',esc(job.name),'裁决台版本 v'+job.state_version+' · '+RENAME_JOB[job.status]+(job.switched_at?' · 切换于 '+esc(job.switched_at):''))
    +`<div class="actions" style="margin-bottom:20px"><a href="#renames">← 返回演练列表</a><button id="refresh">刷新状态</button>${rbadge(job.status)}</div>
    ${job.last_event?`<p class="modelnote">最近事件：${esc(job.last_event.event||job.last_event.op||'switch')} · ${esc(job.last_event.key||'')} ${job.last_event.invalidated_groups?.length?'· 失效组：'+esc(job.last_event.invalidated_groups.join(',')):''}</p>`:''}
    <div class="split"><section class="panel"><h2>撞名裁决组（按新名归并）</h2>${
      job.groups.map(g=>`<article class="record"><h3><span class="mono">${esc(g.new_key)}</span> ${rbadge(g.status)}
        ${g.decision?.state==='invalidated'?'<span class="badge failed">先前选择已失效</span>':''}</h3>
        <div class="tablewrap"><table><thead><tr><th>来源键</th><th>当前值</th><th>版本</th><th>裁决</th></tr></thead><tbody>${members(g)}</tbody></table></div>
        ${g.needs_choice&&writable?`<form data-group="${esc(g.group_ref)}" class="decide-form"><label>保留哪个活键的值（乐观版本 ${job.state_version}）</label>
          <select name="winner_key">${g.members.filter(m=>m.state==='live').map(m=>`<option value="${esc(m.key)}">${esc(m.key)} = ${esc(m.value)}（v${m.version}）</option>`).join('')}</select>
          <input type="hidden" name="expected_version" value="${job.state_version}">
          <div class="actions"><button type="submit">确认裁决 →</button></div></form>`:''}
        ${g.decision&&!g.needs_choice?`<p class="helper">单活键组，切换时自动取唯一活值，无需人工裁决。</p>`:''}
      </article>`).join('')}</section>
    <aside class="panel"><h2>并发站点写入</h2>${writable?`<form id="live-form">
        <label>操作</label><select name="op"><option value="put">put 改值</option><option value="delete">delete 删键（墓碑）</option></select>
        <label>键名（组内任一旧名或新名）</label><input name="key" required maxlength="64" placeholder="temp-north">
        <label>value（put 必填）</label><input name="value" maxlength="200" placeholder="22.9">
        <div class="actions"><button type="submit">注入并发写入</button></div></form>
        <p class="helper">每次写入推高状态版本；组内改值或删键会让该组先前裁决立即失效，值班员需按最新值重判。</p>
        <h2 style="margin-top:30px">一次性切换</h2>
        <div class="actions">${job.ready?'<button class="primary" id="switch-btn">'+(job.status==='failed'?'修正后重新切换 →':'原子切换并启用别名 →')+'</button>':'<button disabled>还有组待裁决</button>'}</div>
        ${job.status==='failed'?'<p class="error">上一轮切换失败：修正撞名组后用新 attempt 重新驱动，已完成的切换保持幂等，同次重试只启用一版。</p>':''}
        <p class="helper">切换在单个事务内完成：新名取裁决值、全部旧名立碑、旧名→新名别名同时生效。中断后 worker 按 attempt 幂等恢复，同次重试只启用一版。</p>`
      :`<p class="helper">观察员只读。切换后可在下方用新旧两种键名查值核对。</p>`}
      <h2 style="margin-top:30px">新旧键查值</h2><form id="read-form"><label>键名（旧名或新名）</label><input name="key" required maxlength="64" placeholder="temp-north 或 temperature">
        <div class="actions"><button type="submit">查值</button></div></form><div id="read-result" class="helper"></div></aside></div>`;
  el.querySelector('#refresh').onclick=()=>renamesPage(el);
  el.querySelectorAll('.decide-form').forEach(f=>f.onsubmit=e=>{e.preventDefault();submit(e.target,async()=>{
    const d=Object.fromEntries(new FormData(e.target));d.expected_version=Number(d.expected_version);
    await decide(id,d);notify('裁决已记录');await renamesPage(el);
  })});
  const lf=el.querySelector('#live-form');
  if(lf)lf.onsubmit=e=>{e.preventDefault();submit(e.target,async()=>{
    const d=Object.fromEntries(new FormData(e.target));
    const payload={op:d.op,key:d.key};if(d.op==='put')payload.value=d.value;
    const r=await liveWrite(id,payload);
    notify(r.invalidated_groups.length?'并发写入已使裁决失效：'+r.invalidated_groups.join(','):'并发写入已记录（v'+r.state_version+'）');
    await renamesPage(el);
  })};
  const sb=el.querySelector('#switch-btn');
  if(sb)sb.onclick=()=>submit(sb,async()=>{await requestSwitch(id,attempt());notify('切换任务已提交，worker 执行中；如本地无 worker，可点直执按钮');await renamesPage(el)});
  el.querySelector('#read-form').onsubmit=e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));
    readKey(id,d.key).then(r=>{el.querySelector('#read-result').innerHTML=`<pre class="json">${json(r)}</pre>
      <div>${r.found?`读到值 <strong>${esc(r.value)}</strong>`:'键不存在或为墓碑（无值）'}<br>规范名：<span class="mono">${esc(r.canonical_name)}</span> · 别名跳数 ${r.alias_hops} · ${r.is_alias?'旧名别名命中':'规范名直读'}</div>`}).catch(e=>notify(e.message))};
}
