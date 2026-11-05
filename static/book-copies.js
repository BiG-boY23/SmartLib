window.showAddCopiesDialog = function (book, options) {
  if (!book || document.getElementById('addCopiesDialog')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'addCopiesDialog';
  dialog.setAttribute('aria-labelledby', 'addCopiesTitle');
  dialog.style.cssText = 'margin:auto;width:min(440px,calc(100% - 32px));padding:28px;border:1px solid #b99a50;border-radius:18px;background:var(--bg-card,#171c27);color:var(--text-primary,#fff)';
  dialog.innerHTML = `<form>
    <h2 id="addCopiesTitle">Add book copies</h2>
    <p class="copy-book-title"></p>
    <label for="newCopyQuantity">New copies received</label>
    <input id="newCopyQuantity" class="form-control" type="number" min="1" max="500" step="1" value="1" required autofocus>
    <p class="copy-preview" aria-live="polite"></p>
    <p>Each new copy receives a unique accession number and QR label. Existing copies remain unchanged.</p>
    <p class="copy-error" role="alert" style="color:#ef9999"></p>
    <div style="display:flex;justify-content:flex-end;gap:12px;margin-top:20px">
      <button type="button" class="btn copy-cancel">Cancel</button>
      <button type="submit" class="btn" style="background:#780d29;color:white">Confirm addition</button>
    </div>
  </form>`;
  const style = document.createElement('style');
  style.textContent = '#addCopiesDialog::backdrop{background:rgba(0,0,0,.7)}#addCopiesDialog p{margin:16px 0;line-height:1.5}';
  dialog.append(style);
  dialog.querySelector('.copy-book-title').textContent = book.title;
  const input = dialog.querySelector('input');
  const preview = dialog.querySelector('.copy-preview');
  const updatePreview = () => {
    preview.textContent = input.checkValidity()
      ? `Current total: ${book.total_copies} + ${Number(input.value)} new = ${book.total_copies + Number(input.value)} copies`
      : 'Enter a whole number from 1 to 500.';
  };
  input.addEventListener('input', updatePreview);
  updatePreview();
  let saving = false;
  dialog.querySelector('.copy-cancel').onclick = () => dialog.close();
  dialog.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
  dialog.addEventListener('close', () => dialog.remove());
  dialog.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    if (saving || !input.reportValidity()) return;
    saving = true;
    dialog.querySelectorAll('button').forEach(button => button.disabled = true);
    try {
      const response = await fetch(`${options.base}/admin/books/${book.id}/copies`, {
        method: 'POST', headers: options.headers(), body: JSON.stringify({quantity: Number(input.value)})
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(typeof error.detail === 'string' ? error.detail : 'Unable to add copies.');
      }
      const updated = await response.json();
      dialog.close();
      options.toast(`Copies added. Total: ${updated.total_copies}. Open QR Labels to print the new labels.`, 'success');
      await options.refresh();
    } catch (error) {
      dialog.querySelector('.copy-error').textContent = error.message;
    } finally {
      saving = false;
      dialog.querySelectorAll('button').forEach(button => button.disabled = false);
    }
  };
  document.body.append(dialog);
  dialog.showModal();
};
