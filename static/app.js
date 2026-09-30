let state={campaigns:[], leads:[], desk:null, view:'dashboard'};
let selectedLead=null;
const $=s=>document.querySelector(s);
const $$=s=>document.querySelectorAll(s);
function toast(m, bad){
  const el=$('#toast'); el.textContent=m; el.classList.toggle('bg-red-600', !!bad); el.classList.remove('hidden');
  clearTimeout(toast.t); toast.t=setTimeout(()=>el.classList.add('hidden'), 3800);
}
async function api(path, opts={}){
  const r=await fetch(path,{headers:{'Content-Type':'application/json', ...(opts.headers||{})}, ...opts});
  const j=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.error||j.detail||'Request failed');
  return j;
}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

// NAV
$$('.nav-btn').forEach(b=>{
  b.addEventListener('click', ()=>{
    $$('.nav-btn').forEach(x=>x.classList.remove('bg-white/10','text-white'));
    b.classList.add('bg-white/10','text-white');
    const v=b.dataset.view;
    state.view=v;
    $('#view-title').textContent=b.textContent.trim();
    document.querySelectorAll('main section').forEach(s=>s.classList.add('hidden'));
    const id=v==='desk'?'view-desk':'view-'+v;
    document.getElementById(id)?.classList.remove('hidden');
    if(v==='dashboard') loadDashboard();
    if(v==='campaigns') loadCampaigns();
    if(v==='leads') loadLeads();
    if(v==='research') loadResearch();
    if(v==='inbox') loadInbox();
    if(v==='followups') loadFollowups();
    if(v==='suppression') loadSuppression();
    if(v==='accounts') loadAccounts();
    if(v==='analytics') loadAnalytics();
    if(v==='settings') loadSettings();
    if(v==='desk') loadDesk();
  });
});

// DASHBOARD
async function loadDashboard(){
  try{
    const {analytics}=await api('/api/analytics');
    const {campaigns}=await api('/api/campaigns');
    const {leads}=await api('/api/leads?limit=6');
    const {replies}=await api('/api/replies');
    const runs=await api('/api/agent-runs');
    // metrics
    const metrics=[
      ['Leads', analytics.total_leads],
      ['Qualified', analytics.qualified],
      ['Verified', analytics.verified],
      ['Sent', analytics.sent],
      ['Replies', analytics.replies],
      ['Positive', analytics.positive],
      ['Campaigns', analytics.campaigns],
    ];
    $('#metrics-grid').innerHTML=metrics.map(([k,v])=>`<div class="card p-4"><div class="text-xs uppercase tracking-widest opacity-60">${k}</div><div class="text-2xl font-semibold mt-1">${v}</div><div class="text-xs mono opacity-60">${k==='Replies'? analytics.reply_rate+'% reply rate':''}</div></div>`).join('');
    // industry breakdown
    const ib=analytics.by_industry||{};
    $('#industry-breakdown').innerHTML=Object.keys(ib).length? Object.entries(ib).map(([k,v])=>`<div class="flex justify-between border-b py-1"><span>${esc(k)}</span><span class="mono">${v}</span></div>`).join(''):'<span class="text-slate-400">No data</span>';
    // recent leads
    $('#recent-leads').innerHTML=(leads||[]).slice(0,6).map(l=>`<div class="flex justify-between text-sm border-b py-1.5"><span>${esc(l.company)} <span class="mono text-xs opacity-60">${esc(l.lead_score||l.tier||'')}</span></span><span class="badge ${scoreColor(l.lead_score)}">${esc(l.lead_status||l.status)}</span></div>`).join('')||'<span class="text-slate-400 text-sm">No leads</span>';
    // recent replies
    $('#recent-replies').innerHTML=(replies||[]).slice(0,5).map(r=>`<div class="border-b py-2"><div class="font-medium text-sm">${esc(r.classification)} • ${esc(r.from_email)}</div><div class="text-xs opacity-60">${esc(r.body?.slice(0,100))}</div></div>`).join('')||'<span class="text-slate-400">No replies</span>';
    // upcoming followups = jobs pending
    const jobs=await api('/api/jobs');
    const pending=(jobs.jobs||[]).filter(j=>j.status==='pending'&&j.job_type==='sendFollowup').slice(0,5);
    $('#upcoming-followups').innerHTML=pending.length? pending.map(j=>`<div class="border-b py-1.5 flex justify-between"><span class="mono text-xs">${esc(j.payload_json.slice(0,60))}</span><span class="text-xs">${esc(j.created_at.slice(0,10))}</span></div>`).join(''):'<span class="text-slate-400">No pending follow-ups</span>';
    // agent activity
    $('#agent-activity').innerHTML=(runs.runs||[]).slice(0,20).map(r=>`<div>[${esc(r.created_at.slice(11,19))}] <b>${esc(r.agent_name)}</b> ${esc(r.status)} ${esc(r.error||'')} <span class="opacity-60">${esc((r.input_json||'').slice(0,80))}</span></div>`).join('')||'No activity yet';
    // campaign chart simple bars
    if(campaigns.length){
      const max=Math.max(...campaigns.map(c=>c.id),1);
      $('#campaign-chart').innerHTML=`<div class="w-full space-y-2">${campaigns.slice(0,4).map(c=>`<div class="flex items-center gap-2 text-sm"><span class="w-32 truncate">${esc(c.name)}</span><div class="flex-1 h-2 bg-slate-100 rounded"><div class="h-2 bg-[#0ea5e9] rounded" style="width:${Math.min(100, c.id/max*100)}%"></div></div><span class="mono text-xs">${esc(c.status)}</span></div>`).join('')}</div>`;
    }
    // clock
    const desk=await api('/api/desk');
    $('#clock').textContent=desk.clock.today+' • '+desk.clock.sent_today+'/'+desk.clock.daily_cap+' sent';
  }catch(e){ /* ignore */ }
}
function scoreColor(s){ if(s==='A+'||s==='A') return 'bg-emerald-100 text-emerald-700'; if(s==='A-'||s==='B+') return 'bg-amber-100 text-amber-700'; if(s) return 'bg-slate-100'; return 'bg-slate-100'; }

