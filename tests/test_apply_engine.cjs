/* Browser form-state engine tested against a simulated accessible employer form.
   This is NOT a live LinkedIn/Indeed test. */
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');const assert=require('node:assert/strict');
class Input{
  constructor(label,type='text',required=false){this.name=label;this.id='';this.type=type;this.value='';this.required=required;this.disabled=false;this.readOnly=false;this.tagName='INPUT';this.labels=[];}
  getClientRects(){return [1]}
  getAttribute(key){return key==='aria-label'?this.name:''}
  dispatchEvent(){}
  checkValidity(){return !this.required||!!this.value}
}
class Event{constructor(type){this.type=type}}
async function main(){
  const first=new Input('First name','text',true),last=new Input('Last name','text',true),email=new Input('Email','email',true);
  let confirmation='Before submit';
  const button={type:'submit',disabled:false,getClientRects:()=>[1],textContent:'Submit application',click(){confirmation='Thank you for applying. Application received.'}};
  const dom={body:{get innerText(){return confirmation}},getElementById:()=>null,
    querySelector:()=>null,querySelectorAll(selector){if(selector==='input,textarea,select')return [first,last,email];
      if(selector==='button,input[type="submit"]')return [button];return []}};
  const sandbox={document:dom,Event,HTMLInputElement:Input,HTMLTextAreaElement:Input,
    location:{hostname:'apply.example.org'},setTimeout,Uint8Array,Map,Array,String};
  vm.createContext(sandbox);
  const source=fs.readFileSync(path.join(__dirname,'../extension/form_engine.js'),'utf8');
  vm.runInContext(source,sandbox);
  const profile={name:'Amina Example',email:'amina@example.org'};
  const prepared=await vm.runInContext('applyCurrentForm',sandbox)(profile,[],null,{submit:false});
  assert.equal(prepared.status,'prepared');assert.equal(prepared.filled,3);
  assert.equal(first.value,'Amina');assert.equal(last.value,'Example');assert.equal(email.value,'amina@example.org');
  const submitted=await vm.runInContext('applyCurrentForm',sandbox)(profile,[],null,{submit:true,allowedHosts:['apply.example.org']});
  assert.equal(submitted.status,'confirmed');
  confirmation='No confirmation';
  sandbox.location.hostname='www.linkedin.com';
  const restricted=await vm.runInContext('applyCurrentForm',sandbox)(profile,[],null,{submit:true,allowedHosts:['www.linkedin.com']});
  assert.equal(restricted.status,'blocked');
  console.log('PASS: candidate fields, review preparation, permitted employer submission, LinkedIn submission restriction');
}
main().catch(err=>{console.error(err);process.exitCode=1});
