const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const sandbox={window:{},document:{},console};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/advanced.js'),'utf8'),sandbox);
const ui=sandbox.window.BritAdvanced;
const job={id:1,title:'Junior Data Analyst',company_name:'Example Ltd',description:'SQL Power BI',location:'London',source:'greenhouse',url:'https://example.org/job/1',found_at:'2026-10-27T08:00:00Z'};
const state={
  loops:[{id:1,name:'London Analytics',titles:'["Junior Data Analyst"]',boards:'["greenhouse"]',
          learner_name:'Amina',location:'London',active:1,mode:'review',frequency_minutes:120,job_count:1,run_count:2,next_run_at:'',last_error:''}],
  learners:[{id:1,name:'Amina'}],jobs:[job],matches:[{id:1,learner_id:1,learner_name:'Amina',job_id:1,
         job_title:'Junior Data Analyst',company_name:'Example Ltd',url:job.url,score:88,decision:'matched',reasons:'SQL Power BI'}],
  applications:[{id:1,learner_name:'Amina',job_title:'Junior Data Analyst',status:'queued',updated_at:'2026-10-27',method:'loop_auto'}],
  cvs:[],questions:[],attempts:[],searchFilters:{},matchFilter:{},dashboard:{active_loops:1,jobs:1,matches:1,applications:1},
  analytics:{by_status:[],by_source:[],by_learner:[]},loopJobs:[],selectedLoopId:0,selectedJobId:0
};
const expected={loops:'My search loops',board:'Application board',questions:'Screening questions',
  cvcheck:'CV checker',analytics:'Placement analytics',jobs:'Search jobs',matches:'All matches'};
for(const [tab,heading] of Object.entries(expected)){
  const html=ui.render(tab,state);
  assert.equal(typeof html,'string');
  assert.ok(html.includes(heading),`${tab}: missing ${heading}`);
  assert.ok(html.length>200,`${tab}: unexpectedly short HTML`);
}
assert.equal(ui.render('overview',state),null);
console.log('PASS: 7 advanced dashboard screens render against fixture state');
