(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const timestamp = value => {
    if (!value) return 'Not recorded';
    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? 'Not recorded' : new Intl.DateTimeFormat('en-PH', {timeZone:'Asia/Manila',year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}).format(date) + ' PHT';
  };
  const money = amount => `₱${Number(amount || 0).toFixed(2)}`;
  let base, headers, toast, rows = [];

  async function load(apiBase, getHeaders, notify) {
    base = apiBase; headers = getHeaders; toast = notify;
    const root = document.getElementById('staffFinesRoot');
    root.innerHTML = '<div class="card"><div class="card-body">Loading returned items and fines…</div></div>';
    try {
      const response = await fetch(`${base}/admin/fines`, {headers:headers()});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not load fines.');
      rows = data;
      render();
    } catch (error) { root.innerHTML = `<div class="card"><div class="card-body" role="alert">${escape(error.message)}</div></div>`; }
  }

  function render() {
    const root = document.getElementById('staffFinesRoot');
    const fineRows = rows.filter(row => row.id != null);
    const outstanding = fineRows.reduce((sum,row) => sum + Number(row.balance || 0),0);
    const paid = fineRows.reduce((sum,row) => sum + Number(row.amount_paid || 0),0);
    root.innerHTML = `<div class="card"><div class="card-head"><h3>Returned items & fines</h3><button class="btn btn-outline btn-sm" id="refreshFines">Refresh</button></div><div class="card-body"><p>Record each installment received. A fine is paid when its remaining balance reaches zero.</p><div style="display:flex;gap:16px;flex-wrap:wrap;margin:14px 0"><strong>Balance: ${money(outstanding)}</strong><strong>Collected: ${money(paid)}</strong><span>${rows.length} returned item${rows.length===1?'':'s'} · ${fineRows.length} fine${fineRows.length===1?'':'s'}</span></div><div class="table-responsive"><table><thead><tr><th>Patron</th><th>Book / copy</th><th>Due date</th><th>Returned</th><th>Return result</th><th>Fine</th><th>Paid</th><th>Balance</th><th>Status</th><th>Action</th></tr></thead><tbody>${rows.length ? rows.map(row => `<tr><td>${escape(row.patron_name)}<br><small>${escape(row.patron_username)}</small></td><td>${escape(row.book_title)}<br><small>${escape(row.copy_accession_no || row.accession_no)}</small></td><td>${timestamp(row.due_date)}</td><td>${timestamp(row.return_date)}</td><td><span class="badge ${row.returned_late?'badge-danger':'badge-success'}">${row.returned_late?'Returned late':'On time'}</span></td><td>${money(row.amount)}</td><td>${row.id == null ? '—' : money(row.amount_paid)}</td><td>${row.id == null ? money(0) : money(row.balance)}</td><td>${row.id == null ? '<span class="badge badge-success">No fine</span>' : `<span class="badge ${row.payment_status==='paid'?'badge-success':row.payment_status==='partial'?'badge-warning':'badge-warning'}">${row.payment_status==='paid'?'Paid':row.payment_status==='partial'?'Partially paid':'Pending'}</span>`}</td><td>${row.id == null ? '—' : `<button class="btn btn-sm ${row.payment_status==='paid'?'btn-outline':'btn-maroon'}" data-payment-id="${row.id}">${row.payment_status==='paid'?'View payments':'Record payment'}</button>`}</td></tr>`).join('') : '<tr><td colspan="10">No returned items recorded.</td></tr>'}</tbody></table></div></div></div>`;
    document.getElementById('refreshFines').onclick = () => load(base,headers,toast);
    root.querySelectorAll('[data-payment-id]').forEach(button => button.onclick = () => openPaymentModal(Number(button.dataset.paymentId)));
  }

  function openPaymentModal(id) {
    const row = rows.find(item => item.id === id);
    if (!row) return;
    document.getElementById('finePaymentModal')?.remove();
    const payments = row.payments || [];
    const overlay = document.createElement('div');
    overlay.id = 'finePaymentModal';
    overlay.innerHTML = `<div style="position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.68);display:grid;place-items:center;padding:18px" data-close-overlay><section role="dialog" aria-modal="true" aria-labelledby="finePaymentTitle" style="width:min(520px,100%);max-height:90vh;overflow:auto;background:#171b27;color:#f5f5f5;border:1px solid #30384a;border-radius:16px;padding:22px;box-shadow:0 18px 60px #0008"><div style="display:flex;justify-content:space-between;gap:12px;align-items:start"><div><h2 id="finePaymentTitle" style="margin:0 0 6px">Record fine payment</h2><p style="margin:0;color:#a7b0c2">${escape(row.patron_name)} · ${escape(row.book_title)}</p></div><button type="button" class="btn btn-outline btn-sm" data-close>Close</button></div><div style="display:flex;gap:16px;flex-wrap:wrap;margin:18px 0"><span>Fine: <b>${money(row.amount)}</b></span><span>Paid: <b>${money(row.amount_paid)}</b></span><span>Balance: <b>${money(row.balance)}</b></span></div>${row.balance > 0 ? `<form id="finePaymentForm"><label for="finePaymentAmount">Amount received</label><div style="display:flex;gap:10px;margin-top:7px"><input id="finePaymentAmount" name="amount" type="number" min="0.01" max="${Number(row.balance).toFixed(2)}" step="0.01" required inputmode="decimal" style="flex:1;min-width:0;padding:11px;border:1px solid #39445a;border-radius:9px;background:#0d1018;color:#fff"><button class="btn btn-maroon" type="submit">Save payment</button></div><small style="display:block;margin-top:7px;color:#a7b0c2">Maximum remaining balance: ${money(row.balance)}</small><p id="finePaymentError" role="alert" style="color:#ff9292"></p></form>` : '<p class="badge badge-success">This fine is fully paid.</p>'}<h3 style="margin:20px 0 10px">Payment history</h3>${payments.length ? `<div style="display:grid;gap:8px">${payments.map(payment => `<div style="padding:10px;border:1px solid #30384a;border-radius:9px">${money(payment.amount)} <small style="color:#a7b0c2">· ${timestamp(payment.recorded_at)} · recorded by ${escape(payment.actor_username)}</small></div>`).join('')}</div>` : '<p style="color:#a7b0c2">No payments recorded yet.</p>'}</section></div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('[data-close]').onclick = () => overlay.remove();
    overlay.querySelector('[data-close-overlay]').onclick = event => { if (event.target === overlay.firstElementChild) overlay.remove(); };
    const form = overlay.querySelector('#finePaymentForm');
    if (form) form.onsubmit = event => submitPayment(event, row, form);
  }

  async function submitPayment(event, row, form) {
    event.preventDefault();
    const amount = Number(new FormData(form).get('amount'));
    const errorNode = form.querySelector('#finePaymentError');
    if (!Number.isFinite(amount) || amount <= 0 || amount > Number(row.balance) + 0.000001) {
      errorNode.textContent = `Enter an amount from ₱0.01 to ${money(row.balance)}.`;
      return;
    }
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    errorNode.textContent = '';
    try {
      const response = await fetch(`${base}/admin/fines/${row.id}/payments`, {method:'POST',headers:headers(),body:JSON.stringify({amount:Number(amount.toFixed(2))})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not record payment.');
      toast(`Payment recorded. Remaining balance: ${money(data.balance)}.`, 'success');
      document.getElementById('finePaymentModal')?.remove();
      await load(base,headers,toast);
    } catch (error) {
      errorNode.textContent = error.message;
      submit.disabled = false;
    }
  }

  window.SmartLibFines = {load};
})();