// CAMPAIGNS
async function loadCampaigns(){
  const {campaigns}=await api('/api/campaigns');
  state.campaigns=campaigns;
  $('#leads-count').textContent=campaigns.length? ''+campaigns.length:'';
  // campaign filter options
  const sel=$('#lead-campaign-filter');
  if(sel) sel.innerHTML='<option value="">All campaigns</option>'+campaigns.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  const list=$('#campaigns-list');
  if(!campaigns.length){ list.innerHTML='<div class="text-sm text-slate-400">No campaigns yet. Create one below.</div>'; return; }
  list.innerHTML=campaigns.map(c=>`<div class="card p-4 flex flex-col lg:flex-row lg:items-center gap-3">
    <div class="flex-1"><div class="font-medium">${esc(c.name)}</div><div class="text-xs mono opacity-60">${esc(c.city||'')}${c.country?', '+esc(c.country):''} • ${esc((c.industries||[]).join(', '))} • ${esc(c.lead_type)} • ${c.rating_min}-${c.rating_max} • ≥${c.review_min} reviews</div><div class="text-xs mt-1">Depth ${esc(c.research_depth)} • ${esc(c.status)} ${c.auto_send?'• autopilot':''}</div></div>
    <div class="flex gap-2">
      <button data-act="start-campaign" data-id="${c.id}" data-mode="research_draft" class="bg-[#0f172a] text-white px-3 py-1.5 rounded-lg text-sm">Start (5)</button>
      <button data-act="start-campaign" data-id="${c.id}" data-mode="autopilot" class="bg-[#0ea5e9] text-white px-3 py-1.5 rounded-lg text-sm">Autopilot</button>
      <button data-act="pause-campaign" data-id="${c.id}" class="border px-3 py-1.5 rounded-lg text-sm">Pause</button>
      <button data-act="view-leads" data-id="${c.id}" class="border px-3 py-1.5 rounded-lg text-sm">View leads</button>
    </div>
  </div>`).join('');
}
// --- Keywords dropdown (replaces free-text) ---
let selectedKeywords = new Set();
function syncKeywords(){
  const hidden=$('#keywords-hidden');
  const display=$('#keywords-display');
  const selectedEl=$('#keywords-selected');
  const pills=$('#keywords-pills');
  const vals=[...selectedKeywords];
  if(hidden) hidden.value = vals.join(', ');
  if(selectedEl) selectedEl.textContent = vals.length ? vals.join(', ') : '—';
  if(display){
    if(!vals.length) { display.textContent='Select keywords…'; display.classList.add('text-slate-400'); display.classList.remove('text-slate-800'); }
    else { display.textContent = vals.length + ' selected'; display.classList.remove('text-slate-400'); display.classList.add('text-slate-800'); }
  }
  if(pills){
    pills.innerHTML = vals.map(v=>`<span class="inline-flex items-center gap-1 bg-[#0f172a] text-white text-xs px-2.5 py-1 rounded-full">${esc(v)} <button type="button" data-kw="${esc(v)}" class="kw-remove ml-0.5 opacity-70 hover:opacity-100">×</button></span>`).join('') || '<span class="text-xs opacity-60">No keywords selected — agent will use general operational signals.</span>';
  }
  // sync checkboxes
  document.querySelectorAll('.kw-cb').forEach(cb=>{
    cb.checked = selectedKeywords.has(cb.value);
  });
}
document.addEventListener('click', e=>{
  const toggle=e.target.closest('#keywords-toggle');
  const wrap=e.target.closest('#keywords-wrap');
  const dd=$('#keywords-dropdown');
  if(toggle){
    e.preventDefault();
    dd?.classList.toggle('hidden');
  } else if(!wrap && dd && !dd.classList.contains('hidden')){
    dd.classList.add('hidden');
  }
  const rm=e.target.closest('.kw-remove');
  if(rm){
    selectedKeywords.delete(rm.dataset.kw);
    syncKeywords();
  }
});
document.addEventListener('change', e=>{
  if(e.target.classList.contains('kw-cb')){
    if(e.target.checked) selectedKeywords.add(e.target.value);
    else selectedKeywords.delete(e.target.value);
    syncKeywords();
  }
});
$('#keywords-add-custom')?.addEventListener('click', ()=>{
  const inp=$('#keywords-custom');
  const v=(inp.value||'').trim().toLowerCase();
  if(!v) return;
  if(v.length<2 || v.length>30) return toast('Keyword must be 2–30 chars', true);
  selectedKeywords.add(v);
  inp.value='';
  syncKeywords();
});
$('#keywords-custom')?.addEventListener('keydown', e=>{
  if(e.key==='Enter'){ e.preventDefault(); $('#keywords-add-custom').click(); }
});
$('#keywords-clear')?.addEventListener('click', ()=>{
  selectedKeywords.clear();
  syncKeywords();
});
syncKeywords();

// --- Industries dropdown (also fixes "constuction" typo) ---
let selectedIndustries = new Set(['construction company']);
function syncIndustries(){
  const hidden=$('#industries-hidden');
  const display=$('#industries-display');
  const selectedEl=$('#industries-selected');
  const pills=$('#industries-pills');
  const vals=[...selectedIndustries];
  if(hidden) hidden.value = vals.join(', ');
  if(selectedEl) selectedEl.textContent = vals.length ? vals.join(', ') : '—';
  if(display){
    if(!vals.length) { display.textContent='Select industries…'; display.classList.add('text-slate-400'); }
    else { display.textContent = vals.join(', '); display.classList.remove('text-slate-400'); }
  }
  if(pills){
    pills.innerHTML = vals.map(v=>`<span class="inline-flex items-center gap-1 bg-sky-100 text-sky-800 text-xs px-2.5 py-1 rounded-full">${esc(v)} <button type="button" data-ind="${esc(v)}" class="ind-remove ml-0.5 opacity-70 hover:opacity-100">×</button></span>`).join('') || '<span class="text-xs opacity-60">Pick from dropdown — e.g., Property Management, Construction, Cleaning</span>';
  }
  document.querySelectorAll('.ind-cb').forEach(cb=>{
    // match lowercased?
    const match = [...selectedIndustries].some(s=> s.toLowerCase()===cb.value.toLowerCase());
    cb.checked = match;
  });
}
document.addEventListener('click', e=>{
  const toggle=e.target.closest('#industries-toggle');
  const wrap=e.target.closest('#industries-wrap');
  const dd=$('#industries-dropdown');
  if(toggle){
    e.preventDefault();
    dd?.classList.toggle('hidden');
  } else if(!wrap && dd && !dd.classList.contains('hidden') && !e.target.closest('.ind-remove')){
    // Close only if click outside wrap (handled by keywords handler too)
    // Let keywords handler handle its own; this is for industries
    if(!e.target.closest('#keywords-wrap')) dd.classList.add('hidden');
  }
  const rm=e.target.closest('.ind-remove');
  if(rm){
    const target=[...selectedIndustries].find(s=> s===rm.dataset.ind || s.toLowerCase()===rm.dataset.ind.toLowerCase());
    if(target) selectedIndustries.delete(target);
    syncIndustries();
  }
});
document.addEventListener('change', e=>{
  if(e.target.classList.contains('ind-cb')){
    const val=e.target.value;
    if(e.target.checked){
      // add with original casing
      selectedIndustries.add(val);
    } else {
      // remove case-insensitively
      const found=[...selectedIndustries].find(s=> s.toLowerCase()===val.toLowerCase());
      if(found) selectedIndustries.delete(found);
    }
    syncIndustries();
  }
});
$('#industries-add-custom')?.addEventListener('click', ()=>{
  const inp=$('#industries-custom');
  const v=(inp.value||'').trim();
  if(!v) return;
  if(v.length<2 || v.length>40) return toast('Industry must be 2–40 chars', true);
  selectedIndustries.add(v);
  inp.value='';
  syncIndustries();
});
$('#industries-custom')?.addEventListener('keydown', e=>{
  if(e.key==='Enter'){ e.preventDefault(); $('#industries-add-custom').click(); }
});
$('#industries-clear')?.addEventListener('click', ()=>{
  selectedIndustries.clear();
  syncIndustries();
});
syncIndustries();

