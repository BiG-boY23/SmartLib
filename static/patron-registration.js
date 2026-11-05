function openPatronRegistration() {
  if (document.getElementById('patronRegistration')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'patronRegistration';
  dialog.style.cssText = 'margin:auto;padding:28px;width:min(520px,95vw);max-height:90vh;overflow:auto;background:#171c27;color:white;border:1px solid #c3a45c;border-radius:18px';
  dialog.setAttribute('aria-label', 'Staff-assisted patron registration');
  const fields = [['username','Student / staff ID','text'],['first_name','First name','text'],['middle_name','Middle name (optional)','text'],['last_name','Last name','text'],['email','Email','email'],['department','Department','text'],['course','Course (optional for faculty)','text'],['password','Password (8–12 characters; upper, lower, number, symbol)','password'],['confirm_password','Confirm password','password']];
  dialog.innerHTML = `<form><h2>Register student / instructor</h2><p>Verify the patron’s institutional ID in person before registering.</p><label>Account type<select name="account_type" class="form-control"><option value="student">Student</option><option value="faculty">Faculty / Instructor</option></select></label>${fields.map(([name,label,type])=>`<label style="display:block;margin-top:12px">${label}<input class="form-control" name="${name}" type="${type}" ${['middle_name','course'].includes(name)?'':'required'} autocomplete="${type==='password'?'new-password':'off'}"></label>`).join('')}<label style="display:block;margin:16px 0"><input type="checkbox" required> I verified this patron’s institutional ID.</label><p role="alert"></p><button class="btn" type="button">Cancel</button> <button class="btn btn-maroon" type="submit">Register patron</button></form>`;
  let saving = false;
  dialog.querySelector('[type=button]').onclick = () => dialog.close();
  dialog.oncancel = e => { if (saving) e.preventDefault(); };
  dialog.onclose = () => dialog.remove();
  dialog.querySelector('form').onsubmit = async e => {
    e.preventDefault(); if(saving) return;
    saving = true;
    dialog.querySelectorAll('button').forEach(b=>b.disabled=true);
    try {
      const response = await fetch(`${API_BASE}/admin/users/patrons`, {method:'POST',headers:getAuthHeaders(),body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});
      const data = await response.json();
      if (!response.ok) throw Error(typeof data.detail==='string'?data.detail:(data.detail||[]).map(x=>x.msg).join('; ')||'Registration failed.');
      dialog.close(); toast('Patron registered. Use their student / staff ID at the circulation desk.', 'success');
    } catch(error) {dialog.querySelector('[role=alert]').textContent=error.message;}
    finally {saving=false;dialog.querySelectorAll('button').forEach(b=>b.disabled=false);}
  };
  document.body.append(dialog);dialog.showModal();
}

