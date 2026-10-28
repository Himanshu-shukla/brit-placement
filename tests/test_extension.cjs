// Offline Chrome-extension logic checks, without Chrome or access to real sites.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const handlers = {};
const controls = Object.fromEntries(['message','token','learner','cv-version','allowed-hosts','connect','fill','queue','auto','copy','resume','page'].map(id=>[id,{
  id,value:id==='token'?'test-key':'',textContent:'',classList:{toggle(){}},
  addEventListener(event,fn){handlers[id+event]=fn},replaceChildren(){this.options=[]},appendChild(option){this.options.push(option)}
}]));
class FakeInput{constructor(label,value=''){
  this.name=label;this.id='';this.placeholder='';this.type='text';this.labels=[];this.value=value;this.disabled=false;this.readOnly=false;this.tagName='INPUT';this.events=[];
}getAttribute(name){return name==='aria-label'?this.name:null}getClientRects(){return [1]}dispatchEvent(event){this.events.push(event.type)}}
class FakeEvent{constructor(type){this.type=type}}
const fields=[new FakeInput('first name'),new FakeInput('last name'),new FakeInput('email'),new FakeInput('phone'),new FakeInput('not a question'),new FakeInput('email','already@filled.test')];
const dom={
  getElementById:id=>controls[id],
  querySelectorAll:selector=>selector==='input,textarea'?fields:[],
  querySelector:selector=>selector==='h1'?{textContent:'Junior Data Analyst'}:null,
  createElement:()=>({}),title:'Junior Data Analyst | LinkedIn'
};
const fakeChrome={storage:{local:{get:async()=>({}),set:async()=>{}}},tabs:{query:async()=>[{id:1,url:'https://www.linkedin.com/jobs/view/123'}]}};
const sandbox={document:dom,chrome:fakeChrome,console,navigator:{},location:{href:'https://www.linkedin.com/jobs/view/123'},
  URL,Event:FakeEvent,HTMLInputElement:FakeInput,HTMLTextAreaElement:FakeInput};
vm.createContext(sandbox);
const script=fs.readFileSync(path.join(__dirname,'../extension/popup.js'),'utf8');
vm.runInContext(script,sandbox);
const result=vm.runInContext(`fillVisibleFields({name:'Amina Example',email:'amina@example.org',phone:'07000000000'})`,sandbox);
assert.equal(result.filled,4);
assert.equal(fields[0].value,'Amina');
assert.equal(fields[1].value,'Example');
assert.equal(fields[2].value,'amina@example.org');
assert.equal(fields[3].value,'07000000000');
assert.equal(fields[4].value,'');
assert.equal(fields[5].value,'already@filled.test');
assert.deepEqual(fields[2].events,['input','change']);
const job=vm.runInContext('extractAdvert()',sandbox);
assert.equal(job.title,'Junior Data Analyst');
assert.match(job.url,/linkedin.com/);
const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../extension/manifest.json'),'utf8'));
assert.equal(manifest.manifest_version,3);
assert.equal(manifest.permissions.includes('scripting'),true);
console.log('PASS: extension MV3 configuration, 4-field autofill, existing-value preservation, event dispatch, job extraction');