// --- Dependent campaign location dropdowns ---
const countrySelect = $('#campaign-country');
const stateSelect = $('#campaign-state');
const citySelect = $('#campaign-city');
const locationSelectClass = 'w-full border rounded-lg px-3 py-2 mt-1';
function setLocationOptions(select, values, placeholder, selected=''){
  select.innerHTML = `<option value="">${esc(placeholder)}</option>` + values.map(value=>
    `<option value="${esc(value)}"${value===selected?' selected':''}>${esc(value)}</option>`
  ).join('');
  select.disabled = values.length === 0;
  select.className = locationSelectClass;
}
async function loadLocationCountries(){
  try{
    const {countries}=await api('/api/locations/countries');
    const current=countrySelect.value || 'Nigeria';
    setLocationOptions(countrySelect, countries, 'Select a country', current);
    if(!countrySelect.value) countrySelect.value = countries.includes('Nigeria') ? 'Nigeria' : (countries[0] || '');
    await loadLocationStates(countrySelect.value, 'Lagos State');
  }catch(err){
    stateSelect.disabled=true;
    citySelect.disabled=true;
    toast('Could not load location lists. Check your internet connection and reload.', true);
  }
}
async function loadLocationStates(country, preferred=''){
  setLocationOptions(stateSelect, [], 'Loading states/regions…');
  setLocationOptions(citySelect, [], 'Select a state/region first');
  if(!country) return;
  try{
    const {states}=await api(`/api/locations/states?country=${encodeURIComponent(country)}`);
    const selected=states.includes(preferred) ? preferred : '';
    setLocationOptions(stateSelect, states, states.length ? 'Select a state/region' : 'No states/regions available', selected);
    if(selected) await loadLocationCities(country, selected, 'Lagos');
  }catch(err){
    setLocationOptions(stateSelect, [], 'States/regions unavailable');
    toast(err.message, true);
  }
}
async function loadLocationCities(country, region, preferred=''){
  setLocationOptions(citySelect, [], 'Loading cities…');
  if(!country || !region){
    setLocationOptions(citySelect, [], 'Select a state/region first');
    return;
  }
  try{
    const {cities}=await api(`/api/locations/cities?country=${encodeURIComponent(country)}&state=${encodeURIComponent(region)}`);
    const selected=cities.includes(preferred) ? preferred : '';
    setLocationOptions(citySelect, cities, cities.length ? 'Select a city' : 'No cities available', selected);
  }catch(err){
    setLocationOptions(citySelect, [], 'Cities unavailable');
    toast(err.message, true);
  }
}
countrySelect?.addEventListener('change', ()=>loadLocationStates(countrySelect.value));
stateSelect?.addEventListener('change', ()=>loadLocationCities(countrySelect.value, stateSelect.value));
loadLocationCountries();

$('#campaign-form').addEventListener('submit', async e=>{
  e.preventDefault();
  const form = e.currentTarget; // capture before await (event pooling)
  const fd=new FormData(form);
  // dropdowns store comma-separated in hidden inputs
  const kwRaw = ($('#keywords-hidden')?.value) || fd.get('keywords') || '';
  const indRaw = ($('#industries-hidden')?.value) || fd.get('industries') || '';
  const payload={
    name: fd.get('name'),
    country: fd.get('country'),
    state: fd.get('state'),
    city: fd.get('city'),
    industries: String(indRaw||'').split(',').map(s=>s.trim()).filter(Boolean),
    lead_type: fd.get('lead_type'),
    rating_min: parseFloat(fd.get('rating_min')),
    rating_max: parseFloat(fd.get('rating_max')),
    review_min: parseInt(fd.get('review_min')),
    keywords: String(kwRaw||'').split(',').map(s=>s.trim()).filter(Boolean),
    daily_send_limit: parseInt(fd.get('daily_send_limit')),
    research_depth: fd.get('research_depth'),
    auto_send: fd.get('auto_send')==='on'
  };
  try{
    await api('/api/campaigns',{method:'POST', body: JSON.stringify(payload)});
    toast('Campaign created');
    form.reset();
    selectedKeywords.clear();
    syncKeywords();
    // restore industries from reset hidden (or default)
    const after = String($('#industries-hidden')?.value || '').split(',').map(s=>s.trim()).filter(Boolean);
    selectedIndustries = new Set(after.length ? after : ['construction company']);
    syncIndustries();
    loadCampaigns();
  }catch(err){ toast(err.message,true); }
});
$('#btn-create-campaign').addEventListener('click',()=> document.getElementById('campaign-form').scrollIntoView({behavior:'smooth'}));
$('#btn-new-campaign').addEventListener('click',()=>{
  document.querySelector('[data-view="campaigns"]').click();
  setTimeout(()=> $('#campaign-form').scrollIntoView({behavior:'smooth'}), 100);
});
document.addEventListener('click', async e=>{
  const b=e.target.closest('[data-act="start-campaign"]');
  if(b){
    const id=b.dataset.id, mode=b.dataset.mode;
    b.textContent='Starting…'; b.disabled=true;
    try{
      toast('Discovery started — this can take ~30s');
      const res=await api(`/api/campaigns/${id}/start`,{method:'POST', body: JSON.stringify({mode, limit:5})});
      toast(`Discovered ${res.discovered}, added ${res.added} (${res.mode})`);
      loadCampaigns(); loadLeads(); loadDashboard();
    }catch(err){ toast(err.message,true); }
    b.disabled=false; b.textContent=mode==='autopilot'?'Autopilot':'Start (5)';
  }
  const p=e.target.closest('[data-act="pause-campaign"]');
  if(p){ try{ await api(`/api/campaigns/${p.dataset.id}/pause`,{method:'POST'}); toast('Paused'); loadCampaigns(); }catch(err){toast(err.message,true)} }
  const v=e.target.closest('[data-act="view-leads"]');
  if(v){ document.querySelector('[data-view="leads"]').click(); $('#lead-campaign-filter').value=v.dataset.id; loadLeads(); }
});

