/* Brit Institute Placement OS — loop-builder, pipeline and intelligence screens.
   Uses existing authenticated local API and dashboard shell. No external JS deps. */
(() => {
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const task=(label,name,attrs='',cls='')=>`<button type="button" class="btn ${cls}" data-action="${name}" ${attrs}>${esc(label)}</button>`;
  const item=(name,label,type='text',placeholder='')=>`<label>${esc(label)}<input name="${name}" type="${type}" placeholder="${esc(placeholder)}"></label>`;
  const choices=(name,label,arr)=>`<label>${esc(label)}<select name="${name}">${arr.map(([v,t])=>`<option value="${esc(v)}">${esc(t)}</option>`).join('')}</select></label>`;
  const ta=(name,label)=>`<label>${esc(label)}<textarea name="${name}"></textarea></label>`;
  const label=(s)=>`<span class="pill">${esc(s)}</span>`;
  const head=(title,desc,buttons='')=>`<div class="hero"><div><h1>${title}</h1><p class="muted">${esc(desc)}</p></div><div class="hero-actions">${buttons}</div></div>`;
  const links=(url,title)=>`<a href="${esc(/^https:\/\//.test(url||'')?url:'#')}" rel="noreferrer noopener" target="_blank">${esc(title)}</a>`;
  const statusPill=(value)=>`<span class="pill ${['dismissed','rejected','blocked','error'].includes(value)?'bad':['review','queued','prepared','needs_review'].includes(value)?'warn':''}">${esc(value)}</span>`;
  let wizard={step:0,data:{}};

  function render(tab,state){
    if(tab==='loops')return renderLoops(state);
    if(tab==='board')return renderBoard(state);
    if(tab==='questions')return renderQuestions(state);
    if(tab==='cvcheck')return renderCVCheck(state);
    if(tab==='analytics')return renderAnalytics(state);
    if(tab==='jobs')return renderSearch(state);
    if(tab==='matches')return renderMatches(state);
    if(tab==='settings')return renderSettings(state);
    return null;
  }

  const SETTINGS=[
    ['AI (Groq)','Screening answers, CV tailoring and recruiter emails.',[
      ['GROQ_API_KEY','Groq API key','password','gsk_…'],['GROQ_MODEL','Groq model','text','llama-3.3-70b-versatile']]],
    ['Automatic applications','Daily cap per learner and random pause between applications keep accounts safe.',[
      ['AUTO_SUBMIT_ENABLED','Submit applications automatically','bool'],['TAILOR_CV','Tailor the CV for every job','bool'],
      ['AI_ANSWERS_AUTO_APPROVE','Reuse AI answers automatically','bool'],['DAILY_APPLY_LIMIT','Applications per learner per day','number','40'],
      ['APPLY_DELAY_MIN_SECONDS','Min pause between applications (s)','number','45'],['APPLY_DELAY_MAX_SECONDS','Max pause (s)','number','120'],
      ['AUTO_APPLY_ALLOWED_HOSTS','Company sites the server may apply on (* = all)','text','*'],['SERVER_HEADLESS','Server browser hidden','bool']]],
    ['Recruiter emails (SMTP)','Gmail: smtp.gmail.com, port 465, and a Google app password.',[
      ['AUTO_EMAIL_ENABLED','Email recruiters automatically','bool'],['EMAIL_GUESSING','Find emails from name patterns / MX','bool'],
      ['DAILY_EMAIL_LIMIT','Emails per learner per day','number','25'],['SMTP_HOST','SMTP host','text','smtp.gmail.com'],
      ['SMTP_PORT','SMTP port (465 SSL / 587 STARTTLS)','number','465'],['SMTP_USERNAME','SMTP username','text'],
      ['SMTP_PASSWORD','SMTP password / app password','password'],['SMTP_FROM','From address','email'],['SMTP_FROM_NAME','Sender name for manual emails','text'],
      ['IMAP_HOST','IMAP host (reply tracking)','text','imap.gmail.com'],['IMAP_USERNAME','IMAP username','text'],['IMAP_PASSWORD','IMAP password','password']]],
    ['Job boards','LinkedIn, Indeed, Glassdoor, NHS Jobs and remote boards need no key.',[
      ['REED_API_KEY','Reed API key','password'],['ADZUNA_APP_ID','Adzuna app ID','text'],['ADZUNA_APP_KEY','Adzuna app key','password'],
      ['JOOBLE_API_KEY','Jooble API key','password'],['JOBSPY_RESULTS','Results per board per search','number','30'],['DISCOVERY_HOURS_OLD','Only jobs posted within (hours)','number','72']]]
  ];

  function renderSettings(s){
    const values=s.settings&&!Array.isArray(s.settings)?s.settings:{};
    const field=([name,labelText,type,placeholder])=>type==='bool'
      ?`<label class="checkbox"><input type="checkbox" name="${name}" ${['1','true','on','yes'].includes(String(values[name]||'').toLowerCase())?'checked':''}> ${esc(labelText)}</label>`
      :`<label>${esc(labelText)}<input name="${name}" type="${type==='password'?'password':type}" value="${esc(values[name]||'')}" placeholder="${esc(placeholder||'')}" autocomplete="off"></label>`;
    return head('Settings','Keys and limits for search, auto-apply, AI and recruiter emails. Stored privately in data/settings.json.',
        task('Save settings','adv-settings-save','','teal')+task('Test Groq','adv-test-ai')+task('Send SMTP test','adv-test-smtp'))+
      `<form id="settings-form">`+SETTINGS.map(([title,help,fields])=>`<div class="card settings-card"><h2>${esc(title)}</h2><p class="muted">${esc(help)}</p><div class="field-grid">${fields.map(field).join('')}</div></div>`).join('')+
      `</form><div class="callout">Install the Chrome extension in each learner's Chrome profile, sign in to LinkedIn and Indeed there, choose the learner in the popup and switch on “Auto-apply to queued jobs”.</div>`;
  }

  function renderLoops(s){
    const top=head('My search loops','Configure recurring searches for each learner. Runs every 2 hours by default.',task('+ Create new loop','adv-new-loop','','teal')+task('Run due loops','adv-tick'));
    if(!s.loops.length)return top+`<div class="card empty"><h2>No loops configured</h2><p>Create a loop to discover fresh UK analytics roles and match them to a learner.</p><div style="height:16px"></div>${task('Create your first loop','adv-new-loop','','teal')}</div>`;
    const cards=s.loops.map(x=>{
      const names=JSON.parse(x.titles||'[]');const boards=JSON.parse(x.boards||'[]');
      return `<article class="loop-card"><div class="rowlike"><div><h2>${esc(x.name)}</h2><p class="meta">${esc(x.learner_name)} · ${esc(x.location)}</p></div>${statusPill(x.active?'active':'paused')}</div>
        <div class="tag-row">${names.map(n=>`<span class="tag">${esc(n)}</span>`).join('')}</div>
        <p class="muted-line">${esc(x.mode==='auto'?'Auto-apply'+(x.tailor_cv?' · tailored CV':'')+(x.auto_email?' · recruiter emails':''):'Review matches manually')} · Every ${x.frequency_minutes} minutes</p>
        <p class="muted-line">Sources: ${esc(boards.length?boards.join(', '):'All configured sources')}</p>
        <div style="margin:12px 0"><b>${x.job_count||0}</b> matches · <b>${x.run_count}</b> cycles completed</div>
        ${x.last_error?`<div class="inline-alert">${esc(x.last_error)}</div>`:''}
        <div class="loop-card-actions">${task('Run now','adv-run-loop',`data-id="${x.id}"`,'teal')}${task('View matches','adv-loop-view',`data-id="${x.id}"`)}${task(x.active?'Pause':'Resume','adv-loop-toggle',`data-id="${x.id}" data-active="${x.active?'0':'1'}"`,'alt')}</div>
        <p class="muted-line">Next: ${esc(x.next_run_at?new Date(x.next_run_at).toLocaleString():'Not yet scheduled')}</p></article>`;
    }).join('');
    let matches='';
    if(s.selectedLoopId){
      const selected=s.loops.find(l=>l.id===s.selectedLoopId);
      matches=`<h2 class="subheading">${esc(selected?.name||'Loop')} — ${s.loopJobs.length} saved matches</h2>`+
        `<div class="table-wrap"><table class="table"><thead><tr><th>Role</th><th>Company</th><th>Score</th><th>State</th><th>Actions</th></tr></thead><tbody>`+
        s.loopJobs.map(m=>`<tr><td class="primary">${links(m.url,m.title)}</td><td>${esc(m.company_name)}</td><td>${m.score}%</td><td>${statusPill(m.status)}</td><td>`+
          task('Favorite','adv-loop-status',`data-loop="${m.loop_id}" data-job="${m.job_id}" data-status="favorite"`,'alt')+' '+
          task('Dismiss','adv-loop-status',`data-loop="${m.loop_id}" data-job="${m.job_id}" data-status="dismissed"`)+`</td></tr>`).join('')+
        `</tbody></table></div>`;
    }
    return top+`<div class="loop-grid">${cards}</div>`+matches;
  }

  function renderBoard(s){
    const lanes=[['Saved',a=>['queued','prepared','applying','needs_review'].includes(a.status)],['Applied',a=>a.status==='confirmed'],
      ['Screened',a=>a.status==='interview'],['Offer received',a=>a.status==='offer'],['Placed',a=>a.status==='hired']];
    const board=lanes.map(([name,selector])=>{
      const list=s.applications.filter(selector);
      return `<section class="kanban-lane" data-lane="${esc(name)}"><div class="kanban-title">${name}<span>${list.length}</span></div><div class="kanban-cards" data-drop-status="${name}">`+
        (list.map(a=>`<div class="kanban-card" draggable="true" data-drag-app="${a.id}"><strong>${esc(a.job_title)}</strong>
          <small>${esc(a.learner_name)}</small><p class="muted-line">${esc(a.method)} · ${esc(a.updated_at.split('T')[0])}</p>
          ${statusPill(a.status)}<br>${task('Update','status',`data-id="${a.id}"`,'alt')}</div>`).join('')||'<div class="muted-line">Drop a job here</div>')+
        `</div></section>`;
    }).join('');
    return head('Application board','Drag jobs between stages, or use Update to add submission evidence.',task('+ Add job','add-job','','teal'))+
           `<div class="kanban">${board}</div><p class="muted-line">Moving into Applied, Screened or Offer requires a real receipt/reference and will ask for verification.</p>`;
  }

  function renderQuestions(s){
    const rows=s.questions.filter(x=>!s.questionLearnerFilter||Number(x.learner_id)===Number(s.questionLearnerFilter));
    return head('Screening questions','Save truthful, learner-approved answers for repeated application questions.',task('+ New answer','adv-question','','teal')+task('AI draft','adv-question-ai'))+
      `<div class="filters"><div class="filter-grid">${choices('questionLearner','Filter candidate',[['','All candidates'],...s.learners.map(x=>[x.id,x.name])])}</div></div>`+
      (rows.length?`<div class="table-wrap"><table class="table"><thead><tr><th>Candidate</th><th>Question</th><th>Approved answer</th><th>Usage</th><th>State</th></tr></thead><tbody>`+
        rows.map(x=>`<tr><td>${esc(s.learners.find(l=>l.id===x.learner_id)?.name||'')}</td><td class="primary">${esc(x.question)}</td><td>${esc(x.answer)}</td><td>${x.occurrence_count}</td><td>${statusPill(x.approved?'approved':'needs_review')}</td></tr>`).join('')+`</tbody></table></div>`:
        `<div class="card empty">No screening answers saved. Add a candidate's verified answer before letting automation reuse it.</div>`);
  }

  function renderCVCheck(s){
    const selected=s.cvResult;
    return head('CV checker & versions','Upload multiple role-specific CVs and inspect job-related weaknesses.',task('Upload CV','adv-upload-cv','','teal')+task('Build new CV','adv-generate-cv'))+
      `<div class="analysis-layout"><div class="card"><h2>My candidate CVs</h2><p class="muted">PDF and DOCX up to 6 MB</p><div style="height:14px"></div>`+
      (s.cvs.map(x=>`<div class="flowstep"><div class="stepnum">CV</div><div><strong>${esc(x.label)}</strong><small>${esc(s.learners.find(l=>l.id===x.learner_id)?.name||'')} · ${esc(x.filename)}</small><button class="linkbtn" data-action="adv-scan-cv" data-id="${x.id}">Scan CV</button> <button class="linkbtn" data-action="adv-download-cv" data-id="${x.id}">Download</button></div></div>`).join('')||'<div class="empty">No uploaded CVs</div>')+
      `</div><div class="card"><h2>CV analysis</h2>`+(selected?`<div class="analysis-gauge">${selected.score}<small>/100</small></div><p class="muted">Automated checks; review against actual job requirements.</p>`+
        `<h3 class="subheading">Strengths</h3>${selected.strengths.map(x=>`<div class="analysis-item">✓ ${esc(x)}</div>`).join('')}`+
        `<h3 class="subheading">Recommendations</h3>${selected.warnings.map(x=>`<div class="analysis-item warn">! ${esc(x)}</div>`).join('')}`:
        `<div class="empty">Select “Scan CV” to inspect a stored candidate CV. No analysis has been run yet.</div>`)+`</div></div>`;
  }

  function renderAnalytics(s){
    const data=s.analytics||{by_source:[],by_status:[],by_learner:[]};
    const total=s.dashboard.applications||0;
    const stat=(name,val)=>`<div class="stat"><label>${name}</label><strong>${val||0}</strong></div>`;
    const sources=data.by_source||[],max=Math.max(1,...sources.map(x=>x.applications||0));
    return head('Placement analytics','Review application attempts and real funnel outcomes; optimise each learner’s loop.',task('Refresh analytics','adv-analytics-refresh','','teal'))+
      `<div class="stats">${stat('Active loops',s.dashboard.active_loops)}${stat('Total matches',s.dashboard.matches)}${stat('Verified submissions',s.dashboard.confirmed_applications)}${stat('Emails sent',s.dashboard.emails_sent)}</div>`+
      `<div class="cards"><div class="card"><h2>Application sources</h2><div class="bars">`+
      (sources.map(x=>`<div class="bar-row"><span>${esc(x.source)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.round((x.applications||0)*100/max)}%"></div></div><strong>${x.applications}</strong></div>`).join('')||'<p class="muted">Run a loop and log applications to see sources.</p>')+`</div></div>`+
      `<div class="card"><h2>Per-learner conversion</h2>`+(data.by_learner||[]).map(x=>`<div class="flowstep"><div class="stepnum">${esc((x.name||'?')[0])}</div><div><strong>${esc(x.name)}</strong><small>${x.applications} applications · ${x.interviews||0} interviews · ${x.offers||0} offers</small></div></div>`).join('')+`</div></div>`+
      `<h2 class="subheading">Application attempts</h2><div class="table-wrap"><table class="table"><thead><tr><th>Candidate</th><th>Role</th><th>Engine</th><th>Result</th><th>Reason</th></tr></thead><tbody>`+
      (s.attempts||[]).slice(0,30).map(x=>`<tr><td>${esc(x.learner_name)}</td><td>${esc(x.job_title)}</td><td>${esc(x.engine)}</td><td>${statusPill(x.status)}</td><td>${esc(x.reason||x.evidence)}</td></tr>`).join('')+`</tbody></table></div>`+
      `<div class="callout">Open-tracking pixels and exact interview attribution require an authorised email provider and public hosting. This dashboard never invents open rates.</div>`;
  }

  function searchFiltered(s){
    const q=s.searchFilters||{};
    return s.jobs.filter(x=>{
      if(q.term&&!(`${x.title} ${x.description}`.toLowerCase().includes(q.term.toLowerCase())))return false;
      if(q.location&&!String(x.location||'').toLowerCase().includes(q.location.toLowerCase()))return false;
      if(q.source&&x.source!==q.source)return false;
      if(q.remote&&!(String(x.description||'')+' '+String(x.location||'')).toLowerCase().includes('remote'))return false;
      if(q.seniority&&!(String(x.title).toLowerCase().includes(q.seniority.toLowerCase())))return false;
      if(q.age){const d=new Date(x.posted_at||x.found_at);if(!isNaN(d.getTime())&&(Date.now()-d.getTime())>Number(q.age)*86400000)return false;}
      return true;
    }).slice(0,200);
  }

  function renderSearch(s){
    const results=searchFiltered(s);
    const selected=results.find(x=>x.id===s.selectedJobId)||results[0];
    const cfg=s.searchFilters||{};
    const filterOptions=[['','All job sources'],...Array.from(new Set(s.jobs.map(x=>x.source))).sort().map(x=>[x,x])];
    return head('Search jobs','Find roles collected across configured sources and company feeds.',task('Sync sources','sync','','teal')+task('Import CSV','import-jobs'))+
    `<section class="filters"><h3>ACTIVE FILTERS</h3><div class="filter-grid">${item('term','Job title or keywords','text','Data Analyst')}${item('location','Location','text','London')}${choices('source','Platform',filterOptions)}${choices('age','Date posted',[['','Any time'],['3','Last 3 days'],['7','Last week'],['14','Last 14 days'],['30','Last month']])}`+
    `${choices('seniority','Seniority',[['','All levels'],['junior','Junior'],['graduate','Graduate'],['analyst','Analyst']])}<label class="checkbox"><input type="checkbox" name="remote" ${cfg.remote?'checked':''}> Remote only</label></div>`+
    `<div class="filter-actions">${task('Reset','adv-search-reset','','alt')}${task('Apply filters','adv-search','','teal')}</div></section>`+
    `<h2 class="subheading">${results.length} search results</h2><div class="split-view"><div class="results">`+
    results.map(x=>`<button class="job-result ${selected?.id===x.id?'selected':''}" data-action="adv-job-select" data-id="${x.id}"><strong>${esc(x.title)}</strong><small>${esc(x.company_name||'Employer')} · ${esc(x.location)}</small><div class="tag-row">${label(x.source)}</div></button>`).join('')+
    `</div><article class="job-details">${selected?`<h2>${esc(selected.title)}</h2><p class="muted">${esc(selected.company_name)} · ${esc(selected.location)}</p><div style="margin:16px 0">${links(selected.url,'Open original posting')} ${task('Queue candidate','queue-pick',`data-id="${selected.id}"`,'teal')} ${task('Find recruiter email','adv-find-recruiter',`data-id="${selected.id}"`)} ${task('Tailor CV','adv-tailor',`data-id="${selected.id}"`,'alt')}</div><div class="description">${esc(selected.description||'Description not available from this source')}</div>`:'Select a job to see its description.'}</article></div>`;
  }

  function renderMatches(s){
    const chosen=s.matchFilter||{};
    const filtered=s.matches.filter(x=>(!chosen.decision||chosen.decision===x.decision)&&(!chosen.source||String(x.url).includes(chosen.source))&&(!chosen.keyword||(`${x.job_title} ${x.company_name} ${x.reasons}`).toLowerCase().includes(chosen.keyword.toLowerCase())));
    return head('All matches','Filter by candidate, platform, job score and application readiness.',task('Recalculate','match','','teal')+task('Local AI refine','ai-match'))+
    `<div class="filters"><h3>ACTIVE FILTERS</h3><div class="filter-grid">${choices('decision','Match status',[['','All matches'],['matched','Recommended'],['review','Needs review'],['excluded','Excluded']])}${item('keyword','Search company / description','text','SQL or Power BI')}${choices('learner','Candidate',[['','All candidates'],...s.learners.map(x=>[x.id,x.name])])}</div><div class="filter-actions">${task('Filter matches','adv-matches-filter','','teal')}</div></div>`+
    `<div class="table-wrap"><table class="table"><thead><tr><th>Candidate</th><th>Role</th><th>Match</th><th>Status</th><th>Action</th></tr></thead><tbody>`+
    filtered.filter(x=>!chosen.learner||Number(x.learner_id)===Number(chosen.learner)).slice(0,250).map(x=>`<tr><td>${esc(x.learner_name)}</td><td class="primary">${links(x.url,x.job_title)}<div class="sub">${esc(x.company_name)}</div></td><td><strong>${x.score}%</strong><div class="sub">${esc(x.reasons)}</div></td><td>${statusPill(x.decision)}</td><td>${task('Queue','queue',`data-learner="${x.learner_id}" data-job="${x.job_id}"`,'alt')}</td></tr>`).join('')+
    `</tbody></table></div>`;
  }

  function openWizard(ctx){
    wizard={step:0,data:{mode:'auto',frequency_minutes:120,min_score:60,auto_email:true,tailor_cv:true,boards:[]}};
    ctx.modal('Create a new search loop','<div id="loop-wizard"></div>');
    document.querySelector('#modal').classList.add('wide-dialog');
    drawWizard(ctx);
  }

  function drawWizard(ctx){
    const steps=['Search info','Email template','Settings','Review'];
    const s=ctx.state,d=wizard.data,n=wizard.step;
    const progress=`<div class="wizard-progress">${steps.map((x,i)=>`<div class="wizard-step ${i<=n?'on':''}">${i+1}. ${x}</div>`).join('')}</div>`;
    const boards=Array.isArray(s.boards)?s.boards:[];
    const chosen=new Set(Array.isArray(d.boards)?d.boards:[]);
    let fields='';
    if(n===0){
      fields=`<div class="field-grid">${choices('learner_id','Choose learner',s.learners.map(x=>[x.id,x.name]))}${item('name','Loop name','text','Junior Analytics — London')}
        <div class="full">${item('titles','Target titles (comma separated)','text','Junior Data Analyst, Reporting Analyst, BI Analyst')}</div>
        ${item('location','UK job location','text','London / United Kingdom')}${item('radius_km','Distance radius (km)','number','30')}
        ${choices('experience','Experience',[['junior','Junior'],['graduate','Graduate'],['mid','Mid-level'],['all','All']])}
        ${choices('job_type','Job type',[['full_time','Full-time'],['part_time','Part-time'],['contract','Contract'],['all','All']])}
        <label class="checkbox"><input type="checkbox" name="remote_only"> Remote roles only</label>
        <label class="checkbox"><input type="checkbox" name="hybrid_only"> Hybrid roles only</label>
        <div class="full"><label>Job boards (none ticked = all)</label><div class="board-grid">${boards.map(b=>`<label class="checkbox" title="${esc(b.note)}"><input type="checkbox" data-board="${esc(b.id)}" ${chosen.has(b.id)?'checked':''}> ${esc(b.label)}</label>`).join('')}</div></div>
        ${item('min_salary','Minimum annual salary (£)','number','25000')}${item('industries','Industries (optional)','text','Health, Retail, Finance')}
        ${choices('cv_version_id','CV version',[['','Default learner CV'],...s.cvs.map(x=>[x.id,`${x.label} — ${s.learners.find(l=>l.id===x.learner_id)?.name||''}`])])}</div>`;
    }else if(n===1){fields=`<p class="muted">After applying, the loop finds the recruiter's email and sends a personalised email with the tailored CV attached. Groq personalises it in this style. Placeholders: {first_name} {job_title} {company} {skills} {candidate_name} {candidate_phone}</p>${ta('email_template','Recruiter email template (blank = default)')}
      <label class="checkbox"><input type="checkbox" name="auto_email"> Email recruiters automatically for this loop</label>`;
    }else if(n===2){fields=`<div class="field-grid">${choices('mode','Application mode',[['auto','Auto-apply (LoopCV style)'],['review','Review matches manually']])}
        ${choices('frequency_minutes','Search frequency',[['120','Every 2 hours'],['240','Every 4 hours'],['720','Every 12 hours'],['1440','Daily']])}
        ${item('min_score','Minimum match score','number','60')}${item('excluded_companies','Exclude companies','text','Employer A, Employer B')}
        <label class="checkbox"><input type="checkbox" name="tailor_cv"> Tailor the CV to every job</label>
        <div class="full"><div class="inline-alert">Auto mode applies to every match above the score: Easy Apply on LinkedIn/Indeed/Glassdoor and UK boards through the learner's Chrome extension, company career sites through the server browser. Sponsorship rules still apply.</div></div></div>`;
    }else{fields=`<h2>Ready to create this loop</h2><p><strong>Learner:</strong> ${esc(s.learners.find(x=>x.id===Number(d.learner_id))?.name||'')}</p>
      <p><strong>Titles:</strong> ${esc(d.titles)}</p><p><strong>Location:</strong> ${esc(d.location||'United Kingdom')}</p>
      <p><strong>Mode:</strong> ${esc(d.mode||'review')}</p><p><strong>Frequency:</strong> every ${Number(d.frequency_minutes)||120} min</p>
      <p><strong>Boards:</strong> ${esc((d.boards||[]).join(', ')||'All')}</p><p><strong>Tailored CV:</strong> ${d.tailor_cv?'Yes':'No'} · <strong>Recruiter emails:</strong> ${d.auto_email?'Yes':'No'}</p>
      <div class="callout">Saving searches the selected boards now (1–3 minutes), then every ${Number(d.frequency_minutes)||120} minutes.</div>`;}
    const back=n>0?task('← Back','adv-wizard-back','','alt'):'';
    const next=task(n===3?'Save and run loop':'Next →',n===3?'adv-wizard-save':'adv-wizard-next','','teal');
    document.querySelector('#loop-wizard').innerHTML=progress+`<div class="wizard-panel"><form id="wizard-fields">${fields}</form><div class="wizard-actions">${back}${next}</div></div>`;
    const form=document.querySelector('#wizard-fields');
    for(const [key,val] of Object.entries(d)){
      const el=form.elements.namedItem(key);
      if(!el)continue;
      if(el.type==='checkbox')el.checked=!!val;
      else el.value=val;
    }
  }

  function captureWizard(){
    const form=document.querySelector('#wizard-fields');
    if(!form)return;
    const entries=Object.fromEntries(new FormData(form));
    for(const checkbox of form.querySelectorAll('input[type="checkbox"][name]'))entries[checkbox.name]=checkbox.checked;
    const boardBoxes=form.querySelectorAll('input[data-board]');
    if(boardBoxes.length)entries.boards=[...boardBoxes].filter(x=>x.checked).map(x=>x.dataset.board);
    Object.assign(wizard.data,entries);
  }

  function presentForm(kind,ctx,payload={}){
    const candidates=ctx.state.learners.map(x=>[x.id,x.name]);
    if(kind==='adv-question'||kind==='adv-question-ai'){
      const fields=choices('learner_id','Learner',candidates)+ta('question','Application question')+(kind==='adv-question'?ta('answer','Candidate-approved answer'):'<p class="inline-alert">AI drafts are not approved. Review factual statements before reuse.</p>')+
        (kind==='adv-question'?'<label class="checkbox"><input type="checkbox" name="approved"> Candidate has checked and approved this answer</label>':'');
      ctx.modal(kind==='adv-question'?'Add approved question':'Draft answer using local AI',`<form id="entry-form" data-kind="${kind}">${fields}<button class="btn teal" type="submit">Save answer</button></form>`);
    }else if(kind==='adv-upload-cv'){
      ctx.modal('Upload role-specific CV',`<form id="entry-form" data-kind="adv-upload-cv">${choices('learner_id','Candidate',candidates)}${item('label','Version label','text','Data Analyst CV')}<label>Upload PDF or DOCX<input type="file" accept=".pdf,.docx" name="file" required></label><label class="checkbox"><input type="checkbox" name="is_default"> Make this the default CV</label><button type="submit" class="btn teal">Upload CV</button></form>`);
    }else if(kind==='adv-generate-cv'){
      ctx.modal('Generate CV from verified profile',`<form id="entry-form" data-kind="adv-generate-cv">${choices('learner_id','Candidate',candidates)}${item('label','CV label','text','Master Analytics CV')}<button class="btn teal" type="submit">Generate PDF</button></form>`);
    }else if(kind==='adv-scan-cv'){
      const jobs=ctx.state.jobs.slice(0,100).map(x=>[x.id,x.title+' — '+x.company_name]);
      ctx.modal('Analyse candidate CV',`<form id="entry-form" data-kind="adv-scan-cv"><input type="hidden" name="cv_id" value="${Number(payload.id)}">${choices('job_id','Compare to role',[['','General CV check'],...jobs])}<button class="btn teal" type="submit">Scan CV</button></form>`);
    }
  }

  async function fileToBase64(file){
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]||'');reader.onerror=()=>reject(new Error('File could not be read'));reader.readAsDataURL(file);
    });
  }

  async function handleForm(kind,form,ctx){
    const data=Object.fromEntries(new FormData(form));
    if(kind==='adv-question')data.approved=!!form.elements.approved.checked;
    if(kind==='adv-upload-cv'){
      const file=form.elements.file.files[0];if(!file)throw new Error('Select a CV');
      if(file.size>6000000)throw new Error('CV must be 6 MB or smaller');
      data.filename=file.name;data.content_base64=await fileToBase64(file);data.is_default=!!form.elements.is_default.checked;delete data.file;
    }
    let response;
    if(kind==='adv-question')response=await ctx.api('questions','POST',data);
    else if(kind==='adv-question-ai')response=await ctx.api('questions/suggest','POST',data);
    else if(kind==='adv-upload-cv')response=await ctx.api('cvs/upload','POST',data);
    else if(kind==='adv-generate-cv')response=await ctx.api('cvs/generate','POST',data);
    else if(kind==='adv-scan-cv'){
      response=await ctx.api('cvs/check','POST',{cv_id:Number(data.cv_id),job_id:data.job_id?Number(data.job_id):null});
      ctx.state.cvResult=response;
      ctx.state.tab='cvcheck';
    }
    document.querySelector('#modal').close();
    document.querySelector('#modal').classList.remove('wide-dialog');
    ctx.notice(kind==='adv-question-ai'?'AI draft saved for human approval':kind==='adv-scan-cv'?'CV scanned successfully':'Saved successfully');
    await ctx.refresh();
    return response;
  }

  async function action(name,el,ctx){
    const s=ctx.state,api=ctx.api,refresh=ctx.refresh;
    if(!name.startsWith('adv-'))return false;
    try{
      if(name==='adv-new-loop'){openWizard(ctx);return true;}
      if(name==='adv-open-settings'){s.tab='settings';ctx.render();return true;}
      if(name==='adv-settings-save'){
        const form=document.querySelector('#settings-form');const values={};
        for(const el of form.querySelectorAll('input[name]'))values[el.name]=el.type==='checkbox'?(el.checked?'1':'0'):el.value;
        s.settings=await api('settings','POST',values);ctx.notice('Settings saved');ctx.render();return true;
      }
      if(name==='adv-test-ai'){ctx.notice('Testing Groq…');const r=await api('settings/test-ai','POST',{});ctx.notice(`Groq OK · model ${r.model}${r.model_available?'':' (not in your model list — pick one of: '+r.models.slice(0,6).join(', ')+')'}`,!r.model_available);return true;}
      if(name==='adv-test-smtp'){const r=await api('settings/test-smtp','POST',{});ctx.notice(`Test email sent to ${r.sent_to}`);return true;}
      if(name==='adv-find-recruiter'){ctx.notice('Looking for the recruiter email…');const r=await api('recruiters/find','POST',{job_id:Number(el.dataset.id)});
        ctx.notice(r.length?`Found: ${r.map(c=>c.email+' ('+(c.confidence||c.kind)+')').join(', ')}`:'No recruiter email found for this employer',!r.length);await refresh();return true;}
      if(name==='adv-tailor'){
        const learner=s.learners.length===1?s.learners[0].id:Number(prompt('Learner ID to tailor for: '+s.learners.map(l=>l.id+' = '+l.name).join(', ')));
        if(!learner)return true;ctx.notice('Tailoring CV with Groq…');
        const r=await api('cvs/tailor','POST',{learner_id:learner,job_id:Number(el.dataset.id),force:true});
        ctx.notice(`Created ${r.label}`);await refresh();s.tab='cvcheck';ctx.render();return true;}
      if(name==='adv-wizard-back'){captureWizard();wizard.step=Math.max(0,wizard.step-1);drawWizard(ctx);return true;}
      if(name==='adv-wizard-next'){
        const form=document.querySelector('#wizard-fields');
        if(!form.reportValidity())return true;
        captureWizard();
        if(wizard.step===0&&(!wizard.data.titles||!wizard.data.titles.trim()))throw new Error('Enter job titles');
        wizard.step=Math.min(3,wizard.step+1);drawWizard(ctx);return true;
      }
      if(name==='adv-wizard-save'){
        captureWizard();
        const created=await api('loops','POST',wizard.data);
        document.querySelector('#modal').close();document.querySelector('#modal').classList.remove('wide-dialog');
        ctx.notice('Loop saved — searching job boards now (1–3 minutes)…');
        const run=await api(`loops/${created.id}/run`,'POST',{discover:true});
        s.tab='loops';ctx.notice(`Loop created; ${run.matched} matched jobs and ${run.queued} queued applications`);
        await refresh();return true;
      }
      if(name==='adv-run-loop'){
        ctx.notice('Searching job boards for this loop…');
        const x=await api(`loops/${Number(el.dataset.id)}/run`,'POST',{discover:true});
        ctx.notice(`Cycle complete: ${x.matched} matches, ${x.review} reviews, ${x.queued} new applications`);
        await refresh();return true;
      }
      if(name==='adv-tick'){
        const x=await api('scheduler/tick','POST',{discover:false});ctx.notice(`${x.due} loops ran`);await refresh();return true;
      }
      if(name==='adv-loop-toggle'){
        await api(`loops/${Number(el.dataset.id)}/toggle`,'POST',{active:el.dataset.active==='1'});await refresh();return true;
      }
      if(name==='adv-loop-view'){
        s.selectedLoopId=Number(el.dataset.id);s.loopJobs=await api(`loop-jobs?loop_id=${s.selectedLoopId}`);ctx.render();return true;
      }
      if(name==='adv-loop-status'){
        await api('loop-jobs/review','POST',{loop_id:Number(el.dataset.loop),job_id:Number(el.dataset.job),status:el.dataset.status});
        s.loopJobs=await api(`loop-jobs?loop_id=${s.selectedLoopId}`);ctx.render();return true;
      }
      if(name==='adv-search'||name==='adv-search-reset'){
        if(name==='adv-search-reset')s.searchFilters={};
        else s.searchFilters=Object.fromEntries(new FormData(document.querySelector('.filters').querySelector('form')||filterForm(document.querySelector('.filters'))));
        s.selectedJobId=0;ctx.render();return true;
      }
      if(name==='adv-job-select'){s.selectedJobId=Number(el.dataset.id);ctx.render();return true;}
      if(name==='adv-matches-filter'){
        s.matchFilter=Object.fromEntries(new FormData(filterForm(document.querySelector('.filters'))));ctx.render();return true;
      }
      if(['adv-question','adv-question-ai','adv-upload-cv','adv-generate-cv','adv-scan-cv'].includes(name)){
        presentForm(name,ctx,el.dataset);return true;
      }
      if(name==='adv-download-cv'){
        const response=await fetch(`/api/cvs/${Number(el.dataset.id)}/download`,{headers:{'X-Placement-Key':ctx.key()}});
        if(!response.ok)throw new Error('CV could not be downloaded');
        const file=s.cvs.find(x=>x.id===Number(el.dataset.id));const blob=await response.blob();const url=URL.createObjectURL(blob);
        const a=document.createElement('a');a.href=url;a.download=file?.filename||'candidate-cv.pdf';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);return true;
      }
      if(name==='adv-analytics-refresh'){s.analytics=await api('analytics');await refresh();return true;}
    }catch(error){ctx.notice(error.message,true);return true;}
    return false;
  }

  // Read existing form inputs without losing them to a re-render.
  function filterForm(container){
    const form=document.createElement('form');
    for(const el of container.querySelectorAll('input,select')){
      const copy=el.cloneNode(true);
      if(el.type==='checkbox'){copy.checked=el.checked;copy.value=el.checked?'true':'';}
      else copy.value=el.value;
      form.appendChild(copy);
    }
    return form;
  }

  window.BritAdvanced={render,action,handleForm};
})();
