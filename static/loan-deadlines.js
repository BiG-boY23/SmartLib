// Stored naive timestamps are UTC; display deadlines in the library's timezone.
function loanDueTime(value) {
  if (!value) return NaN;
  return Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : value + 'Z');
}
function loanDeadlineCell(record) {
  const time = loanDueTime(record.due_date);
  const active = ['CHECKED_OUT', 'OVERDUE'].includes(record.status);
  if (!Number.isFinite(time)) return active ? '<strong style="color:#f3ae55">Due date missing — review loan</strong>' : '—';
  const date = new Intl.DateTimeFormat('en-PH', {timeZone:'Asia/Manila',year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}).format(time);
  if (!active) return date + ' (PHT)';
  const difference = time - Date.now();
  const minutes = Math.max(1, Math.ceil(Math.abs(difference) / 60000));
  const duration = `${Math.floor(minutes / 1440)}d ${Math.floor(minutes % 1440 / 60)}h ${minutes % 60}m`;
  return `${date} (PHT)<br><strong style="color:${difference <= 0 ? '#ff8585' : '#e5c477'}">${difference <= 0 ? 'Overdue by ' : 'Due in '}${duration}</strong>`;
}
setInterval(() => {
  const table = document.getElementById('borrowQueueTable');
  if (table && table.getClientRects().length && typeof renderBorrowQueue === 'function') renderBorrowQueue();
}, 30000);