// LEADS
async function loadLeads(){
  const q=$('#lead-search').value.trim();
  const status=$('#lead-status-filter').value;
  const camp=$('#lead-campaign-filter').value;
  const params=new URLSearchParams();
  if(q) params.set('q', q);
  if(status) params.set('status', status);
  if(camp) params.set('campaign_id', camp);
  const {leads, total}=await api('/api/leads?'+params.toString());
  state.leads=leads;
  const tbl=$('#leads-table');
  if(!leads.length){ tbl.innerHTML='<div class="text-sm text-slate-400 p-4">No leads yet. Start a campaign.</div>'; return; }
  tbl.innerHTML=`<table class="w-full text-sm">
    <thead class="text-xs uppercase opacity-60"><tr><th class="text-left p-2">Company</th><th class="text-left p-2">Industry</th><th class="text-left p-2">Email</th><th class="text-left p-2">Score</th><th class="text-left p-2">Value</th><th class="text-left p-2">Status</th><th class="p-2"></th></tr></thead>
    <tbody>${leads.map(l=>`<tr class="border-t hover:bg-slate-50">
      <td class="p-2"><div class="font-medium">${esc(l.company)}</div><div class="mono text-xs opacity-60">${esc(l.domain||l.website||'')}</div><div class="text-xs opacity-60">${esc(l.city||'')} ${esc(l.lead_type||'')}</div></td>
      <td class="p-2">${esc(l.industry||'')}</td>
      <td class="p-2"><div class="mono text-xs">${esc(l.email||'')}</div><div class="text-xs ${l.email_verified?'text-emerald-600':'text-amber-600'}">${l.email_verified?'verified':'pending'} ${esc(l.email_confidence||'')}<br>${esc(l.email_source_url?.slice(0,30)||'')}</div></td>
      <td class="p-2"><span class="badge ${scoreColor(l.lead_score)}">${esc(l.lead_score||'—')}</span></td>
      <td class="p-2">${esc(l.project_value||'')}</td>
      <td class="p-2"><span class="badge bg-slate-100">${esc(l.lead_status||l.status)}</span></td>
      <td class="p-2 flex gap-1 flex-wrap">
        <button data-act="open-lead" data-id="${l.id}" class="border px-2 py-1 rounded text-xs">View</button>
        <button data-act="research-lead" data-id="${l.id}" class="border px-2 py-1 rounded text-xs">Research</button>
        <button data-act="reject-lead" data-id="${l.id}" class="border px-2 py-1 rounded text-xs">Reject</button>
      </td>
    </tr>`).join('')}</tbody></table><div class="mono text-xs opacity-60 mt-2">Total ${total}</div>`;
}
$('#lead-search').addEventListener('input', debounce(loadLeads,300));
$('#lead-status-filter').addEventListener('change', loadLeads);
$('#lead-campaign-filter').addEventListener('change', loadLeads);
$('#btn-export').addEventListener('click', async ()=>{
  const camp=$('#lead-campaign-filter').value;
  const {csv}=await api('/api/export'+(camp?`?campaign_id=${camp}`:''));
  const blob=new Blob([csv],{type:'text/csv'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a'); a.href=url; a.download='leads.csv'; a.click(); URL.revokeObjectURL(url);
});
function debounce(fn,d){let t;return (...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),d)};}
// --- Streaming research (like Folio) ---
async function streamResearch(leadId, traceEl){
  traceEl = traceEl || document.createElement('div');
  traceEl.innerHTML = '<div class="mono text-xs p-3">Starting streaming research…</div>';
  traceEl.classList.remove('hidden');
  try{
    const resp = await fetch(`/api/leads/${leadId}/research/stream`, {headers: {Accept: 'text/event-stream'}});
    if(!resp.ok || !resp.body){
      const j = await resp.json().catch(()=>({}));
      throw new Error(j.error || 'Stream failed');
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let lastLead = null;
    while(true){
      const {done, value} = await reader.read();
      if(done) break;
      buffer += decoder.decode(value, {stream:true});
      let idx;
      while((idx = buffer.indexOf('\n\n')) !== -1){
        const chunk = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx+2);
        if(!chunk) continue;
        // chunk is like "data: {...}"
        const lines = chunk.split('\n');
        for(const line of lines){
          if(!line.startsWith('data:')) continue;
          const json = line.slice(5).trim();
          if(!json) continue;
          try{
            const ev = JSON.parse(json);
            if(ev.type === 'step' || ev.type === 'progress'){
              const icon = ev.node==='agent'?'🤖': ev.node==='discover'?'🔍': ev.node==='read'?'📄': ev.node==='socials'?'👥': ev.node==='recent'?'🕒': ev.node==='summarize'?'📝':'•';
              const ms = ev.ms ? ` <span class="opacity-40">${ev.ms}ms</span>` : '';
              const detail = ev.message ? `<span class="opacity-80">${esc(ev.message)}</span>` : '';
              // compact urls/socials if present
              let extra = '';
              if(ev.urls) extra = `<div class="opacity-60 text-[11px] mt-1 truncate">${ev.urls.slice(0,3).map(u=>esc(u)).join(' • ')}</div>`;
              if(ev.socials) extra = `<div class="opacity-60 text-[11px] mt-1">${ev.socials.map(s=>esc(s.network+':'+(s.handle||''))).join(' • ')}</div>`;
              if(ev.pages) extra = `<div class="opacity-60 text-[11px] mt-1">${ev.pages.slice(0,3).map(p=>esc(p.title||p.url)).join(' • ')}</div>`;
              traceEl.insertAdjacentHTML('beforeend', `<div class="border-b border-white/10 py-2 flex gap-2"><span>${icon}</span><div class="flex-1"><span class="font-semibold">${esc(ev.title||ev.node)}</span> — ${detail}${ms}${extra}</div></div>`);
              traceEl.scrollTop = traceEl.scrollHeight;
            } else if(ev.type === 'answer'){
              traceEl.insertAdjacentHTML('beforeend', `<div class="py-2 text-emerald-300">✓ ${esc(ev.message||'Research ready')} — ${ev.result?.pages_fetched||0} pages, ${ev.result?.evidence_claims?.length||0} claims</div>`);
              if(ev.result?.business_age) traceEl.insertAdjacentHTML('beforeend', `<div class="text-xs opacity-70 mt-1">History: ${esc(ev.result.business_age.slice(0,120))}</div>`);
              if(ev.result?.services?.length) traceEl.insertAdjacentHTML('beforeend', `<div class="text-xs opacity-70">Services: ${ev.result.services.slice(0,2).map(s=>esc(s.slice(0,80))).join(' • ')}</div>`);
            } else if(ev.type === 'done'){
              lastLead = ev.lead;
            } else if(ev.type === 'error'){
              traceEl.insertAdjacentHTML('beforeend', `<div class="py-2 text-red-300">⚠ ${esc(ev.message)}</div>`);
            }
          }catch(e){ /* ignore */ }
        }
      }
    }
    return lastLead;
  }catch(err){
    traceEl.insertAdjacentHTML('beforeend', `<div class="py-2 text-amber-300">Stream error — falling back: ${esc(err.message)}</div>`);
    return null;
  }
}
document.addEventListener('click', async e=>{
  const b=e.target.closest('[data-act="open-lead"]');
  if(b) openLead(parseInt(b.dataset.id));
  const r=e.target.closest('[data-act="research-lead"]');
  if(r){
    const id=r.dataset.id;
    r.textContent='Streaming…'; r.disabled=true;
    // Create a tiny inline trace under the button? Just stream without UI, fallback to toast
    try{
      const lead = await streamResearch(id, null);
      if(lead){
        toast('Researched — streaming done');
      } else {
        await api(`/api/leads/${id}/research`,{method:'POST'});
        toast('Researched');
      }
      loadLeads();
    }catch(err){ toast(err.message,true) }
    r.textContent='Research'; r.disabled=false;
  }
  const rej=e.target.closest('[data-act="reject-lead"]');
  if(rej){ try{ await api(`/api/leads/${rej.dataset.id}/reject`,{method:'POST', body:JSON.stringify({reason:'manual'})}); toast('Rejected'); loadLeads(); }catch(err){toast(err.message,true)} }
});

