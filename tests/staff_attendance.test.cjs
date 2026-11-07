const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

test('attendance filters are present and sent to the preview request', async () => {
  const elements = new Map(), requests = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, {value:'', innerHTML:'', textContent:'', dataset:{}, disabled:false, classList:{add(){},remove(){}}});
    return elements.get(id);
  };
  let ready, panelMarkup = '';
  const existing = {parentElement:{appendChild(panel){panelMarkup=panel.innerHTML;}}};
  const nav = {className:'nav-item-btn active', parentElement:{appendChild(){}}};
  const context = vm.createContext({window:{}, URLSearchParams, Date, Intl,
    localStorage:{getItem:()=>''}, sessionStorage:{getItem:()=>''},
    document:{addEventListener:(_,callback)=>{ready=callback;},querySelector:selector=>selector==='.nav-item-btn'?nav:existing,
      createElement:()=>({className:'',innerHTML:''}),getElementById:el},
    fetch:async path=>{
      requests.push(path);
      if(path.includes('/options')) return {ok:true,json:async()=>({departments:['Computing'],courses:[{department:'Computing',name:'BSIT'}]})};
      return {ok:true,json:async()=>({columns:['Department','Course'],rows:[],visits:0,unique_visitors:0})};
    },
  });
  vm.runInContext(fs.readFileSync('static/staff-attendance.js','utf8'),context);
  ready();
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(panelMarkup.includes('Department'));
  assert.ok(panelMarkup.includes('Course'));
  assert.ok(panelMarkup.includes('Sort by'));
  assert.ok(el('attendanceCourse').innerHTML.includes('BSIT'));
  Object.assign(el('attendanceStart'),{value:'2026-10-01'});
  Object.assign(el('attendanceEnd'),{value:'2026-10-31'});
  Object.assign(el('attendanceDepartment'),{value:'Computing'});
  Object.assign(el('attendanceCourse'),{value:'BSIT'});
  Object.assign(el('attendanceSort'),{value:'course'});
  Object.assign(el('attendanceDirection'),{value:'asc'});
  el('attendanceFilters').onsubmit({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  const query = new URL(requests.at(-1),'http://localhost');
  assert.equal(query.pathname,'/admin/reports/attendance');
  assert.equal(query.searchParams.get('department'),'Computing');
  assert.equal(query.searchParams.get('course'),'BSIT');
  assert.equal(query.searchParams.get('sort'),'course');
  assert.equal(query.searchParams.get('direction'),'asc');
});
