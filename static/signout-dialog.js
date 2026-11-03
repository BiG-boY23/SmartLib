(() => {
  document.addEventListener('DOMContentLoaded', () => {
    if (!location.pathname.endsWith('/login.html') || sessionStorage.getItem('smartlib_logout_notice') !== 'signed-out') return;
    sessionStorage.removeItem('smartlib_logout_notice');
    const notice = document.createElement('dialog');
    notice.setAttribute('aria-labelledby', 'logoutCompleteTitle');
    notice.style.cssText = 'width:min(420px,calc(100% - 32px));padding:28px;border:1px solid #d8b366;border-radius:18px;background:#fff;color:#252129;margin:auto;';
    notice.innerHTML = '<h2 id="logoutCompleteTitle">Signed out successfully</h2><p>You have signed out on this device.</p><button type="button" autofocus style="padding:10px 24px;background:#730d24;color:white;border:0;border-radius:8px">OK</button>';
    notice.querySelector('button').onclick=()=>notice.close();
    notice.onclose=()=>notice.remove();
    document.body.append(notice);notice.showModal();
  });
  window.confirmSignOut = function (onConfirm) {
    if (document.getElementById('signoutDialog')) return;
    const dialog = document.createElement('dialog');
    dialog.id = 'signoutDialog';
    dialog.setAttribute('aria-labelledby', 'signoutTitle');
    dialog.style.cssText = 'width:min(420px,calc(100% - 32px));padding:28px;border:1px solid #d8b366;border-radius:18px;background:#fff;color:#252129;margin:auto;';
    dialog.innerHTML = '<h2 id="signoutTitle" style="margin-top:0">Sign out?</h2><p>You will need to sign in again to access your account.</p><div style="display:flex;justify-content:flex-end;gap:12px;margin-top:24px"><button type="button" data-cancel autofocus>Stay signed in</button><button type="button" data-confirm>Sign out</button></div>';
    dialog.querySelectorAll('button').forEach(button => { button.style.cssText='padding:10px 16px;border:1px solid #730d24;border-radius:8px;cursor:pointer;'; });
    const confirm=dialog.querySelector('[data-confirm]');confirm.style.background='#730d24';confirm.style.color='#fff';
    dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();
    confirm.onclick=()=>{dialog.close();onConfirm();};
    dialog.onclose=()=>dialog.remove();
    document.body.append(dialog);dialog.showModal();
  };
})();