// Lead detail drawer
async function openLead(id){
  const {lead, opportunity, evidence, replies}=await api(`/api/leads/${id}`);
  selectedLead=lead;
  const drawer=$('#lead-drawer');
  const el=$('#lead-detail');
  el.innerHTML=`
    <div class="flex justify-between items-start gap-4"><div><h2 class="text-xl font-semibold">${esc(lead.company)}</h2><div class="mono text-xs opacity-60">${esc(lead.website)} • ${esc(lead.domain)} • ${esc(lead.city)} • ${esc(lead.industry)}</div><div class="mt-2 flex gap-2"><span class="badge ${scoreColor(lead.lead_score)}">${esc(lead.lead_score||'unscored')}</span><span class="badge bg-slate-100">${esc(lead.project_value||'')}</span><span class="badge bg-slate-100">${esc(lead.lead_status)}</span></div></div><button onclick="document.getElementById('lead-drawer').close()" class="border px-3 py-1.5 rounded-lg text-sm">Close</button></div>
    <div class="grid grid-cols-2 gap-4 mt-6 text-sm">
      <div class="card p-3"><div class="font-semibold">Contact</div><div class="mono text-xs mt-1">${esc(lead.decision_maker||lead.name||'—')} ${lead.decision_maker?'• '+esc(lead.decision_maker_source):''}</div><div class="mono text-xs">${esc(lead.email)} ${lead.email_verified?'✓ verified':''}</div><div class="text-xs opacity-60">${esc(lead.phone||'')}</div><div class="text-xs opacity-60">${esc(lead.address||'')}</div></div>
      <div class="card p-3"><div class="font-semibold">Business model</div><div class="text-xs mt-1">${esc(lead.business_model||lead.research_summary||lead.reason||'—')}</div></div>
    </div>
    <div class="card p-4 mt-4">
      <h3 class="font-semibold">Opportunity</h3>
      ${opportunity? `<div class="text-sm mt-2 space-y-1"><div><b>Problem:</b> ${esc(opportunity.business_problem)}</div><div><b>Behind:</b> ${esc(opportunity.problem_behind)}</div><div><b>Impact:</b> ${esc(opportunity.commercial_impact)}</div><div><b>Solution:</b> ${esc(opportunity.recommended_solution)}</div><div><b>Angle:</b> ${esc(opportunity.pitch_angle)}</div></div>`:'<div class="text-sm opacity-60">No opportunity yet — run Research.</div>'}
    </div>
    <div class="card p-4 mt-4">
      <h3 class="font-semibold">Evidence (${evidence.length})</h3>
      <div class="mt-2 space-y-2">${evidence.length? evidence.map(ev=>`<div class="border rounded-lg p-2"><div class="mono text-xs opacity-60">${esc(ev.source_type)} • ${esc(ev.confidence)} • ${esc(ev.discovered_date.slice(0,10))}</div><div class="text-sm font-medium">${esc(ev.claim||ev.summary.slice(0,120))}</div><a href="${esc(ev.source_url)}" target="_blank" class="text-xs text-[#0ea5e9] break-all">${esc(ev.source_url)}</a></div>`).join(''):'<div class="text-sm opacity-60">No evidence yet</div>'}</div>
    </div>
    <div class="card p-4 mt-4">
      <h3 class="font-semibold">Email</h3>
      ${lead.drafts?.length? lead.drafts.map(d=>`<div class="border rounded-lg p-3 mt-2"><div class="mono text-xs opacity-60">${esc(d.kind)} • ${esc(d.status)} • ${esc(d.subject)}</div><div class="whitespace-pre-wrap text-sm mt-2">${esc(d.body)}</div><div class="flex gap-2 mt-3"><button data-act="approve" data-id="${d.id}" class="bg-[#0f172a] text-white px-3 py-1.5 rounded text-xs">Approve</button><button data-act="send" data-id="${d.id}" class="bg-[#0ea5e9] text-white px-3 py-1.5 rounded text-xs">Send</button></div></div>`).join(''):'<div class="text-sm opacity-60">No draft — <button data-act="gen-email" data-id="${lead.id}" class="text-[#0ea5e9]">Generate email</button></div>'}
      <div class="mt-3 flex gap-2 flex-wrap">
        <button data-act="research-lead-drawer" data-id="${lead.id}" class="bg-[#0f172a] text-white px-3 py-1.5 rounded text-sm">Re-research & regenerate (streaming)</button>
        <button data-act="gen-email" data-id="${lead.id}" class="border px-3 py-1.5 rounded text-sm">Regenerate email only</button>
        <button data-act="block-lead" data-id="${lead.id}" class="border px-3 py-1.5 rounded text-sm">Block company</button>
      </div>
      <div id="research-trace-${lead.id}" class="hidden mt-3 bg-[#0f172a] text-[#e2e8f0] rounded-lg max-h-[260px] overflow-auto mono text-xs leading-5 p-2"></div>
      <div class="text-xs opacity-60 mt-2">Streaming reads the site (<b>8 pages</b>), history, services, socials + recent posts — everything you need to know (how long in business, what they do, recent activity). Like Folio, every claim is cited.</div>
    </div>
    <div class="card p-4 mt-4">
      <h3 class="font-semibold">Research source & draft</h3>
      <div class="text-xs mono opacity-60 mt-1">Recent: ${esc(lead.recent?.text||'—')} <a href="${esc(lead.recent?.url||'#')}" class="text-[#0ea5e9]">${esc(lead.recent?.domain||'')}</a></div>
      <div class="text-xs opacity-60">Fact: ${esc(lead.research?.fact||'—')}</div>
      ${lead.research?.business_age ? `<div class="text-xs mt-1"><b>History:</b> ${esc(lead.research.business_age)}</div>` : ''}
      ${lead.research?.services?.length ? `<div class="text-xs mt-1"><b>Services:</b> ${lead.research.services.slice(0,3).map(s=>esc(s.slice(0,120))).join(' • ')}</div>` : ''}
      ${lead.research?.facts?.length ? `<div class="text-xs mt-1"><b>Top claims (${lead.research.facts.length}):</b> ${lead.research.facts.slice(0,2).map(s=>esc(s.slice(0,120))).join(' • ')}</div>` : ''}
      <div class="text-xs opacity-60 mt-1">Sources (${(lead.research?.sources||[]).length}): ${(lead.research?.sources||[]).map(s=>`<a href="${esc(s.url)}" target="_blank" class="text-[#0ea5e9]">${esc(s.title||s.url)}</a>`).join(' • ')}</div>
      <div class="text-xs opacity-60">Socials: ${(lead.research?.socials||[]).map(s=>esc(s.network+':'+(s.handle||''))).join(' • ')||'—'}</div>
    </div>
    <div class="card p-4 mt-4">
      <h3 class="font-semibold">Replies</h3>
      <div class="text-sm mt-2 space-y-1">${replies.length? replies.map(r=>`<div class="border rounded p-2"><div class="mono text-xs">${esc(r.classification)} • ${esc(r.from_email)} • ${esc(r.received_at.slice(0,19))}</div><div class="text-xs">${esc(r.body.slice(0,200))}</div></div>`).join(''):'No replies'}</div>
      <form data-act="simulate-reply" data-id="${lead.id}" class="flex gap-2 mt-3"><input name="body" placeholder="Simulate reply: e.g. Interested, tell me more" class="border rounded px-3 py-2 flex-1 text-sm"><button class="bg-[#0f172a] text-white px-3 py-1.5 rounded text-sm">Simulate</button></form>
    </div>
  `;
  // bind inside drawer
  el.querySelectorAll('[data-act="approve"]').forEach(b=> b.addEventListener('click', async()=>{
    try{ await api(`/api/drafts/${b.dataset.id}/approve`,{method:'POST'}); toast('Approved'); openLead(id); loadLeads(); }catch(err){toast(err.message,true)}
  }));
  el.querySelectorAll('[data-act="send"]').forEach(b=> b.addEventListener('click', async()=>{
    if(!confirm('Send this letter from your mailbox?')) return;
    try{ await api(`/api/drafts/${b.dataset.id}/send`,{method:'POST'}); toast('Sent'); openLead(id); loadLeads(); loadDashboard(); }catch(err){toast(err.message,true)}
  }));
  el.querySelectorAll('[data-act="gen-email"]').forEach(b=> b.addEventListener('click', async()=>{
    try{ await api(`/api/leads/${b.dataset.id}/generate-email`,{method:'POST'}); toast('Email generated'); openLead(id); }catch(err){toast(err.message,true)}
  }));
  const reBtn = el.querySelector('[data-act="research-lead-drawer"]');
  const traceEl = el.querySelector(`#research-trace-${id}`);
  if(reBtn) reBtn.addEventListener('click', async()=>{
    reBtn.textContent='Streaming…'; reBtn.disabled=true;
    if(traceEl){ traceEl.classList.remove('hidden'); traceEl.innerHTML='<div class="opacity-60 p-2">Starting deep research…</div>'; }
    try{
      const lead = await streamResearch(id, traceEl);
      if(lead){
        toast('Streamed: ' + (lead.research?.business_age ? 'history found • ' : '') + (lead.research?.facts?.length||0) + ' claims • ' + (lead.socials?.length||lead.research?.socials?.length||0) + ' socials');
      } else {
        await api(`/api/leads/${id}/research`,{method:'POST'});
        toast('Re-researched & regenerated');
      }
      openLead(id); loadLeads();
    }catch(err){ toast(err.message,true) }
    reBtn.textContent='Re-research & regenerate (streaming)'; reBtn.disabled=false;
  });
  const blockBtn=el.querySelector('[data-act="block-lead"]');
  if(blockBtn) blockBtn.addEventListener('click', async()=>{
    try{ await api(`/api/leads/${id}/block`,{method:'POST'}); toast('Blocked'); drawer.close(); loadLeads(); }catch(err){toast(err.message,true)}
  });
  const simForm=el.querySelector('[data-act="simulate-reply"]');
  if(simForm) simForm.addEventListener('submit', async e=>{
    e.preventDefault();
    const body=new FormData(e.target).get('body');
    try{ await api(`/api/leads/${id}/reply`,{method:'POST', body: JSON.stringify({body})}); toast('Reply simulated & follow-ups cancelled if needed'); openLead(id); }catch(err){toast(err.message,true)}
  });
  drawer.showModal();
}

