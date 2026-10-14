const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const parser = require('../mobile-native/node_modules/@babel/parser');
const html = fs.readFileSync('librarian-dashboard.html', 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]).find(s => s.includes('function loadBorrowQueue'));
const nodes = parser.parse(script).program.body;
const functions = nodes.filter(n => n.type === 'FunctionDeclaration' && ['renderBorrowQueue','loadAcquisitions'].includes(n.id.name)).map(n => script.slice(n.start,n.end)).join('\n');
const rows = [];
const table = {innerHTML:'',insertRow(){const row = {}; rows.push(row); return row;}};
const fields = {queueStatus:{value:''},queueSearch:{value:''},queueSort:{value:'newest'},queueCount:{textContent:''},borrowQueueTable:{getElementsByTagName(){return [table];}},acquisitionsTable:{getElementsByTagName(){return [table];}}};
const context = vm.createContext({document:{getElementById:id => fields[id]},console,escapeHtml:String,API_BASE:'',getAuthHeaders:()=>({}),queueOffset:0,borrowQueueRows:[],fetch:async()=>({ok:true,json:async()=>[
  {acq_id:'ACQ-1',title:'Book',author:'Author',status:'Approved for Purchase',created_at:'2026-10-14'},
  {acq_id:'ACQ-2',title:'Book',author:'Author',status:'Under Review',created_at:'2026-10-14'}
]})});
vm.runInContext(functions, context);
(async()=>{
  await context.loadAcquisitions();
  assert.ok(rows[0].innerHTML.includes('Reviewed'));
  assert.ok(!rows[0].innerHTML.includes('onclick="updateAcq'));
  assert.ok(rows[1].innerHTML.includes('onclick="updateAcq'));
  rows.length=0;
  context.borrowQueueRows = [{req_id:'REQ-1',status:'CHECKED_OUT',request_date:'2026-10-14',user_name:'Reader',book_title:'Programming'},{req_id:'REQ-2',status:'APPROVED',request_date:'2026-10-13',user_name:'Other',book_title:'Math'}];
  context.renderBorrowQueue();
  assert.ok(rows[0].innerHTML.includes('Extend loan'));
  assert.ok(!rows[1].innerHTML.includes('onclick="approveReq'));
  fields.queueSearch.value='programming';rows.length=0;context.renderBorrowQueue();
  assert.equal(rows.length,1);
  assert.ok(rows[0].innerHTML.includes('REQ-1'));
  console.log('Staff acquisition actions and borrowing queue rendering passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
