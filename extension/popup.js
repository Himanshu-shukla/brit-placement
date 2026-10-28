const API = 'http://127.0.0.1:8770/api/';
const byId = (id) => document.getElementById(id);
let candidates = [];
let cvVersions = [];
let savedAnswers = [];
const notify = (message, error = false) => {byId('message').textContent = message;byId('message').classList.toggle('error',error)};
const selection = () => candidates.find(x => String(x.id) === byId('learner').value);

async function api(path, method='GET', body) {
  const token=byId('token').value.trim();
  if(!token) throw new Error('Enter the backend admin key');
  const response=await fetch(API+path,{method,headers:{'X-Placement-Key':token,...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data=await response.json();
  if(!response.ok) throw new Error(data.error||'Backend error');
  return data;
}

async function activeTab(){
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  if(!tab||!/^https?:\/\//.test(tab.url||'')) throw new Error('Open an HTTPS job page first');
  return tab;
}

// Executed ONLY when the user clicks the popup button. No background crawling.
function extractAdvert(){
  const pick=(selectors)=>{for(const s of selectors){const e=document.querySelector(s);const value=e?.textContent?.trim();if(value)return value.slice(0,240)}return ''};
  const title=pick(['.job-details-jobs-unified-top-card__job-title','.jobs-unified-top-card__job-title','[data-testid="jobsearch-JobInfoHeader-title"]','h1']);
  const company=pick(['.job-details-jobs-unified-top-card__company-name','.jobs-unified-top-card__company-name','[data-testid="inlineHeader-companyName"]','[data-testid="company-name"]']);
  return {title:title||document.title.split(' | ')[0], company:company||new URL(location.href).hostname, url:location.href};
}

function fillVisibleFields(profile){
  const values={name:profile.name||'',email:profile.email||'',phone:profile.phone||'',location:profile.location||''};
  let completed=0;const names=[];
  const split=values.name.trim().split(/\s+/);values.firstname=split[0]||'';values.lastname=split.slice(1).join(' ');
  function fieldType(el){
    const nearLabel=el.labels&&el.labels.length?Array.from(el.labels).map(l=>l.textContent||'').join(' '):'';
    const label=String([el.getAttribute('aria-label'),el.name,el.id,el.placeholder,nearLabel].filter(Boolean).join(' ')).toLowerCase();
    if((el.type||'').toLowerCase() === 'email'||/e[- ]?mail/.test(label))return 'email';
    if((el.type||'').toLowerCase() === 'tel'||/phone|mobile|telephone/.test(label))return 'phone';
    if(/first[ -]?name|given[ -]?name/.test(label))return 'firstname';
    if(/last[ -]?name|surname|family[ -]?name/.test(label))return 'lastname';
    if(/full[ -]?name|your[ -]?name|applicant[ -]?name/.test(label)||/^name$/.test(label.trim()))return 'name';
    if(/city|location/.test(label)&&!/(current job|desired|remote)/.test(label))return 'location';
    return null;
  }
  const elements=[...document.querySelectorAll('input,textarea')];
  for(const el of elements){
    if(!el.getClientRects().length||el.disabled||el.readOnly)continue;
    if(['hidden','file','password','checkbox','radio','submit'].includes((el.type||'').toLowerCase()))continue;
    const type=fieldType(el),value=values[type];
    if(!type||!value||el.value?.trim())continue;
    const setter=Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value')?.set;
    if(setter) setter.call(el,value);else el.value=value;
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    completed++;names.push(type);
  }
  return {filled:completed,types:names};
}

async function inject(func,args=[]){
  const tab=await activeTab();
  const result=await chrome.scripting.executeScript({target:{tabId:tab.id},func,args});
  return result[0]?.result;
}

async function connect(){
  const [profiles,cvs,questions]=await Promise.all([api('learners'),api('cvs'),api('questions')]);
  candidates=profiles;cvVersions=cvs;savedAnswers=questions;
  byId('learner').replaceChildren();
  for(const item of candidates){const option=document.createElement('option');option.value=String(item.id);option.textContent=`${item.name} · ${item.email}`;byId('learner').appendChild(option)}
  const last=await chrome.storage.local.get('lastLearner');
  if(last.lastLearner && candidates.some(x=>String(x.id)===last.lastLearner))byId('learner').value=last.lastLearner;
  showCVs();
  notify(`${candidates.length} candidates loaded`);
}

function showCVs(){
  const person=selection(),dropdown=byId('cv-version');
  dropdown.replaceChildren();
  const empty=document.createElement('option');empty.value='';empty.textContent='Attach CV manually';dropdown.appendChild(empty);
  for(const cv of cvVersions.filter(x=>x.learner_id===person?.id)){
    const option=document.createElement('option');option.value=String(cv.id);option.textContent=cv.label+(cv.is_default?' — default':'');dropdown.appendChild(option);
    if(cv.is_default)dropdown.value=String(cv.id);
  }
}

async function cvPayload(){
  const id=Number(byId('cv-version').value);if(!id)return null;
  const version=cvVersions.find(x=>x.id===id);
  if(!version)throw new Error('CV not found for this learner');
  const token=byId('token').value.trim();
  const response=await fetch(API+`cvs/${id}/download`,{headers:{'X-Placement-Key':token}});
  if(!response.ok)throw new Error('Could not load candidate CV');
  const binary=new Uint8Array(await response.arrayBuffer());
  let encoded='';
  for(let i=0;i<binary.length;i+=16384)encoded+=String.fromCharCode(...binary.subarray(i,i+16384));
  return {filename:version.filename,mime_type:version.mime_type,content_base64:btoa(encoded)};
}

async function queueCurrent(person){
  const job=await inject(extractAdvert);
  if(!job?.title||!job?.url)throw new Error('Could not identify this vacancy');
  const url=new URL(job.url);
  const company=await api('companies','POST',{name:job.company||url.hostname,domain:url.hostname,ats:'manual',ats_slug:url.hostname+'__'+(job.company||'')});
  const saved=await api('jobs','POST',{company_id:company.id,title:job.title,url:job.url});
  const application=await api('applications','POST',{learner_id:person.id,job_id:saved.id,method:'browser_assisted'});
  byId('page').textContent=`${job.title} — ${job.company}`;
  return {...application,job};
}

async function perform(task){
  try{
    const person=selection();
    if(!person && task!=='connect')throw new Error('Connect and select a candidate');
    if(task==='connect'){await chrome.storage.local.set({token:byId('token').value.trim()});return connect()}
    if(!person.permission_to_apply)throw new Error('Candidate consent is not recorded in the backend');
    await chrome.storage.local.set({lastLearner:String(person.id)});
    if(task==='fill'){
      const answers=savedAnswers.filter(x=>x.learner_id===person.id&&x.approved);
      const result=await inject(applyCurrentForm,[person,answers,await cvPayload(),{submit:false}]);
      notify(`${result?.filled||0} field(s) filled; CV ${result?.uploaded?'attached':'not attached'}. ${result?.reason||'Review and submit manually.'}`);
    }else if(task==='queue'){
      await queueCurrent(person);
      notify('Saved and queued. This is NOT a submission confirmation.');
    }else if(task==='auto'){
      const tab=await activeTab();const hostname=new URL(tab.url).hostname.toLowerCase();
      const allowed=byId('allowed-hosts').value.split(',').map(x=>x.trim().toLowerCase()).filter(Boolean);
      if(!allowed.includes(hostname))throw new Error(`Explicitly add ${hostname} to Allowed employer hostnames`);
      if(!confirm(`Attempt to submit this job application for ${person.name} on ${hostname}? Only continue if you have consent and permission to automate this employer site.`))return;
      await chrome.storage.local.set({allowedHosts:byId('allowed-hosts').value});
      const application=await queueCurrent(person);
      const answers=savedAnswers.filter(x=>x.learner_id===person.id&&x.approved);
      const result=await inject(applyCurrentForm,[person,answers,await cvPayload(),{submit:true,allowedHosts:allowed}]);
      await api('attempts','POST',{application_id:application.id,status:result.status,
         steps:result.steps||0,reason:result.reason||'',evidence:result.evidence||''});
      notify(`Application ${result.status}: ${result.reason||result.evidence||''} (${result.filled||0} fields filled).`);
    }else if(task==='copy'){
      await navigator.clipboard.writeText(`${person.name}\n${person.email}\n${person.phone}\n${person.location}\nSkills: ${person.skills}`);
      notify('Verified candidate details copied');
    }else if(task==='resume'){
      // A key header is required for API access; print view is opened via the authenticated dashboard.
      await chrome.tabs.create({url:'http://127.0.0.1:8770/'});
      notify('Dashboard opened: Learners → Open CV. Review and save as PDF.');
    }
  }catch(err){notify(err.message||String(err),true)}
}

for(const id of ['connect','fill','queue','auto','copy','resume'])byId(id).addEventListener('click',()=>perform(id));
byId('learner').addEventListener('change',()=>{chrome.storage.local.set({lastLearner:byId('learner').value});showCVs()});
chrome.storage.local.get(['token','lastLearner','allowedHosts']).then(data=>{
  if(data.allowedHosts)byId('allowed-hosts').value=data.allowedHosts;
  if(data.token){byId('token').value=data.token;connect().catch(err=>notify(err.message,true))}
});