// Research queue
async function loadResearch(){
  const jobs=await api('/api/jobs');
  const runs=await api('/api/agent-runs');
  $('#jobs-list').innerHTML=(jobs.jobs||[]).slice(0,30).map(j=>`<div class="flex justify-between border-b py-1"><span>${esc(j.job_type)} #${j.id} → ${esc(j.status)} ${esc(j.error||'')}</span><span class="opacity-60">${esc(j.created_at.slice(11,19))}</span></div>`).join('')||'<span class="opacity-60">No jobs</span>';
  $('#agent-runs').innerHTML=(runs.runs||[]).slice(0,30).map(r=>`<div class="border-b py-2"><div><b>${esc(r.agent_name)}</b> <span class="badge ${r.status==='ok'?'bg-emerald-100 text-emerald-700':'bg-red-100 text-red-700'}">${esc(r.status)}</span> <span class="mono text-xs opacity-60">${r.duration_ms}ms</span></div><div class="mono text-xs opacity-60">in: ${esc(r.input_json.slice(0,120))}</div><div class="mono text-xs">out: ${esc(r.output_json.slice(0,120))}</div>${r.error?`<div class="text-red-600 text-xs">${esc(r.error)}</div>`:''}</div>`).join('')||'<span class="opacity-60">No runs</span>';
}

// Inbox
async function loadInbox(){
  const {replies}=await api('/api/replies');
  const pending=replies.filter(r=> !r.handled);
  $('#inbox-badge').textContent=pending.length? pending.length:''; $('#inbox-badge').classList.toggle('hidden', !pending.length);
  const list=$('#inbox-list');
  if(!replies.length){ list.innerHTML='<div class="text-sm text-slate-400">No replies yet. Simulate one from a lead detail drawer or wait for real Gmail.</div>'; return; }
  list.innerHTML=replies.map(r=>`<div class="card p-4">
    <div class="flex justify-between"><span class="badge ${r.classification==='positive'?'bg-emerald-100 text-emerald-700': r.classification==='unsubscribe'?'bg-red-100 text-red-700':'bg-slate-100'}">${esc(r.classification)}</span><span class="mono text-xs opacity-60">${esc(r.received_at.slice(0,19))}</span></div>
    <div class="font-medium mt-2">${esc(r.from_email)} • ${esc(r.subject)}</div>
    <div class="text-sm mt-1 whitespace-pre-wrap">${esc(r.body)}</div>
    <div class="flex gap-2 mt-3"><button data-act="reclassify" data-id="${r.id}" class="border px-3 py-1.5 rounded text-xs">Re-classify</button></div>
  </div>`).join('');
}
$('#btn-check-inbox').addEventListener('click', async()=>{
  const res=await api('/api/inbox/check',{method:'POST'});
  toast(`Inbox check: ${res.pending} pending`);
  loadInbox();
});
document.addEventListener('click', async e=>{
  const b=e.target.closest('[data-act="reclassify"]');
  if(b){ try{ await api(`/api/replies/${b.dataset.id}/classify`,{method:'POST'}); toast('Classified'); loadInbox(); }catch(err){toast(err.message,true)} }
});

