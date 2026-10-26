export function showMobileNotice() {
  if (!matchMedia('(max-width: 760px)').matches) return;
  try { if (sessionStorage.getItem('overpass.mobile-notice.seen')) return; } catch { /* Dismissal still works without storage. */ }
  const dialog = document.getElementById('mobile-notice') as HTMLDialogElement;
  const remember = () => {
    try { sessionStorage.setItem('overpass.mobile-notice.seen', '1'); } catch { /* No persistent storage required. */ }
  };
  const dismiss = () => { remember(); dialog.close(); };
  document.getElementById('dismiss-mobile-notice')!.onclick = dismiss;
  dialog.addEventListener('click', event => {
    const box = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) dismiss();
  });
  // Persist before native Escape dismissal, rather than in its queued close
  // event, so even an immediate reload remembers the user's choice.
  dialog.addEventListener('cancel', remember);
  dialog.showModal();
}
