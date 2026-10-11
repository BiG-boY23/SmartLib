/* Shared, authenticated reporting and suggestions UI for both staff roles. */
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const headers = () => ({Authorization: `Bearer ${localStorage.getItem('token') || sessionStorage.getItem('token') || ''}`, 'Content-Type':'application/json'});
  async function api(path, options={}) {
    const res=await fetch(path,{...options,headers:headers()});
    if(!res.ok) { const data=await res.json().catch(()=>({})); throw new Error(typeof data.detail==='string'?data.detail:'Unable to load library data. Please sign in again or retry.'); }
    return res;
  }
  const today = () => new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Manila'});
  const offsetDay = (day,n) => { const d=new Date(`${day}T12:00:00Z`); d.setUTCDate(d.getUTCDate()+n); return d.toISOString().slice(0,10); };
  const charts=new Map(); let reportOptions={departments:[],courses:[]}; let revision=0;
  function plot(canvas, daily, people=true) {
    charts.get(canvas)?.destroy();
    if(typeof Chart==='undefined') return;
    charts.set(canvas,new Chart(document.getElementById(canvas),{type:'line',data:{labels:daily.map(d=>d.date),datasets:[
      {label:people?'People borrowing':'Items borrowed',data:daily.map(d=>d[people?'borrowers':'borrowed_items']),borderColor:'#e1b842',backgroundColor:'#e1b84222',tension:0,pointRadius:3},
      {label:people?'People returning':'Items returned',data:daily.map(d=>d[people?'returners':'returned_items']),borderColor:'#36bf9a',tension:0,pointRadius:3}
    ]},options:{
      responsive:true,maintainAspectRatio:false,
      plugins:{legend:{labels:{color:'#cdd3de'}}},
      scales:{
        y:{beginAtZero:true,ticks:{precision:0,color:'#9ca9bb'},grid:{color:'#272e3c'}},
        x:{ticks:{color:'#9ca9bb',maxTicksLimit:8},grid:{display:false}}
      }
    }}));
  }
  async function loadDashboardChart(canvas, days=30) {
    const el=document.getElementById(canvas); if(!el) return;
    el.parentElement.style.height='310px';
    try { const end=today();const report=await (await api(`/admin/reports/circulation?role=all&start=${offsetDay(end,1-days)}&end=${end}`)).json();plot(canvas,report.daily,false); }
    catch(error) { window.toast?.(error.message,'danger'); }
  }
  function show(panelId, navId, title, subtitle) {
    document.querySelectorAll('.screen-panel,.nav-item-btn').forEach(e=>e.classList.remove('active'));
    document.getElementById(panelId).classList.add('active'); document.getElementById(navId).classList.add('active');
    document.getElementById('pageTitle').textContent=title;document.getElementById('pageSubTitle').textContent=subtitle;
  }
  function refreshCourses() {
    const department=document.getElementById('reportDepartment').value;
    const select=document.getElementById('reportCourse'); const previous=select.value;
    select.innerHTML='<option value="">All courses</option>'+[...new Set(reportOptions.courses.filter(c=>!department||c.department===department).map(c=>c.name))].sort().map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');
    if([...select.options].some(o=>o.value===previous))select.value=previous;
  }
  function params(format='json') {
    const values={start:'reportStart',end:'reportEnd',department:'reportDepartment',course:'reportCourse',role:'reportRole',sort:'reportSort',direction:'reportDirection'};
    const p=new URLSearchParams({format});Object.entries(values).forEach(([k,id])=>{const value=document.getElementById(id).value;if(value)p.set(k,value)});return p;
  }
  async function openReports() {
    show('panelReports','navReports','Circulation reports','Recorded borrowing and returns · Ormoc City Campus · Philippine time');
    try {reportOptions=await (await api('/admin/reports/options')).json();document.getElementById('reportDepartment').innerHTML='<option value="">All departments</option>'+reportOptions.departments.map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join('');refreshCourses();await generate();}
    catch(error){document.getElementById('reportStatus').textContent=error.message;}
  }
  async function generate() {
    const current=++revision;const status=document.getElementById('reportStatus');status.textContent='Loading recorded circulation activity…';
    document.getElementById('reportOutput').innerHTML='';
    try {
      const data=await (await api('/admin/reports/circulation?'+params())).json(); if(current!==revision)return;
      const s=data.summary;
      document.getElementById('reportOutput').innerHTML=`<div class="report-metrics">${[['People borrowing',s.unique_borrowers],['People returning',s.unique_returners],['Items borrowed',s.borrowed_items],['Items returned',s.returned_items]].map(([label,n])=>`<div class="card" style="padding:18px"><strong style="font-size:25px">${n}</strong><div>${label}</div></div>`).join('')}</div>
        <div class="card" style="padding:18px;margin-top:18px"><h3>Daily borrowing and returns</h3><div style="height:300px"><canvas id="reportChart"></canvas></div><p class="report-note">People are counted once per day per activity. Period totals count each person once across the selected dates. Pending reservations and approvals are excluded.</p></div>
        <div class="card" style="margin-top:18px;padding:18px"><h3>Daily totals</h3><div class="table-responsive"><table><thead><tr><th>Date</th><th>Borrowers</th><th>Returners</th><th>Items borrowed</th><th>Items returned</th></tr></thead><tbody>${data.daily.map(d=>`<tr><td>${d.date}</td><td>${d.borrowers}</td><td>${d.returners}</td><td>${d.borrowed_items}</td><td>${d.returned_items}</td></tr>`).join('')}</tbody></table></div></div>
        <div class="card" style="margin-top:18px;padding:18px"><h3>Activity details</h3><p class="report-note">${data.rows.length} recorded activities. ${data.rows.length>200?'Showing the first 200; exports include every matching record.':''}</p><div class="table-responsive"><table><thead><tr><th>Date / time</th><th>Name / ID</th><th>Department</th><th>Course</th><th>Book</th><th>Activity</th></tr></thead><tbody>${data.rows.slice(0,200).map(r=>`<tr><td>${esc(r.date)} ${esc(r.time)}</td><td>${esc(r.name)}<br>${esc(r.institutional_id)}</td><td>${esc(r.department)}</td><td>${esc(r.course)}</td><td>${esc(r.book)}</td><td>${esc(r.action)}</td></tr>`).join('')||'<tr><td colspan="6">No recorded activity matches the selected filters.</td></tr>'}</tbody></table></div></div>`;
      plot('reportChart',data.daily);status.textContent=`Report generated for ${data.start} to ${data.end}. All dates use Philippine time.`;
    }catch(error){if(current===revision)status.textContent=error.message;}
  }
  async function download(format) {
    const status=document.getElementById('reportStatus');status.textContent=`Preparing ${format.toUpperCase()} export…`;
    try { const response=await api('/admin/reports/circulation?'+params(format));const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=`SmartLib-circulation-${document.getElementById('reportStart').value}-${document.getElementById('reportEnd').value}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status.textContent='Export downloaded using the selected filters.'; }
    catch(error){status.textContent=error.message;}
  }
  async function openInbox() {
    show('panelStaffSuggestions','navStaffSuggestions','Suggestions & inquiries','Public feedback and book recommendations received by the library');
    const content=document.getElementById('staffSuggestionContent');content.textContent='Loading suggestions…';
    try {
      const [feedback,books]=await Promise.all([api('/admin/feedbacks').then(r=>r.json()),api('/admin/acquisitions').then(r=>r.json())]);
      content.innerHTML=`<h3>Public suggestions & inquiries</h3><div class="table-responsive"><table><thead><tr><th>Date</th><th>Sender</th><th>Email</th><th>Category</th><th>Message</th></tr></thead><tbody>${feedback.map(f=>`<tr><td>${esc(f.created_at.slice(0,10))}</td><td>${esc(f.name)}</td><td>${esc(f.email)}</td><td>${esc(f.category)}</td><td style="white-space:pre-wrap;max-width:450px;overflow-wrap:anywhere">${esc(f.message)}</td></tr>`).join('')||'<tr><td colspan="5">No public suggestions have been submitted yet.</td></tr>'}</tbody></table></div>
      <h3 style="margin-top:28px">Book recommendations</h3><div class="table-responsive"><table><thead><tr><th>Requested by</th><th>Title / author</th><th>Reason</th><th>Status</th><th>Review</th></tr></thead><tbody>${books.map(b=>`<tr><td>${esc(b.requester_name)}</td><td>${esc(b.title)}<br>${esc(b.author)}</td><td style="white-space:pre-wrap;max-width:350px">${esc(b.justification)}</td><td>${esc(b.status)}</td><td><button class="btn btn-outline btn-sm" data-id="${esc(b.acq_id)}" data-state="Approved for Purchase">Approve</button> <button class="btn btn-outline btn-sm" data-id="${esc(b.acq_id)}" data-state="Declined">Decline</button></td></tr>`).join('')||'<tr><td colspan="5">No book recommendations yet.</td></tr>'}</tbody></table></div>`;
      content.querySelectorAll('[data-id]').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;try{await api(`/admin/acquisitions/${encodeURIComponent(button.dataset.id)}/status`,{method:'PUT',body:JSON.stringify({status:button.dataset.state})});await openInbox();}catch(error){window.toast?.(error.message,'danger');button.disabled=false;}}));
    }catch(error){content.textContent=error.message;}
  }
  function install() {
    const style=document.createElement('style');style.textContent='.report-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:14px}.report-filters label{display:block;margin-bottom:6px}.report-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}.report-note{font-size:12px;color:var(--text-muted);margin:10px 0}.report-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}';document.head.appendChild(style);
    const menu=document.querySelector('.sidebar-menu');
    const button=document.createElement('button');button.id='navReports';button.className='nav-item-btn';button.innerHTML='<i class="fa-solid fa-file-lines"></i> Circulation Reports';button.onclick=openReports;menu.appendChild(button);
    const suggestions=document.getElementById('navFeedback')||document.createElement('button');suggestions.id='navStaffSuggestions';suggestions.className='nav-item-btn';suggestions.removeAttribute('onclick');suggestions.innerHTML='<i class="fa-solid fa-comments"></i> Suggestions & inquiries';suggestions.onclick=openInbox;if(!suggestions.parentElement)menu.appendChild(suggestions);
    document.querySelector('.content-container').insertAdjacentHTML('beforeend',`<div class="screen-panel" id="panelReports"><div class="card" style="padding:22px"><h3>Build a circulation report</h3><p class="report-note">For a single date, set both dates to the same day. Filters apply to preview and downloads. Department and course use the member's recorded profile.</p><form id="reportForm"><div class="report-filters">
      <div><label>From date</label><input type="date" id="reportStart" class="form-control" required></div><div><label>To date</label><input type="date" id="reportEnd" class="form-control" required></div>
      <div><label>Department</label><select id="reportDepartment" class="form-select"><option value="">All departments</option></select></div><div><label>Course</label><select id="reportCourse" class="form-select"><option value="">All courses</option></select></div>
      <div><label>Audience</label><select id="reportRole" class="form-select"><option value="student">Students</option><option value="faculty">Faculty</option><option value="all">All members</option></select></div>
      <div><label>Sort details by</label><select id="reportSort" class="form-select"><option value="date">Date</option><option value="department">Department</option><option value="course">Course</option></select></div><div><label>Order</label><select id="reportDirection" class="form-select"><option value="asc">Ascending</option><option value="desc">Descending</option></select></div></div>
      <div class="report-actions"><button class="btn btn-gold" type="submit">Generate report</button><button class="btn btn-outline" type="button" id="reportToday">Today</button>${['csv','pdf','docx'].map(f=>`<button class="btn btn-outline" type="button" data-export="${f}">Export ${f==='docx'?'Word':f.toUpperCase()}</button>`).join('')}</div></form><p id="reportStatus" role="status" class="report-note"></p></div><div id="reportOutput" style="margin-top:18px"></div></div>
      <div class="screen-panel" id="panelStaffSuggestions"><div class="card" style="padding:22px"><button class="btn btn-outline btn-sm" id="refreshSuggestions" style="float:right">Refresh</button><div id="staffSuggestionContent"></div></div></div>`);
    document.getElementById('reportEnd').value=today();document.getElementById('reportStart').value=offsetDay(today(),-29);
    document.getElementById('reportDepartment').onchange=refreshCourses;document.getElementById('reportForm').onsubmit=e=>{e.preventDefault();generate()};
    document.getElementById('reportToday').onclick=()=>{document.getElementById('reportStart').value=today();document.getElementById('reportEnd').value=today();generate()};
    document.querySelectorAll('[data-export]').forEach(b=>b.onclick=()=>download(b.dataset.export));document.getElementById('refreshSuggestions').onclick=openInbox;
  }
  window.SmartLibStaff={loadDashboardChart,openReports,openInbox};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
