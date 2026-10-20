const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

test('both staff dashboards load shared reporting and messaging controls', () => {
  for (const page of ['admin-dashboard.html', 'librarian-dashboard.html']) {
    const html = fs.readFileSync(page, 'utf8');
    for (const script of ['staff-reports.js', 'staff-chat.js', 'staff-records.js']) {
      assert.ok(html.includes(`/static/${script}`), `${page} includes ${script}`);
    }
  }
});

test('book views distinguish returns and borrowing and weekly requests carry audience filters', async () => {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, {value: '', options:[], innerHTML: '', textContent: '', classList: {add() {}, remove() {}}});
    return elements.get(id);
  };
  for (const [id, value] of Object.entries({reportStart:'2026-10-12', reportEnd:'2026-10-18', reportWeek:'2026-10-14', reportRole:'all', reportSort:'date', reportDirection:'asc', reportBookActivity:'all', reportActivity:'all'})) el(id).value = value;
  const calls = [];
  const book = (title, borrowed_items, returned_items) => ({title, borrowed_items, returned_items, author:'Writer', accession_no:'B1'});
  const report = {
    summary: {unique_borrowers:2, unique_returners:1, borrowed_items:2, returned_items:1},
    start:'2026-10-12', end:'2026-10-18', daily:[],
    books:[book('<Script title>',2,0),book('Returned only',0,1)],
    rows:[{date:'2026-10-13', book:'Checkout only', action:'Borrowed'}, {date:'2026-10-14', book:'Return only', action:'Returned'}],
  };
  const trends = {
    week_start:'2026-10-12', week_end:'2026-10-18', previous_week_start:'2026-10-05', previous_week_end:'2026-10-11',
    books:[{...book('Popular book',2,1), rank:1, borrowed_change:1, returned_change:-1, previous_borrowed_items:1, previous_returned_items:2}],
  };
  const context = vm.createContext({window:{}, URLSearchParams, Date, Map, Set,
    localStorage:{getItem:()=>''}, sessionStorage:{getItem:()=>''},
    document:{readyState:'loading', addEventListener(){}, getElementById:el, querySelectorAll:()=>[]},
    fetch:async path => {
      calls.push(path);
      return {ok:true, json:async()=>path.includes('/options')?{departments:[],courses:[]}:path.includes('/book-trends')?trends:report};
    },
  });
  vm.runInContext(fs.readFileSync('static/staff-reports.js','utf8'),context);
  await context.window.SmartLibStaff.openReports();
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(el('reportBookTable').innerHTML.includes('&lt;Script title&gt;'));
  el('reportBookActivity').value='returned';el('reportBookActivity').onchange();
  assert.ok(el('reportBookTable').innerHTML.includes('Returned only'));
  assert.ok(!el('reportBookTable').innerHTML.includes('Script title'));
  el('reportActivity').value='Returned';el('reportActivity').onchange();
  assert.ok(el('reportActivityTable').innerHTML.includes('Return only'));
  assert.ok(!el('reportActivityTable').innerHTML.includes('Checkout only'));
  const weekly = new URL(calls.find(path=>path.includes('/book-trends')), 'http://localhost');
  assert.equal(weekly.searchParams.get('week'),'2026-10-14');
  assert.equal(weekly.searchParams.get('role'),'all');
  assert.ok(el('reportTrendOutput').innerHTML.includes('Popular book'));
  assert.ok(el('reportTrendOutput').innerHTML.includes('+1'));
  assert.ok(el('reportTrendOutput').innerHTML.includes('-1'));
});

