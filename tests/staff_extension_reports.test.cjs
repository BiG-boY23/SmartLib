const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

test('extension history shows audit details and exports the same dates, audience, and paper', async () => {
  const elements=new Map(), buttons=[], requests=[], downloads=[];
  const el=id=>{if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',textContent:'',options:[],classList:{add(){},remove(){}}});return elements.get(id);};
  let install, markup='';
  const circulation={summary:{unique_borrowers:0,unique_returners:0,borrowed_items:0,returned_items:0},
    start:'2026-07-07',end:'2026-07-07',daily:[],books:[],rows:[]};
  const extensions={summary:{approved_extensions:1,unique_patrons:1},history_note:'Earlier approvals remain in Audit Logs.',
    rows:[{date:'2026-07-07',time:'00:00:00',reference:'REQ-EXT',name:'<Reader>',institutional_id:'R1',
      department:'Computing',course:'BSIT',book:'Research',copy_accession_no:'COPY-01',
      old_due_date:'2026-07-07T18:00:00+08:00',new_due_date:'2026-07-09T18:00:00+08:00',
      actor_name:'Librarian',actor_username:'library',actor_role:'librarian',reason:'<Research extension>'}]};
  const context=vm.createContext({window:{},URLSearchParams,Date,Map,Set,
    localStorage:{getItem:()=>''},sessionStorage:{getItem:()=>''},
    URL:{createObjectURL:()=>'blob:export',revokeObjectURL(){}},setTimeout:callback=>callback(),
    document:{readyState:'loading',head:{appendChild(){}},addEventListener:(_,callback)=>{install=callback;},
      getElementById:id=>id==='navFeedback'?null:el(id),
      createElement:tag=>({style:{},dataset:{},removeAttribute(){},click(){if(tag==='a')downloads.push(this.download);}}),
      querySelector:selector=>selector==='.sidebar-menu'?{appendChild(){}}:{insertAdjacentHTML:(_,html)=>{
        markup=html;for(const [,format] of html.matchAll(/data-extension-export="([^"]+)"/g))buttons.push({dataset:{extensionExport:format}});
      }},
      querySelectorAll:selector=>selector==='[data-extension-export]'?buttons:[],
    },
    fetch:async path=>{requests.push(path);return {ok:true,blob:async()=>({}),json:async()=>{
      if(path.includes('/options'))return {departments:[],courses:[]};
      if(path.includes('/loan-extensions'))return extensions;
      if(path.includes('/book-trends'))return {week_start:'2026-07-06',week_end:'2026-07-12',previous_week_start:'2026-06-29',previous_week_end:'2026-07-05',books:[]};
      return circulation;
    }};},
  });
  vm.runInContext(fs.readFileSync('static/staff-reports.js','utf8'),context);install();
  assert.ok(markup.includes('Loan extension history'));
  for(const [id,value] of Object.entries({reportStart:'2026-07-07',reportEnd:'2026-07-07',reportWeek:'2026-07-07',
    reportRole:'student',reportSort:'course',reportDirection:'desc',reportCourse:'BSIT',reportPaper:'a4',reportActivity:'all',reportBookActivity:'all'}))el(id).value=value;
  await context.window.SmartLibStaff.openReports();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(el('reportExtensionOutput').innerHTML.includes('&lt;Reader&gt;'));
  assert.ok(el('reportExtensionOutput').innerHTML.includes('&lt;Research extension&gt;'));
  for(const value of ['2026-07-07T18:00:00+08:00','2026-07-09T18:00:00+08:00','Librarian','Earlier approvals remain in Audit Logs.']){
    assert.ok(el('reportExtensionOutput').innerHTML.includes(value));
  }
  for(const button of buttons){
    await button.onclick();const query=new URL(requests.at(-1),'http://localhost');
    assert.equal(query.pathname,'/admin/reports/loan-extensions');
    assert.equal(query.searchParams.get('start'),'2026-07-07');assert.equal(query.searchParams.get('end'),'2026-07-07');
    assert.equal(query.searchParams.get('role'),'student');assert.equal(query.searchParams.get('sort'),'course');
    assert.equal(query.searchParams.get('direction'),'desc');assert.equal(query.searchParams.get('format'),button.dataset.extensionExport);
    assert.equal(query.searchParams.get('paper'),['pdf','docx'].includes(button.dataset.extensionExport)?'a4':null);
    assert.ok(downloads.at(-1).endsWith(`.${button.dataset.extensionExport}`));
  }
});