// Followups
async function loadFollowups(){
  const jobs=await api('/api/jobs');
  const fus=(jobs.jobs||[]).filter(j=>j.job_type==='sendFollowup');
  $('#followups-list').innerHTML=fus.length? fus.map(j=>`<div class="flex justify-between border-b py-2 text-sm"><span>Lead #${j.contact_id} • ${esc(j.status)} • ${esc(j.payload_json.slice(0,80))}</span><span class="mono text-xs opacity-60">${esc(j.created_at.slice(0,19))}</span></div>`).join(''):'No follow-ups scheduled. They appear after you send the first email.';
}

// Suppression
async function loadSuppression(){
  const {suppression, blocked}=await api('/api/suppression');
  $('#suppression-list').innerHTML=suppression.length? suppression.map(s=>`<div class="flex justify-between border-b py-1"><span class="mono text-xs">${esc(s.email)}</span><span class="text-xs opacity-60">${esc(s.reason)}</span></div>`).join(''):'No suppressed emails';
  $('#blocked-list').innerHTML=blocked.length? blocked.map(b=>`<div class="border-b py-1"><div class="font-medium text-sm">${esc(b.company_name||b.domain)}</div><div class="mono text-xs opacity-60">${esc(b.domain)} ${esc(b.email)}</div></div>`).join(''):'No blocked companies';
}
$('#suppression-form').addEventListener('submit', async e=>{
  e.preventDefault(); const form=e.currentTarget; const email=new FormData(form).get('email');
  try{ await api('/api/suppression',{method:'POST', body: JSON.stringify({email})}); toast('Suppressed'); form.reset(); loadSuppression(); }catch(err){toast(err.message,true)}
});
$('#block-form').addEventListener('submit', async e=>{
  e.preventDefault(); const form=e.currentTarget; const fd=new FormData(form);
  try{ await api('/api/blocklist',{method:'POST', body: JSON.stringify({company_name:fd.get('company_name'), domain:fd.get('domain'), email:fd.get('email')})}); toast('Blocked'); form.reset(); loadSuppression(); }catch(err){toast(err.message,true)}
});

// Accounts
async function loadAccounts(){
  const {accounts}=await api('/api/email-accounts');
  $('#accounts-list').innerHTML=accounts.length? accounts.map(a=>`<div class="flex items-center justify-between border rounded-lg p-3"><div><div class="font-medium text-sm">${esc(a.email)} • ${esc(a.provider)}</div><div class="mono text-xs opacity-60">${esc(a.status)} • ${a.sent_today}/${a.daily_limit} today</div></div><button data-act="disconnect" data-id="${a.id}" class="border px-3 py-1.5 rounded text-xs">Disconnect</button></div>`).join(''):'<div class="text-sm text-slate-400">No email accounts. Connect Gmail below (mock works for demo).</div>';
}
$('#account-form').addEventListener('submit', async e=>{
  e.preventDefault(); const form=e.currentTarget; const fd=new FormData(form);
  try{ await api('/api/email-accounts/connect',{method:'POST', body: JSON.stringify({email: fd.get('email'), display_name: fd.get('display_name'), daily_limit: parseInt(fd.get('daily_limit')||50), provider:'gmail'})}); toast('Account connected (mock OAuth)'); form.reset(); loadAccounts(); }catch(err){toast(err.message,true)}
});
document.addEventListener('click', async e=>{
  const b=e.target.closest('[data-act="disconnect"]');
  if(b){ try{ await api(`/api/email-accounts/${b.dataset.id}/disconnect`,{method:'POST'}); toast('Disconnected'); loadAccounts(); }catch(err){toast(err.message,true)} }
});
$('#btn-oauth-url').addEventListener('click', async()=>{
  const {url}=await api('/api/email-accounts/auth-url?redirect_uri='+encodeURIComponent(location.origin+'/api/email-accounts/callback'));
  prompt('Open this URL to authorize Google (mock if no credentials):', url);
});
$('#btn-oauth-exchange').addEventListener('click', async()=>{
  const code=$('#oauth-code').value.trim();
  if(!code) return toast('Paste code',true);
  try{ const {account}=await api('/api/email-accounts/callback?code='+encodeURIComponent(code)); toast('Exchanged: '+account.email); loadAccounts(); }catch(err){toast(err.message,true)}
});

// Analytics
async function loadAnalytics(){
  const {total_leads, qualified, verified, sent, replies, positive, reply_rate, positive_rate, by_industry}=await api('/api/analytics');
  $('#analytics-cards').innerHTML=[
    ['Total leads', total_leads],
    ['Qualified', qualified],
    ['Verified emails', verified],
    ['Sent', sent],
    ['Replies', `${replies} (${reply_rate}%)`],
    ['Positive', `${positive} (${positive_rate}%)`],
  ].map(([k,v])=>`<div class="card p-4"><div class="text-xs uppercase opacity-60">${k}</div><div class="text-xl font-semibold mt-1">${v}</div></div>`).join('');
  const breakdown=$('#analytics-breakdown');
  breakdown.innerHTML=`<div><h4 class="font-medium">By industry</h4><div class="mt-2 space-y-1">${Object.keys(by_industry||{}).length? Object.entries(by_industry).map(([k,v])=>`<div class="flex justify-between border-b py-1"><span>${esc(k)}</span><span class="mono">${v}</span></div>`).join(''):'No data'}</div></div><div><h4 class="font-medium">Funnel</h4><div class="mt-2 space-y-1 text-sm"><div>Discovered → Qualified → Verified → Sent → Replied</div><div class="mono text-xs opacity-60">${total_leads} → ${qualified} → ${verified} → ${sent} → ${replies}</div></div></div>`;
}

