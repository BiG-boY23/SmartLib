const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const parser = require('../mobile-native/node_modules/@babel/parser');
const html = fs.readFileSync('librarian-dashboard.html', 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]).find(s => s.includes('function loadBorrowQueue'));
const nodes = parser.parse(script).program.body;
const functions = nodes.filter(n => n.type === 'FunctionDeclaration' && ['renderBorrowQueue','loadAcquisitions'].includes(n.id.name)).map(n => script.slice(n.start,n.end)).join('\n');
const rows = [];
const table = {innerHTML:'',insertRow(){const row = {lastElementChild:{append(){}}}; rows.push(row); return row;}};
const fields = {queueStatus:{value:''},queueSearch:{value:''},queueSort:{value:'newest'},queueCount:{textContent:''},borrowQueueTable:{getElementsByTagName(){return [table];}},acquisitionsTable:{getElementsByTagName(){return [table];}}};
const context = vm.createContext({StaffRecords:{refresh:async()=>{},visible:()=>true,viewUrl:path=>path,button:()=>({})},document:{getElementById:id => fields[id]},console,escapeHtml:String,API_BASE:'',getAuthHeaders:()=>({}),queueOffset:0,borrowQueueRows:[],fetch:async()=>({ok:true,json:async()=>[
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
  const adminHtml=fs.readFileSync('admin-dashboard.html','utf8');
  const adminScript=[...adminHtml.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]).find(s=>s.includes('function loadBorrowQueue'));
  const adminRender=parser.parse(adminScript).program.body.find(n=>n.type==='FunctionDeclaration' && n.id.name==='renderBorrowQueue');
  vm.runInContext(adminScript.slice(adminRender.start,adminRender.end),context);
  fields.queueSearch.value='';rows.length=0;
  context.borrowQueueRows.push({req_id:'REQ-3',status:'PENDING',request_date:'2026-10-15',user_name:'Pending',book_title:'Physics'});
  context.renderBorrowQueue();
  assert.ok(rows[0].innerHTML.includes('onclick="approveReq'));
  assert.ok(rows[0].innerHTML.includes('onclick="rejectReq'));
  assert.ok(rows[1].innerHTML.includes('Extend loan'));
  assert.ok(!rows[2].innerHTML.includes('onclick="approveReq'));
  fields.queueStatus.value='APPROVED';rows.length=0;context.renderBorrowQueue();
  assert.equal(rows.length,1);
  assert.ok(rows[0].innerHTML.includes('REQ-2'));
  fields.queueStatus.value='';fields.queueSearch.value='programming';rows.length=0;context.renderBorrowQueue();
  assert.equal(rows.length,1);
  assert.ok(rows[0].innerHTML.includes('REQ-1'));
  for(const [name,source] of [['admin',adminHtml],['librarian',html]]) {
    const markup=source.replace(/<script(?:\s[^>]*)?>[\s\S]*?<\/script>/g,'');
    const ids=[...markup.matchAll(/\sid="([^"]+)"/g)].map(m=>m[1]);
    assert.equal(ids.length,new Set(ids).size,`${name} must not contain duplicate HTML IDs`);
  }
  await testSharedAcquisitions();
  console.log('Admin and librarian queue rendering, acquisition moderation, archive views, and HTML IDs passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

async function testSharedAcquisitions() {
  class Element {
    constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={};this.value='';}
    append(...children){this.children.push(...children);}
    prepend(child){this.children.unshift(child);}
    replaceChildren(...children){this.children=[...children];}
    insertRow(){const row=new Element('tr');this.append(row);return row;}
    insertCell(){const cell=new Element('td');this.append(cell);return cell;}
    querySelectorAll(selector){
      const match=node=>{const attr=selector.match(/^\[data-([a-z-]+)="([^"]+)"\]$/);return attr?node.dataset[attr[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]===attr[2]:node.tag===selector;};
      return this.children.filter(child=>child instanceof Element).flatMap(child=>[...(match(child)?[child]:[]),...child.querySelectorAll(selector)]);
    }
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  }
  const body=new Element('body'), parent=new Element('div');body.append(parent);
  const document={addEventListener(){},createElement:tag=>new Element(tag),querySelectorAll:s=>body.querySelectorAll(s),getElementById:id=>{
    const find=node=>node.id===id?node:node.children.filter(c=>c instanceof Element).map(find).find(Boolean);
    return find(body)||null;
  }};
  let archives=[{key:'acquisitions:ACQ-OLD',reason:'Old suggestion'}], reloads=0, promptValue='Not suitable for the collection';
  const requests=[];
  const recordsContext=vm.createContext({window:{},document,URLSearchParams,getAuthHeaders:()=>({}),toast(){},prompt:()=>promptValue,fetch:async(path,options={})=>{
    requests.push({path,...options});
    if(options.method==='PUT' && path.includes('/archives/'))archives=[...archives,{key:'acquisitions:ACQ-NEW',reason:promptValue}];
    return {ok:true,json:async()=>path==='/auth/me'?{role:'superadmin'}:path==='/admin/archives'?archives:{}};
  }});
  vm.runInContext(fs.readFileSync('static/staff-records.js','utf8'),recordsContext);
  const records=recordsContext.window.StaffRecords;
  const books=[{acq_id:'ACQ-NEW',status:'Under Review',title:'New book'},{acq_id:'ACQ-DONE',status:'Approved for Purchase',title:'Reviewed book'},{acq_id:'ACQ-OLD',status:'Under Review',title:'Archived book'}];
  const reload=async()=>{reloads++;};
  await records.mountAcquisitions(parent,books,reload);
  let tableRows=parent.querySelectorAll('tbody')[0].children;
  assert.equal(tableRows.length,2,'Active view excludes archived suggestions');
  assert.deepEqual(tableRows[0].querySelectorAll('button').map(b=>b.textContent),['Approve','Decline','Archive']);
  assert.deepEqual(tableRows[1].querySelectorAll('button').map(b=>b.textContent),['Archive'],'Reviewed suggestions cannot be reviewed again');
  await tableRows[0].querySelectorAll('button')[1].onclick();
  const review=requests.find(r=>r.path==='/admin/acquisitions/ACQ-NEW/status');
  assert.deepEqual(JSON.parse(review.body),{status:'Declined',reason:promptValue});
  assert.equal(reloads,1);
  await tableRows[0].querySelectorAll('button')[2].onclick();
  assert.equal(reloads,2,'Archive uses the supplied inbox reload callback');
  assert.ok(requests.some(r=>r.method==='PUT' && r.path.includes('/archives/acquisitions/ACQ-NEW?')));
  const selector=parent.querySelector('[data-archive-view="acquisitions"]');selector.value='archived';selector.onchange();
  await records.mountAcquisitions(parent,books,reload);
  assert.equal(parent.querySelector('[data-archive-view="acquisitions"]').value,'archived','The selected archive view survives inbox rerendering');
  tableRows=parent.querySelectorAll('tbody')[0].children;
  assert.equal(tableRows.length,2);
  assert.deepEqual(tableRows[0].querySelectorAll('button').map(b=>b.textContent),['Restore'],'An archived pending suggestion must be restored before review');
  assert.equal(parent.querySelectorAll('[data-queue-exports="acquisitions"]').length,1,'Rerendering does not duplicate export controls');
}