test('circulation and weekly downloads use selected filters, Excel, and PDF / Word paper', async () => {
  const elements = new Map(), buttons = [], requests = [], downloads = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, {value:'', innerHTML:'', textContent:'', options:[], classList:{add(){}, remove(){}}});
    return elements.get(id);
  };
  let install, markup = '';
  const context = vm.createContext({window:{}, URLSearchParams, Date, Map, Set,
    localStorage:{getItem:()=>''}, sessionStorage:{getItem:()=>''},
    URL:{createObjectURL:()=>'blob:report', revokeObjectURL(){}}, setTimeout:callback=>callback(),
    document:{readyState:'loading', head:{appendChild(){}},
      addEventListener:(_, callback)=>{install=callback;},
      getElementById:id=>id==='navFeedback'?null:el(id),
      createElement:tag=>({style:{}, dataset:{}, removeAttribute(){}, click(){if(tag==='a')downloads.push(this.download);}}),
      querySelector:selector=>selector==='.sidebar-menu'?{appendChild(){}}:{insertAdjacentHTML:(_, html)=>{
        markup=html;
        for(const [, kind, format] of html.matchAll(/data-(export|trend-export)="([^"]+)"/g)) buttons.push({dataset:{[kind==='export'?'export':'trendExport']:format}});
      }},
      querySelectorAll:selector=>buttons.filter(b=>selector==='[data-export]'?b.dataset.export:selector==='[data-trend-export]'?b.dataset.trendExport:false),
    },
    fetch:async path=>{requests.push(path);return {ok:true, blob:async()=>({})};},
  });
  vm.runInContext(fs.readFileSync('static/staff-reports.js','utf8'), context);
  install();
  assert.ok(markup.includes('Export Excel (.xlsx)'));
  assert.ok(markup.includes('Export weekly Excel (.xlsx)'));
  assert.ok(markup.includes('CSV has no cell formatting.'));
  assert.ok(markup.includes('<option value="long">Long bond (8.5 × 13 in)</option><option value="a4">A4</option>'));
  for(const [id, value] of Object.entries({reportStart:'2026-10-01', reportEnd:'2026-10-19', reportWeek:'2026-10-12', reportDepartment:'Engineering', reportCourse:'Civil Engineering', reportRole:'student', reportSort:'course', reportDirection:'desc'}))el(id).value=value;
  for(const [dataset, endpoint] of [['export','/admin/reports/circulation'], ['trendExport','/admin/reports/book-trends']]) {
    assert.deepEqual(buttons.filter(b=>b.dataset[dataset]).map(b=>b.dataset[dataset]), ['xlsx','csv','pdf','docx']);
    await buttons.find(b=>b.dataset[dataset]==='xlsx').onclick();
    const query=new URL(requests.at(-1), 'http://localhost');
    assert.equal(query.pathname, endpoint);
    assert.equal(query.searchParams.get('format'), 'xlsx');
    assert.equal(query.searchParams.get('department'), 'Engineering');
    assert.equal(query.searchParams.get('course'), 'Civil Engineering');
    assert.equal(query.searchParams.get('role'), 'student');
    assert.equal(query.searchParams.has('paper'), false, 'Excel is independent of paper size');
    assert.ok(downloads.at(-1).endsWith('.xlsx'));
    if(dataset==='export') {
      assert.equal(query.searchParams.get('start'), '2026-10-01');
      assert.equal(query.searchParams.get('end'), '2026-10-19');
      assert.equal(query.searchParams.get('sort'), 'course');
      assert.equal(query.searchParams.get('direction'), 'desc');
    } else assert.equal(query.searchParams.get('week'), '2026-10-12');
    el('reportPaper').value='';
    await buttons.find(b=>b.dataset[dataset]==='pdf').onclick();
    assert.equal(new URL(requests.at(-1), 'http://localhost').searchParams.get('paper'), 'long', 'Long bond is the default');
    el('reportPaper').value='a4';
    for(const format of ['pdf','docx']) {
      await buttons.find(b=>b.dataset[dataset]===format).onclick();
      const paperQuery=new URL(requests.at(-1), 'http://localhost');
      assert.equal(paperQuery.pathname, endpoint);
      assert.equal(paperQuery.searchParams.get('format'), format);
      assert.equal(paperQuery.searchParams.get('paper'), 'a4');
      assert.equal(paperQuery.searchParams.get('role'), 'student');
      assert.ok(downloads.at(-1).endsWith(`.${format}`));
    }
  }
});

