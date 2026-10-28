/* Injected, self-contained browser form engine. The caller supplies candidate-
   approved answers and only enables submission on explicitly allowed sites.
   No captcha solving, stealth controls, login handling, or credential access. */
async function applyCurrentForm(profile, approvedAnswers, cvPayload, options={}) {
  const normalize=s=>String(s||'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
  const visible=el=>!!el && !!el.getClientRects().length && !el.disabled;
  const labelOf=el=>{
    const labels=el.labels?Array.from(el.labels).map(l=>l.innerText||l.textContent||'').join(' '):'';
    const aria=el.getAttribute('aria-labelledby');
    return String([labels,el.getAttribute('aria-label'),el.getAttribute('placeholder'),el.name,el.id,
      aria&&document.getElementById(aria)?.textContent].filter(Boolean).join(' ')).trim();
  };
  const fields={name:profile.name||'',email:profile.email||'',phone:profile.phone||'',location:profile.location||''};
  const nameParts=fields.name.split(/\s+/).filter(Boolean);
  fields.firstname=nameParts[0]||'';fields.lastname=nameParts.slice(1).join(' ');
  const answerMap=new Map((approvedAnswers||[]).filter(x=>x.approved).map(x=>[normalize(x.question),String(x.answer)]));
  const notes=[],unknown=[];
  let completed=0,uploaded=false;
  const setText=(el,value)=>{
    const descriptor=Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value');
    if(descriptor?.set)descriptor.set.call(el,value);else el.value=value;
    el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
  };
  const autoValue=el=>{
    const label=normalize(labelOf(el));const type=String(el.type||'').toLowerCase();
    if(type==='email'||/\bemail\b/.test(label))return fields.email;
    if(type==='tel'||/phone|mobile|telephone/.test(label))return fields.phone;
    if(/first name|given name/.test(label))return fields.firstname;
    if(/last name|surname|family name/.test(label))return fields.lastname;
    if(/full name|your name|applicant name/.test(label)||label==='name')return fields.name;
    if(/current city|current location/.test(label))return fields.location;
    return answerMap.get(label)||'';
  };
  const inputs=Array.from(document.querySelectorAll('input,textarea,select'));
  for(const el of inputs){
    if(!visible(el)||el.readOnly)continue;
    const type=String(el.type||'').toLowerCase();
    if(['hidden','password','submit','button','reset','file','checkbox','radio'].includes(type))continue;
    const value=autoValue(el);
    if(!value)continue;
    if(el.tagName==='SELECT'){
      if(el.value&&el.selectedIndex>0)continue;
      const found=Array.from(el.options||[]).find(x=>normalize(x.textContent)===normalize(value)||normalize(x.value)===normalize(value));
      if(!found)continue;
      el.value=found.value;el.dispatchEvent(new Event('change',{bubbles:true}));completed++;
    }else if(!String(el.value||'').trim()){
      setText(el,value);completed++;
    }
  }
  // Upload a verified candidate PDF/DOCX through the browser's File API.
  if(cvPayload?.content_base64){
    try{
      const fileInput=Array.from(document.querySelectorAll('input[type="file"]')).find(visible);
      if(fileInput){
        const bytes=Uint8Array.from(atob(cvPayload.content_base64),c=>c.charCodeAt(0));
        const file=new File([bytes],cvPayload.filename||'candidate-cv.pdf',{type:cvPayload.mime_type||'application/pdf'});
        const transfer=new DataTransfer();transfer.items.add(file);
        fileInput.files=transfer.files;
        fileInput.dispatchEvent(new Event('change',{bubbles:true}));
        uploaded=true;
      }
    }catch(error){notes.push('CV upload needs manual completion: '+String(error.message||error).slice(0,100));}
  }
  const challenge=!!document.querySelector('iframe[src*="recaptcha"],iframe[src*="captcha"],input[name*="captcha"],#captcha');
  if(challenge)return {status:'blocked',filled:completed,uploaded,steps:1,reason:'CAPTCHA/challenge detected',unknown,notes};
  const missing=[];
  for(const el of inputs){
    if(!visible(el)||!el.required)continue;
    if(typeof el.checkValidity==='function'&&!el.checkValidity())missing.push(labelOf(el)||el.name||el.type||'required field');
  }
  // Some custom questions look optional to the browser. Flag them for review if
  // they look sensitive and no candidate-approved answer exists.
  const sensitive=/(visa|sponsor|work authori|right to work|citizen|disabilit|gender|ethnic|nationality|salary expectation|criminal|veteran)/i;
  for(const el of inputs){
    if(!visible(el)||!sensitive.test(labelOf(el)))continue;
    const text=labelOf(el);const value=el.type==='checkbox'||el.type==='radio'?el.checked:el.value;
    if(!value && !answerMap.has(normalize(text)))unknown.push(text.slice(0,150));
  }
  if(missing.length||unknown.length){
    return {status:'needs_review',filled:completed,uploaded,steps:1,
      reason:'Unanswered fields: '+[...missing,...unknown].join('; ').slice(0,400),unknown:[...missing,...unknown],notes};
  }
  if(!options.submit){
    return {status:'prepared',filled:completed,uploaded,steps:1,
      reason:'Fields prepared. Verify and submit manually.',unknown,notes};
  }
  const hostname=location.hostname.toLowerCase();
  const allowed=(options.allowedHosts||[]).map(x=>String(x).toLowerCase());
  const buttons=Array.from(document.querySelectorAll('button,input[type="submit"]')).filter(visible);
  const submitButton=buttons.find(el=>/^(submit application|submit|send application|apply now|apply)$/.test(normalize(el.textContent||el.value||el.getAttribute('aria-label'))));
  if(!submitButton)return {status:'needs_review',filled:completed,uploaded,steps:1,reason:'No unambiguous submit button found',unknown,notes};
  submitButton.click();
  await new Promise(resolve=>setTimeout(resolve,2200));
  const confirmation=(document.body?.innerText||'').toLowerCase();
  const phrases=['application submitted','application received','thank you for applying','thanks for applying','we have received your application','successfully applied'];
  if(phrases.some(text=>confirmation.includes(text))){
    return {status:'confirmed',filled:completed,uploaded,steps:2,evidence:'Employer page displayed application confirmation',notes};
  }
  return {status:'unverified',filled:completed,uploaded,steps:2,
    reason:'Submit clicked but no confirmation detected. Do not retry before checking manually.',unknown,notes};
}
