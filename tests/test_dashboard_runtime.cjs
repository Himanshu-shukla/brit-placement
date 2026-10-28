const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const handlers={};
function node(name){return {name,innerHTML:'',textContent:'',dataset:{},hidden:true,classList:{toggle(){},remove(){},add(){}},
  addEventListener(type,handler){handlers[name+':'+type]=handler},querySelector(){return null}}}
const nodes=Object.fromEntries(['#today','#notice','#navigation','#content','#modal-body','#close-modal','#refresh','#top-new-loop','#modal'].map(name=>[name,node(name)]));
nodes['#modal'].close=()=>{};nodes['#modal'].showModal=()=>{};
const document={querySelector:name=>nodes[name]||null,querySelectorAll:name=>name==='.nav'?[node('nav')]:[]};
const sandbox={window:{setTimeout},document,Date,setTimeout,console,
  sessionStorage:{getItem:()=> 'testkey',setItem(){},removeItem(){}},
  fetch:async url=>({ok:true,status:200,json:async()=>url.includes('dashboard')?{loops:0,jobs:0}:url.includes('analytics')?{by_source:[],by_learner:[]}:[]})};
vm.createContext(sandbox);
for(const file of ['advanced.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/'+file),'utf8'),sandbox);
setTimeout(()=>{
  try{
    assert.match(nodes['#content'].innerHTML,/Placement overview/);
    assert.ok(handlers['#navigation:click']);
    handlers['#navigation:click']({target:{closest:()=>({dataset:{tab:'loops'}})}});
    assert.match(nodes['#content'].innerHTML,/No loops configured/);
    handlers['#navigation:click']({target:{closest:()=>({dataset:{tab:'jobs'}})}});
    assert.match(nodes['#content'].innerHTML,/Search jobs/);
    console.log('PASS: dashboard initializes, authenticated data hydrates, and navigation renders new screens');
  }catch(error){console.error(error);process.exitCode=1}
},80);