test('SuperAdmin queue exports retain filters, independent paper sizes, and separate archive views by kind', async () => {
  class Element {
    constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={};this.value='';}
    append(...children){this.children.push(...children);}
    prepend(child){this.children.unshift(child);}
    closest(){return this.parentElement;}
    querySelectorAll(selector){
      const match=node=>{
        const attr=selector.match(/^\[data-([a-z-]+)="([^"]+)"\]$/);
        return attr?node.dataset[attr[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]===attr[2]:node.tag===selector;
      };
      return this.children.filter(child=>child instanceof Element).flatMap(child=>[...(match(child)?[child]:[]),...child.querySelectorAll(selector)]);
    }
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  }
  const body=new Element('body'), acquisitions=new Element('div'), queue=new Element('div');body.append(acquisitions,queue);
  const elements={acquisitionsTable:{parentElement:acquisitions},queueStatus:{value:'CHECKED_OUT',closest:()=>queue},queueSearch:{value:'Two words'}};
  const requests=[], downloads=[];let install;
  const document={addEventListener:(_,callback)=>{install=callback;},createElement:tag=>{
    const node=new Element(tag);if(tag==='a')node.click=()=>downloads.push(node.download);return node;
  },querySelectorAll:selector=>body.querySelectorAll(selector),getElementById:id=>elements[id]||null};
  const context=vm.createContext({window:{},document,URLSearchParams,getAuthHeaders:()=>({}),toast(){},loadAcquisitions(){},
    URL:{createObjectURL:()=>'blob:report',revokeObjectURL(){}},setTimeout:callback=>callback(),
    fetch:async path=>{requests.push(path);return {ok:true,blob:async()=>({}),json:async()=>path==='/auth/me'?{role:'superadmin'}:[]};},
  });
  vm.runInContext(fs.readFileSync('static/staff-records.js','utf8'),context);
  await install();
  for(const [kind,parent] of [['acquisitions',acquisitions],['borrow-requests',queue]]) {
    assert.deepEqual(parent.querySelectorAll('button').map(b=>b.textContent), ['Export Excel (.xlsx)','Export CSV','Export PDF','Export Word']);
    assert.ok(parent.querySelectorAll('p').some(p=>p.textContent.includes('CSV has no cell formatting.')));
  }
  const selector=acquisitions.querySelector('[data-archive-view="acquisitions"]');selector.value='archived';selector.onchange();
  const acquisitionPaper=acquisitions.querySelector('[data-export-paper="acquisitions"]');
  const queuePaper=queue.querySelector('[data-export-paper="borrow-requests"]');
  assert.equal(acquisitionPaper.value,'long');assert.equal(queuePaper.value,'long');
  assert.notEqual(acquisitionPaper.id,queuePaper.id);
  acquisitionPaper.value='a4';
  assert.equal(queuePaper.value,'long','Each export bar has an independent paper choice');
  for(const [kind,parent] of [['acquisitions',acquisitions],['borrow-requests',queue]]) {
    await parent.querySelectorAll('button')[0].onclick();
    const query=new URL(requests.at(-1),'http://localhost');
    assert.equal(query.pathname,'/admin/reports/queue');
    assert.equal(query.searchParams.get('format'),'xlsx');
    assert.equal(query.searchParams.get('kind'),kind);
    assert.equal(query.searchParams.get('archived'),kind==='acquisitions'?'true':'false');
    assert.equal(query.searchParams.has('paper'),false,'Excel is independent of paper size');
    assert.equal(downloads.at(-1),`SmartLib-${kind}.xlsx`);
    if(kind==='borrow-requests'){
      assert.equal(query.searchParams.get('status'),'CHECKED_OUT');
      assert.equal(query.searchParams.get('search'),'Two words');
    }
    const paper=kind==='acquisitions'?acquisitionPaper:queuePaper;
    for(const format of ['pdf','docx']) {
      paper.value='a4';
      await parent.querySelectorAll('button').find(button=>button.textContent===`Export ${format==='pdf'?'PDF':'Word'}`).onclick();
      const paperQuery=new URL(requests.at(-1),'http://localhost');
      assert.equal(paperQuery.searchParams.get('format'),format);
      assert.equal(paperQuery.searchParams.get('paper'),'a4');
      assert.equal(paperQuery.searchParams.get('kind'),kind);
      assert.equal(paperQuery.searchParams.get('archived'),kind==='acquisitions'?'true':'false');
    }
  }
});