// Settings
async function loadSettings(){
  const desk=await api('/api/desk');
  const s=desk.settings||{};
  const f=$('#settings-form');
  if(f) for(const [k,v] of Object.entries(s)){ if(f.elements[k]) f.elements[k].value=v??''; }
  const smtp=desk.smtp||{};
  const sf=$('#smtp-form');
  if(sf){ sf.elements.host.value=smtp.host||''; sf.elements.port.value=smtp.port||587; sf.elements.user.value=smtp.user||''; sf.elements.security.value=smtp.security||'starttls'; sf.elements.password.value=''; sf.elements.password.placeholder=smtp.configured?'Saved on this computer. Leave blank to keep it.':'Mailbox password'; }
  const prompts=await api('/api/prompts');
  $('#prompt-email').value=prompts.cold_email||'';
  $('#prompt-research').value=prompts.lead_research||'';
  const logs=await api('/api/audit');
  $('#audit-log').innerHTML=(logs.logs||[]).slice(0,30).map(l=>`<div class="border-b py-1 flex justify-between"><span>${esc(l.action)} ${esc(l.entity_type)} #${l.entity_id||''}</span><span class="opacity-60">${esc(l.created_at.slice(11,19))}</span></div>`).join('')||'No audit yet';
}
$('#settings-form').addEventListener('submit', async e=>{
  e.preventDefault(); const data=Object.fromEntries(new FormData(e.target).entries());
  try{ await api('/api/settings',{method:'PUT', body: JSON.stringify(data)}); toast('Settings saved'); }catch(err){toast(err.message,true)}
});
$('#smtp-form').addEventListener('submit', async e=>{
  e.preventDefault(); const data=Object.fromEntries(new FormData(e.target).entries());
  const smtp={host:data.host, port:Number(data.port||587), user:data.user, password:data.password, security:data.security};
  try{ await api('/api/smtp',{method:'PUT', body: JSON.stringify(smtp)}); toast('Mailbox saved'); loadSettings(); }catch(err){toast(err.message,true)}
});
$('#check-smtp').addEventListener('click', async()=>{
  try{ await api('/api/smtp/check',{method:'POST'}); toast('Mailbox login works'); }catch(err){toast(err.message,true)}
});
$('#btn-save-prompts').addEventListener('click', async()=>{
  try{
    await api('/api/prompts',{method:'POST', body: JSON.stringify({name:'cold_email', content: $('#prompt-email').value})});
    await api('/api/prompts',{method:'POST', body: JSON.stringify({name:'lead_research', content: $('#prompt-research').value})});
    toast('Prompts saved');
  }catch(err){toast(err.message,true)}
});

// Classic desk (reuse old logic partially)
let deskState=null, selected=null, viewKind='first';
async function loadDesk(){
  deskState=await api('/api/desk');
  if(!selected || !deskState.contacts.some(p=>p.id===selected)) selected=deskState.contacts[0]?.id||null;
  renderDesk();
}
function renderDesk(){
  if(!deskState) return;
  // counts
  const waiting=deskState.contacts.filter(i=>['drafted','researched','new'].includes(i.status)).length;
  const approved=deskState.contacts.filter(i=>i.status==='approved').length;
  // we show counts in desk header? reuse
  // list
  const q=$('#filter')?.value.trim().toLowerCase()||'';
  const people=deskState.contacts.filter(i=> `${i.name} ${i.email} ${i.company}`.toLowerCase().includes(q));
  const host=$('#list');
  if(host) host.innerHTML=people.length? people.map(i=>`<button class="w-full text-left p-2 rounded-lg border ${i.id===selected?'bg-[#0f172a] text-white':'hover:bg-slate-50'}"><div class="font-medium text-sm">${esc(i.company)}</div><div class="text-xs opacity-60">${esc(i.name)} • ${esc(i.email)}</div></button>`).join(''):'<div class="text-sm opacity-60 p-2">No one on the desk</div>';
  // letter/side simplified
  const contact=deskState.contacts.find(x=>x.id===selected);
  const letterHost=$('#letter'), sideHost=$('#side');
  if(!contact){ if(letterHost) letterHost.innerHTML='<div class="text-sm opacity-60">Select a contact</div>'; return; }
  const draft=contact.drafts.find(d=>d.kind===viewKind)||contact.drafts[0];
  if(letterHost) letterHost.innerHTML=draft? `<div class="space-y-3"><div class="font-semibold">${esc(draft.subject)}</div><pre class="whitespace-pre-wrap text-sm leading-6">${esc(draft.body)}</pre><div class="flex gap-2"><button data-desk="approve" data-id="${draft.id}" class="bg-[#0f172a] text-white px-3 py-1.5 rounded text-sm">Approve</button><button data-desk="send" data-id="${draft.id}" class="bg-[#0ea5e9] text-white px-3 py-1.5 rounded text-sm">Send</button></div></div>`:'<div class="text-sm opacity-60">No draft</div>';
  if(sideHost) sideHost.innerHTML=`<div class="space-y-3 text-sm"><div><b>Reason</b><div class="mono text-xs opacity-60">${esc(contact.reason)}</div></div><div><b>Fact</b><div class="mono text-xs">${esc(contact.research.fact)}</div></div><div><b>Website</b><div class="mono text-xs">${esc(contact.website)}</div></div><div><b>Emails</b><div class="mono text-xs">${(contact.research.emails||[]).map(e=>esc(e.email)).join('<br>')||'—'}</div></div></div>`;
}
document.addEventListener('click', e=>{
  const b=e.target.closest('#list button');
  if(b){ const id=parseInt(b.textContent.match(/#?(\d+)/)?.[1]||''); // fallback
  }
});
$('#filter')?.addEventListener('input', renderDesk);
document.addEventListener('click', async e=>{
  const b=e.target.closest('[data-desk="approve"]');
  if(b){ try{ await api(`/api/drafts/${b.dataset.id}/approve`,{method:'POST'}); toast('Approved'); loadDesk(); }catch(err){toast(err.message,true)} }
  const s=e.target.closest('[data-desk="send"]');
  if(s){ try{ await api(`/api/drafts/${s.dataset.id}/send`,{method:'POST'}); toast('Sent'); loadDesk(); }catch(err){toast(err.message,true)} }
});
document.addEventListener('click', e=>{
  const btn=e.target.closest('#list button');
  if(btn){
    // find contact by company text
    const txt=btn.querySelector('div').textContent.trim();
    const c=deskState.contacts.find(x=>x.company===txt);
    if(c){ selected=c.id; renderDesk(); }
  }
});

// Find 5 more (uses active campaign or creates one)
$('#btn-find5').addEventListener('click', async()=>{
  const camps=await api('/api/campaigns').then(r=>r.campaigns).catch(()=>[]);
  let cid=camps[0]?.id;
  if(!cid){
    // create default campaign
    const {campaign}=await api('/api/campaigns',{method:'POST', body: JSON.stringify({name:'Texas Property Management — Auto', country:'United States', state:'Texas', city:'Houston', industries:['Property Management'], lead_type:'both', rating_min:2.5, rating_max:3.9, review_min:50})});
    cid=campaign.id;
  }
  try{
    toast('Finding 5 more — discovery + research, ~30s');
    await api(`/api/campaigns/${cid}/start`,{method:'POST', body: JSON.stringify({mode:'research_draft', limit:5})});
    toast('5 leads processed — see Leads');
    loadLeads(); loadDashboard();
    document.querySelector('[data-view="leads"]').click();
  }catch(err){ toast(err.message,true); }
});

// Initial load
loadDashboard();
loadCampaigns();
loadDesk();
setInterval(()=>{ if(state.view==='dashboard') loadDashboard(); }, 15000);
