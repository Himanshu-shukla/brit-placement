(() => {
  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const key = () => sessionStorage.getItem('placement_key') || '';
  const state = { tab: 'overview', dashboard:{}, learners:[],companies:[],jobs:[],matches:[],applications:[],contacts:[],outreach:[],
    loops:[],loopJobs:[],selectedLoopId:0,selectedJobId:0,searchFilters:{},matchFilter:{},questions:[],cvs:[],attempts:[],analytics:null,cvResult:null };
  $('#today').textContent = new Date().toLocaleDateString('en-GB',{month:'short',day:'numeric',year:'numeric'});

  async function api(path, method = 'GET', body = null, retry = true) {
    if (!key()) {
      const input = prompt('Enter the admin key printed in the Python server terminal:');
      if (!input) throw new Error('Admin key needed. Restart/inspect the Python terminal to retrieve it.');
      sessionStorage.setItem('placement_key', input.trim());
    }
    const res = await fetch('/api/' + path, {
      method, headers: {'X-Placement-Key':key(),...(body !== null ? {'Content-Type':'application/json'} : {})},
      ...(body !== null ? {body:JSON.stringify(body)} : {})
    });
    if (res.status === 401 && retry) {
      sessionStorage.removeItem('placement_key');
      return api(path,method,body,false);
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'API error');
    return data;
  }

  function notice(message, error=false) {
    const box=$('#notice');box.hidden=false;box.className='notice'+(error?' error':'');box.textContent=message;
    window.setTimeout(()=>{box.hidden=true;},7000);
  }
  const action = (label,task,extra='') => `<button class="btn ${extra}" data-action="${task}">${esc(label)}</button>`;
  const link = (url,text) => `<a href="${esc(/^https?:\/\//i.test(url||'')?url:'#')}" target="_blank" rel="noreferrer noopener">${esc(text)}</a>`;
  function heading(title,desc,buttons='') { return `<div class="hero"><div><h1>${title}</h1><p class="muted">${desc}</p></div><div class="hero-actions">${buttons}</div></div>`; }
  const badge = (status) => `<span class="pill ${['excluded','rejected','withdrawn'].includes(status)?'bad':['review','queued','prepared','draft'].includes(status)?'warn':''}">${esc(status)}</span>`;
  const table = (cols,items,render) => items.length ? `<div class="table-wrap"><table class="table"><thead><tr>${cols.map(c=>`<th>${c}</th>`).join('')}</tr></thead><tbody>${items.map(render).join('')}</tbody></table></div>` : '<div class="card empty">No records yet. Add an item to get started.</div>';

  function render() {
    document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.tab===state.tab));
    let html='';
    const s=state;
    if(s.tab==='overview'){
      const stat=(label,value,help)=>`<div class="stat"><label>${label}</label><strong>${value||0}</strong><small>${help}</small></div>`;
      html = heading('Placement overview','Automate discovery. Preserve candidate consent. Track real outcomes.', action('Create a loop','adv-new-loop','teal')+action('Run matching','match'))+
        `<div class="stats">${stat('Active loops',s.dashboard.active_loops,'Every 2 hours by default')}${stat('Discovered jobs',s.dashboard.jobs,'Across configured feeds')}${stat('Verified submissions',s.dashboard.confirmed_applications,'Confirmed by employer')}${stat('Pending applications',s.dashboard.pending_applications,'Queued or prepared')}</div>`+
        `<div class="cards"><div class="card"><h2>Daily operating flow</h2>`+[
          ['01','Discover','Connect company ATS feeds; optional Reed and Adzuna keys.'],
          ['02','Qualify','Match skills, seniority and right-to-work/sponsorship.'],
          ['03','Prepare','Review CVs and use assisted applications in the browser.'],
          ['04','Verify','Log submission reference, interviews and actual offers.']
        ].map(x=>`<div class="flowstep"><div class="stepnum">${x[0]}</div><div><strong>${x[1]}</strong><small>${x[2]}</small></div></div>`).join('')+
        `</div><div class="card"><h2>Quick setup</h2><p class="muted">Start by adding your 10 learners and company career feeds. All outreach requires individual review.</p><div style="height:18px"></div>`+
        action('Add learner','add-learner','teal')+' '+action('Add company','add-company')+' '+action('Import sponsor list','import-sponsors','alt')+
        `<div class="callout">LinkedIn and Indeed: the Chrome extension assists with form filling while the candidate reviews and submits. It does not bypass challenges or assert successful submission.</div></div></div>`;
    }
    if(s.tab==='learners') html=heading('Learners','Candidate-owned profiles with explicit placement consent.',action('+ Add learner','add-learner','teal'))+
      table(['Learner','Skills','UK eligibility','Consent','CV'],s.learners,x=>`<tr><td class="primary">${esc(x.name)}<div class="sub">${esc(x.email)}</div></td><td>${esc(x.skills)}</td><td>${x.sponsorship?'Needs sponsorship':esc(x.right_to_work)}</td><td>${x.permission_to_apply?badge('granted'):badge('missing')}</td><td><button class="linkbtn" data-action="pdf" data-id="${x.id}">Download PDF</button> <button class="linkbtn" data-action="cv" data-id="${x.id}">Print view</button></td></tr>`);
    if(s.tab==='companies') html=heading('Target companies','Connect ATS boards with their public board slugs.',action('+ Add company','add-company','teal')+action('Sync feeds','sync'))+
      table(['Employer','Source','Board identifier','Contact discovery'],s.companies,x=>`<tr><td class="primary">${esc(x.name)}<div class="sub">${esc(x.domain)}</div></td><td>${badge(x.ats)}</td><td class="code">${esc(x.ats_slug)}</td><td><button class="linkbtn" data-action="scan" data-id="${x.id}">Scan public contact page</button></td></tr>`);
    if(s.tab==='jobs') html=heading('Recent vacancies','Fresh job posts from feeds and manually captured listings.',action('Sync now','sync','teal')+action('Import jobs CSV','import-jobs')+action('+ Add job','add-job'))+
      table(['Vacancy','Company','Location','Source','Action'],s.jobs,x=>`<tr><td class="primary">${link(x.url,x.title)}</td><td>${esc(x.company_name)}</td><td>${esc(x.location)}</td><td>${badge(x.source)}</td><td><button class="linkbtn" data-action="queue-pick" data-id="${x.id}">Queue learner</button></td></tr>`);
    if(s.tab==='matches') html=heading('Role matching','Explainable scores; sponsorship checks may require human review.',action('Recalculate matches','match','teal')+action('Local AI refine top 10','ai-match','alt'))+
      table(['Candidate','Vacancy','Score','Decision','Explanation','Action'],s.matches.filter(x=>x.decision!=='excluded').slice(0,200),x=>`<tr><td class="primary">${esc(x.learner_name)}</td><td>${link(x.url,x.job_title)}<div class="sub">${esc(x.company_name)}</div></td><td class="primary">${x.score}/100</td><td>${badge(x.decision)}</td><td>${esc(x.reasons)}</td><td><button class="linkbtn" data-action="queue" data-learner="${x.learner_id}" data-job="${x.job_id}">Queue</button></td></tr>`);
    if(s.tab==='applications') html=heading('Application pipeline','An application only counts as submitted when evidence is recorded.',action('Open extension setup','extension'))+
      table(['Learner','Vacancy','Status','Method','Verification','Update'],s.applications,x=>`<tr><td class="primary">${esc(x.learner_name)}</td><td>${link(x.url,x.job_title)}</td><td>${badge(x.status)}</td><td>${esc(x.method)}</td><td>${esc(x.confirmation||'Not verified')}</td><td><button class="linkbtn" data-action="status" data-id="${x.id}">Change status</button></td></tr>`);
    if(s.tab==='contacts') html=heading('Contact & outreach','Only explicitly published recruitment addresses; reviewed emails only.',action('New email draft','draft','teal'))+
      `<div class="cards"><div class="card"><h2>Published company contacts</h2>${s.contacts.length ? s.contacts.map(c=>`<div class="flowstep"><div class="stepnum">@</div><div><strong>${esc(c.email)}</strong><small>${esc(c.company_name)} · ${link(c.source_url,'source page')}</small></div></div>`).join(''):'<p class="muted">Use Companies → Scan public contact page.</p>'}</div><div class="card"><h2>Outreach queue</h2>`+
      (s.outreach.length?s.outreach.map(x=>`<div class="flowstep"><div class="stepnum">✉</div><div><strong>${esc(x.learner_name)} → ${esc(x.contact_email)}</strong><small>${esc(x.job_title)} · ${x.status}</small><button class="linkbtn" data-action="view-email" data-id="${x.id}">Review</button> ${x.status==='draft'?`<button class="linkbtn" data-action="edit-email" data-id="${x.id}">Edit</button> <button class="linkbtn" data-action="tailor-email" data-id="${x.id}">AI tailor</button> <button class="linkbtn" data-action="send" data-id="${x.id}">Approve & send</button>`:''}</div></div>`).join(''):'<p class="muted">No messages drafted.</p>')+
      `<div class="callout">Use verified business role inboxes. Honour opt-outs and relevant UK data-protection rules. No bulk unsolicited campaigns.</div>${action('Suppress address','suppress','alt')}</div></div>`;
    const enhanced=window.BritAdvanced?.render(s.tab,s);
    if(enhanced!==null&&enhanced!==undefined)html=enhanced;
    $('#content').innerHTML=html;
    const currentFilters=s.tab==='jobs'?s.searchFilters:s.tab==='matches'?s.matchFilter:null;
    if(currentFilters){
      for(const [name,value] of Object.entries(currentFilters)){
        const element=$('#content').querySelector(`[name="${name}"]`);
        if(!element)continue;
        if(element.type==='checkbox')element.checked=!!value;
        else element.value=value;
      }
    }
    if(s.tab==='questions'){
      const selector=$('#content').querySelector('[name="questionLearner"]');
      if(selector)selector.value=s.questionLearnerFilter||'';
    }
  }

  function modal(title,form) { $('#modal-title').textContent=title;$('#modal-body').innerHTML=form;$('#modal').showModal(); }
  const input=(name,label,type='text',required=false,placeholder='')=>`<label>${esc(label)}<input name="${name}" type="${type}" ${required?'required':''} placeholder="${esc(placeholder)}"></label>`;
  const select=(name,label,values)=>`<label>${esc(label)}<select name="${name}">${values.map(([id,t])=>`<option value="${esc(id)}">${esc(t)}</option>`).join('')}</select></label>`;
  const textarea=(name,label)=>`<label>${esc(label)}<textarea name="${name}"></textarea></label>`;
  const options=(items,labelFn)=>items.map(x=>[String(x.id),labelFn(x)]);
  function submitForm(kind,fields,button='Save'){modal(kind.replace(/-/g,' '),`<form id="entry-form" data-kind="${kind}">${fields}<button class="btn teal" type="submit">${button}</button></form>`);}
  function showForm(type,payload={}) {
    const learners=options(state.learners,x=>x.name), jobs=options(state.jobs,x=>`${x.title} — ${x.company_name}`), contacts=options(state.contacts,x=>`${x.email} — ${x.company_name}`);
    switch(type){
      case 'add-learner': return submitForm(type,input('name','Full name','text',true)+input('email','Email','email',true)+input('phone','Phone')+input('location','UK location')+input('skills','Verified skills (comma separated)','text',true,'SQL, Excel, Power BI')+textarea('summary','Professional summary')+textarea('projects','Verified portfolio work')+input('experience_years','Relevant experience (years)','number')+select('right_to_work','UK right-to-work',[['unknown','Unknown'],['yes','Confirmed'],['no','Not currently eligible']])+'<label class="checkbox"><input type="checkbox" name="sponsorship"> Candidate needs sponsorship</label><label class="checkbox"><input type="checkbox" name="permission_to_apply"> Candidate has explicitly agreed to placement applications</label>');
      case 'add-company': return submitForm(type,input('name','Employer','text',true)+input('domain','Company domain','text',false,'example.com')+input('careers_url','Company careers/contact page URL','url',false,'https://example.com/careers')+select('ats','ATS / source',[['greenhouse','Greenhouse'],['lever','Lever'],['ashby','Ashby'],['workable','Workable (availability varies)'],['manual','Manual']])+input('ats_slug','Employer board slug (ATS boards only)'));
      case 'add-job': return submitForm(type,select('company_id','Company',options(state.companies,x=>x.name))+input('title','Position title','text',true)+input('url','Advert URL','url',true)+input('location','UK location')+textarea('description','Job description'));
      case 'queue-pick': return submitForm(type,select('learner_id','Candidate',learners)+`<input type="hidden" name="job_id" value="${Number(payload.id)}">`,'Queue application');
      case 'status': return submitForm(type,select('status','New stage',[['queued','Queued'],['prepared','Prepared'],['confirmed','Submitted — verified'],['interview','Interview'],['offer','Offer'],['hired','Hired'],['rejected','Rejected'],['withdrawn','Withdrawn']])+input('confirmation','Evidence or reference number')+textarea('notes','Notes')+`<input type="hidden" name="id" value="${Number(payload.id)}">`,'Update status');
      case 'draft': return submitForm(type,select('learner_id','Candidate',learners)+select('job_id','Vacancy',jobs)+select('contact_id','Published recruitment contact',contacts),'Create draft');
      case 'suppress': return submitForm(type,input('email','Email to suppress','email',true)+input('reason','Reason','text',false,'Opt out'));
      case 'import-sponsors': return submitForm(type,'<label>Select the official Home Office sponsor register CSV<input required type="file" name="csvfile" accept=".csv,text/csv"></label>','Import official CSV');
      case 'import-jobs': return submitForm(type,'<label>Select a CSV job export (JobSpy column format accepted)<input required type="file" name="csvfile" accept=".csv,text/csv"></label>','Import jobs');
      case 'edit-email': {const message=state.outreach.find(x=>x.id===Number(payload.id));if(!message)return;submitForm(type,input('subject','Subject','text',true)+textarea('body','Email body')+`<input type="hidden" name="id" value="${Number(payload.id)}">`,'Save reviewed draft');const form=$('#entry-form');form.elements.subject.value=message.subject;form.elements.body.value=message.body;form.elements.body.style.height='240px';return;}
      default: return;
    }
  }

  async function refresh(){
    const paths=['dashboard','learners','companies','jobs','matches','applications','contacts','outreach','loops','questions','cvs','attempts','analytics'];
    const values=await Promise.all(paths.map(p=>api(p)));
    paths.forEach((p,i)=>state[p]=values[i]);render();
  }
  async function perform(el){
    const task=el.dataset.action;
    try{
      const ctx={state,api,refresh,notice,modal,render,key};
      if(await window.BritAdvanced?.action(task,el,ctx))return;
      if(['add-learner','add-company','add-job','queue-pick','status','draft','suppress','import-sponsors','import-jobs','edit-email'].includes(task))return showForm(task,el.dataset);
      if(task==='extension')return modal('Chrome extension','<p>Open <code>chrome://extensions</code>, enable Developer mode, click <b>Load unpacked</b> and select the project <b>extension</b> folder. Enter the local API key in its popup. Never share the key.</p>');
      if(task==='sync') {const data=await api('sync','POST',{mode:'all'});notice(`Discovery complete: ${JSON.stringify(data).slice(0,260)}`);return refresh();}
      if(task==='match'){const data=await api('matches/run','POST',{});notice(`${data.matches_scored} matches scored`);return refresh();}
      if(task==='ai-match'){notice('Asking local Ollama for rankings; this may take a few minutes...');const data=await api('matches/ai-refine','POST',{limit:10});notice(`${data.refined} candidates reranked (review AI suggestions)`);return refresh();}
      if(task==='scan'){const data=await api('contacts/scan','POST',{company_id:Number(el.dataset.id)});notice(`Found ${data.emails.length} published recruitment addresses and ${data.contact_forms.length} contact links`);return refresh();}
      if(task==='queue'){await api('applications','POST',{learner_id:Number(el.dataset.learner),job_id:Number(el.dataset.job)});notice('Application queued; not yet submitted.');return refresh();}
      if(task==='cv') {const w=window.open('about:blank','_blank');if(!w)throw new Error('Allow popups for this local page');const response=await fetch(`/api/learners/${Number(el.dataset.id)}/resume`,{headers:{'X-Placement-Key':key()}});if(!response.ok)throw new Error('CV could not be opened');const body=await response.text();w.document.open();w.document.write(body);w.document.close();return;}
      if(task==='pdf') {const response=await fetch(`/api/learners/${Number(el.dataset.id)}/resume.pdf`,{headers:{'X-Placement-Key':key()}});if(!response.ok){const failure=await response.json();throw new Error(failure.error||'PDF generation failed')}const objectUrl=URL.createObjectURL(await response.blob());const anchor=document.createElement('a');anchor.href=objectUrl;anchor.download=`candidate-${Number(el.dataset.id)}.pdf`;document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(objectUrl),5000);return;}
      if(task==='view-email'){const rec=state.outreach.find(x=>x.id===Number(el.dataset.id));return modal('Review email',`<p><b>To:</b> ${esc(rec.contact_email)}</p><p><b>Subject:</b> ${esc(rec.subject)}</p><pre>${esc(rec.body)}</pre>`);}
      if(task==='tailor-email'){const data=await api(`outreach/${Number(el.dataset.id)}/tailor`,'POST',{});notice('Local AI produced an unverified draft. Review carefully.');await refresh();return modal('Review AI tailored draft',`<pre>${esc(data.body)}</pre>`);}
      if(task==='send'){
        if(!confirm('Send this reviewed draft to the published company inbox? Check that it is relevant, lawful, and the candidate has agreed.'))return;
        await api(`outreach/${Number(el.dataset.id)}/send`,'POST',{approved:true});notice('Email submitted to configured SMTP provider');return refresh();
      }
    }catch(err){notice(err.message,true)}
  }
  $('#navigation').addEventListener('click',(e)=>{const node=e.target.closest('[data-tab]');if(node){state.tab=node.dataset.tab;render()}});
  $('#top-new-loop').addEventListener('click',(e)=>perform(e.currentTarget));
  $('#content').addEventListener('click',(e)=>{const node=e.target.closest('[data-action]');if(node)perform(node)});
  $('#content').addEventListener('change',(e)=>{
    if(e.target.name==='questionLearner'){state.questionLearnerFilter=e.target.value;render();}
  });
  $('#content').addEventListener('dragstart',(e)=>{
    const card=e.target.closest('[data-drag-app]');if(card&&e.dataTransfer)e.dataTransfer.setData('text/plain',card.dataset.dragApp);
  });
  $('#content').addEventListener('dragover',(e)=>{if(e.target.closest('[data-drop-status]'))e.preventDefault()});
  $('#content').addEventListener('drop',async(e)=>{
    const lane=e.target.closest('[data-drop-status]');if(!lane)return;e.preventDefault();
    const id=Number(e.dataTransfer?.getData('text/plain'));if(!id)return;
    const statuses={'Saved':'queued','Applied':'confirmed','Screened':'interview','Offer received':'offer','Placed':'hired'};
    const status=statuses[lane.dataset.dropStatus];if(!status)return;
    let confirmation='';if(!['queued','prepared'].includes(status)){
      confirmation=prompt('Enter real submission or hiring evidence/reference before moving this card:')||'';
      if(!confirmation.trim()){notice('Evidence required to advance the application',true);return;}
    }
    try{await api(`applications/${id}/status`,'POST',{status,confirmation});await refresh();notice('Application stage updated');}
    catch(err){notice(err.message,true)}
  });
  $('#modal-body').addEventListener('click',(e)=>{const node=e.target.closest('[data-action]');if(node)perform(node)});
  $('#close-modal').addEventListener('click',()=>{$('#modal').close();$('#modal').classList.remove('wide-dialog')});
  $('#refresh').addEventListener('click',()=>refresh().catch(err=>notice(err.message,true)));
  $('#modal-body').addEventListener('submit',async(e)=>{
    if(e.target.id!=='entry-form')return;e.preventDefault();
    const kind=e.target.dataset.kind, values=Object.fromEntries(new FormData(e.target));
    if(kind.startsWith('adv-')){
      e.preventDefault();
      try{await window.BritAdvanced.handleForm(kind,e.target,{state,api,refresh,notice,modal,render,key})}
      catch(err){notice(err.message,true)}
      return;
    }
    values.permission_to_apply=!!e.target.elements.permission_to_apply?.checked;
    values.sponsorship=!!e.target.elements.sponsorship?.checked;
    const endpoints={'add-learner':'learners','add-company':'companies','add-job':'jobs','queue-pick':'applications','draft':'outreach/draft','suppress':'suppressions','import-sponsors':'sponsors/import','import-jobs':'jobs/import-csv'};
    try{
      if(kind==='import-sponsors'||kind==='import-jobs'){values.csv=await e.target.elements.csvfile.files[0].text();delete values.csvfile;}
      const endpoint=kind==='status'?`applications/${Number(values.id)}/status`:kind==='edit-email'?`outreach/${Number(values.id)}/edit`:endpoints[kind];
      await api(endpoint,'POST',values);$('#modal').close();notice('Saved successfully');await refresh();
    }catch(err){notice(err.message,true)}
  });
  refresh().catch(err=>notice(err.message,true));
})();
