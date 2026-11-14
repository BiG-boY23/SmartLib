/* Staff-only library entry register and attendance reports. */
(() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const headers = () => ({'Content-Type':'application/json'});
  async function request(url, options={}) {
    const response=await fetch(url,{...options,headers:headers()});
    if(!response.ok) { const data=await response.json(); throw new Error(data.detail || 'Unable to complete attendance request.'); }
    return response;
  }
  document.addEventListener('DOMContentLoaded', () => {
    const nav = document.querySelector('.nav-item-btn');
    const existing = document.querySelector('.screen-panel');
    if(!nav || !existing) return;
    const button=document.createElement('button'); button.className=nav.className.replace('active','');
    button.innerHTML='<i class="fa-solid fa-door-open" aria-hidden="true"></i> Library Attendance'; button.id='navAttendance'; nav.parentElement.appendChild(button);
    const panel=document.createElement('div'); panel.className='screen-panel'; panel.id='panelAttendance';
    panel.innerHTML=`<div class="attendance-layout">
      <section class="attendance-card attendance-entry" aria-labelledby="attendanceEntryTitle">
        <div><span class="attendance-eyebrow">Library entry register</span><h2 id="attendanceEntryTitle">Welcome your next visitor</h2><p>Scan a Library ID or enter an institutional ID.<br>Repeat scans within 30 seconds count as one entry.</p></div>
        <form id="attendanceEntry"><label for="attendanceId">Library ID / institutional ID</label><div class="attendance-input-row"><input id="attendanceId" placeholder="Scan or enter ID" maxlength="300" required autocomplete="off"><button class="btn btn-maroon" type="submit">Record entry</button></div><p id="attendanceMessage" role="status" aria-live="polite"></p></form>
      </section>
      <div class="attendance-stats" aria-label="Selected period totals"><div class="attendance-card attendance-stat"><strong id="attendanceVisits">—</strong><span>Total visits</span></div><div class="attendance-card attendance-stat"><strong id="attendancePeople">—</strong><span>Unique visitors</span></div></div>
      <section class="attendance-card" aria-labelledby="attendanceReportTitle">
        <div class="attendance-report-head"><div><h2 id="attendanceReportTitle">Attendance history</h2><p>Entry times are shown in Philippine time.</p></div>
          <div class="attendance-export"><select id="attendanceFormat" aria-label="Export format"><option value="xlsx">Excel</option><option value="csv">CSV</option><option value="pdf">PDF</option><option value="docx">Word</option></select><button class="btn btn-outline" id="attendanceExport">Export report</button></div>
        </div>
        <form class="attendance-filters" id="attendanceFilters"><div><label for="attendanceStart">From</label><input type="date" id="attendanceStart" required></div><div><label for="attendanceEnd">To</label><input type="date" id="attendanceEnd" required></div><div><label for="attendanceDepartment">Department</label><select id="attendanceDepartment"><option value="">All departments</option></select></div><div><label for="attendanceCourse">Course</label><select id="attendanceCourse"><option value="">All courses</option></select></div><div><label for="attendanceSort">Sort by</label><select id="attendanceSort"><option value="date">Entry time</option><option value="department">Department</option><option value="course">Course</option></select></div><div><label for="attendanceDirection">Order</label><select id="attendanceDirection"><option value="desc">Descending</option><option value="asc">Ascending</option></select></div><button class="btn btn-maroon" id="attendanceGenerate">Apply filters</button><button class="btn btn-outline" id="attendanceToday" type="button">Today</button></form>
        <p id="attendanceSummary" role="status" aria-live="polite"></p><div class="attendance-table-wrap"><table aria-label="Library attendance records"><thead id="attendanceHead"></thead><tbody id="attendanceRows"></tbody></table></div>
      </section></div>`;
    existing.parentElement.appendChild(panel);
    const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Manila'}).format(new Date());
    document.getElementById('attendanceStart').value=today;document.getElementById('attendanceEnd').value=today;
    const message=document.getElementById('attendanceMessage');
    let reportVersion=0;
    let options={departments:[],courses:[]};
    function refreshCourses() {
      const department=document.getElementById('attendanceDepartment').value;
      const course=document.getElementById('attendanceCourse');
      const previous=course.value;
      const names=[...new Set(options.courses.filter(item=>!department||item.department===department).map(item=>item.name))].sort((a,b)=>a.localeCompare(b));
      course.innerHTML='<option value="">All courses</option>'+names.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');
      course.value=names.includes(previous)?previous:'';
    }
    request('/admin/reports/options').then(response=>response.json()).then(data=>{
      options=data;
      document.getElementById('attendanceDepartment').innerHTML='<option value="">All departments</option>'+options.departments.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');
      refreshCourses();
    }).catch(error=>{document.getElementById('attendanceSummary').textContent=error.message;document.getElementById('attendanceSummary').dataset.error='true';});
    document.getElementById('attendanceDepartment').onchange=refreshCourses;
    async function report(format='json') {
      const version = format==='json' ? ++reportVersion : reportVersion;
      const status=document.getElementById('attendanceSummary');
      status.dataset.error='false';
      const start=document.getElementById('attendanceStart').value, end=document.getElementById('attendanceEnd').value;
      if(!start || !end || start>end){status.textContent='Choose a valid start and end date.';status.dataset.error='true';return;}
      const action=document.getElementById(format==='json'?'attendanceGenerate':'attendanceExport');action.disabled=true;
      if(format==='json')status.textContent='Loading attendance…';
      try {
        const query=new URLSearchParams({start:document.getElementById('attendanceStart').value,end:document.getElementById('attendanceEnd').value,format,sort:document.getElementById('attendanceSort').value,direction:document.getElementById('attendanceDirection').value});
        for(const [key,id] of Object.entries({department:'attendanceDepartment',course:'attendanceCourse'})){const value=document.getElementById(id).value;if(value)query.set(key,value);}
        const response=await request('/admin/reports/attendance?'+query);
        if(format!=='json') {const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='library-attendance.'+format;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return;}
        const data=await response.json();if(version!==reportVersion)return;
        document.getElementById('attendanceVisits').textContent=data.visits;document.getElementById('attendancePeople').textContent=data.unique_visitors;
        status.textContent=`${data.visits} ${data.visits===1?'visit':'visits'} · ${data.unique_visitors} unique ${data.unique_visitors===1?'visitor':'visitors'}`;
        document.getElementById('attendanceHead').innerHTML='<tr>'+data.columns.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr>';
        document.getElementById('attendanceRows').innerHTML=data.rows.map(r=>'<tr>'+r.map((c,i)=>`<td>${esc(i===0 && !Number.isNaN(Date.parse(c)) ? new Date(c).toLocaleString('en-PH',{timeZone:'Asia/Manila',dateStyle:'medium',timeStyle:'short'}) : c)}</td>`).join('')+'</tr>').join('') || '<tr><td colspan="6" class="attendance-empty">No entries for these dates. Record a visit or choose another period.</td></tr>';
      } catch(error) { if(version===reportVersion){status.textContent=error.message;status.dataset.error='true';if(format==='json'){document.getElementById('attendanceVisits').textContent='—';document.getElementById('attendancePeople').textContent='—';document.getElementById('attendanceRows').innerHTML='<tr><td colspan="6" class="attendance-empty">Unable to load records. Please retry.</td></tr>';}} } finally {if(version===reportVersion || format!=='json')action.disabled=false;}
    }
    button.onclick=()=>{document.querySelectorAll('.screen-panel,.nav-item-btn').forEach(e=>e.classList.remove('active'));panel.classList.add('active');button.classList.add('active');document.getElementById('pageTitle').textContent='Library Attendance';document.getElementById('pageSubTitle').textContent='Entry register and reports · Philippine time';report();};
    document.getElementById('attendanceEntry').onsubmit=async event=>{event.preventDefault();message.dataset.error='false';const submit=event.target.querySelector('button');submit.disabled=true;try{const data=await (await request('/admin/attendance',{method:'POST',body:JSON.stringify({identifier:document.getElementById('attendanceId').value})})).json();message.textContent=(data.duplicate?'Entry already recorded for ':'Entry recorded for ')+data.name;document.getElementById('attendanceId').value='';document.getElementById('attendanceId').focus();await report();}catch(error){message.textContent=error.message;message.dataset.error='true';}finally{submit.disabled=false;}};
    document.getElementById('attendanceFilters').onsubmit=event=>{event.preventDefault();report();};
    document.getElementById('attendanceToday').onclick=()=>{const date=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Manila'}).format(new Date());document.getElementById('attendanceStart').value=date;document.getElementById('attendanceEnd').value=date;report();};
    document.getElementById('attendanceExport').onclick=()=>report(document.getElementById('attendanceFormat').value);
  });
})();
