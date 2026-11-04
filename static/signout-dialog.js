(() => {
  const style = document.createElement('style');
  style.textContent = `
    dialog.session-dialog { box-sizing:border-box; position:fixed; inset:0; width:min(440px,calc(100% - 32px)); max-height:calc(100dvh - 32px); overflow:auto; margin:auto; padding:32px; border:1px solid #343b49; border-radius:20px; background:#171c27; color:#f4f5f7; text-align:center; font-family:inherit; box-shadow:0 24px 80px #0008; }
    dialog.session-dialog::backdrop { background:rgba(3,7,13,.72); backdrop-filter:blur(4px); }
    .session-dialog h2 { margin:0 0 12px; font-size:23px; line-height:1.3; color:#f4f5f7; }
    .session-dialog p { margin:0; font-size:14px; line-height:1.65; color:#aeb9ca; }
    .session-dialog .session-symbol { display:grid; place-items:center; width:56px; height:56px; margin:0 auto 20px; border:1px solid #62532c; border-radius:50%; background:#d8b36612; color:#e2bf6e; font-size:28px; }
    .session-dialog .session-actions { display:flex; justify-content:center; flex-wrap:wrap; gap:12px; margin-top:26px; }
    .session-dialog button { min-height:44px; padding:11px 22px; border:1px solid #465164; border-radius:10px; background:transparent; color:#f4f5f7; font:inherit; font-size:14px; font-weight:600; cursor:pointer; }
    .session-dialog button.session-primary { background:#840f2a; border-color:#ab2948; min-width:120px; }
    .session-dialog button:hover { filter:brightness(1.2); }
    .session-dialog button:focus-visible { outline:2px solid #e2bf6e; outline-offset:4px; }
    @media(max-width:480px) { dialog.session-dialog {padding:26px 20px;} .session-dialog h2 {font-size:21px;} }
  `;
  document.head.append(style);
  document.addEventListener('DOMContentLoaded', () => {
    if (!location.pathname.endsWith('/login.html') || sessionStorage.getItem('smartlib_logout_notice') !== 'signed-out') return;
    sessionStorage.removeItem('smartlib_logout_notice');
    const notice = document.createElement('dialog');
    notice.setAttribute('aria-labelledby', 'logoutCompleteTitle');
    notice.className = 'session-dialog';
    notice.innerHTML = '<div class="session-symbol" aria-hidden="true">✓</div><h2 id="logoutCompleteTitle">Signed out successfully</h2><p>You have signed out on this device.<br>You can sign in again whenever you’re ready.</p><div class="session-actions"><button class="session-primary" type="button" autofocus>OK</button></div>';
    notice.querySelector('button').onclick=()=>notice.close();
    notice.onclose=()=>notice.remove();
    document.body.append(notice);notice.showModal();
  });
  window.confirmSignOut = function (onConfirm) {
    if (document.getElementById('signoutDialog')) return;
    const dialog = document.createElement('dialog');
    dialog.id = 'signoutDialog';
    dialog.setAttribute('aria-labelledby', 'signoutTitle');
    dialog.className = 'session-dialog';
    dialog.innerHTML = '<div class="session-symbol" aria-hidden="true">↗</div><h2 id="signoutTitle">Sign out?</h2><p>You will need to sign in again to access your account.</p><div class="session-actions"><button type="button" data-cancel autofocus>Stay signed in</button><button class="session-primary" type="button" data-confirm>Sign out</button></div>';
    const confirm=dialog.querySelector('[data-confirm]');
    dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();
    confirm.onclick=()=>{dialog.close();onConfirm();};
    dialog.onclose=()=>dialog.remove();
    document.body.append(dialog);dialog.showModal();
  };
})();
