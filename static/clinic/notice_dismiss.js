document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-dismissible-notice]').forEach((notice) => {
    const key = `clinic-notice-dismissed:${notice.dataset.dismissibleNotice}`;
    const target = notice.closest('.home-telegram-connect-banner') || notice;
    try {
      target.hidden = sessionStorage.getItem(key) === '1';
    } catch {
      // The close button still works if session storage is unavailable.
    }
    notice.querySelector('[data-notice-dismiss]')?.addEventListener('click', () => {
      target.hidden = true;
      try {
        sessionStorage.setItem(key, '1');
      } catch {
        // Keep the notice closed until this page is reloaded.
      }
    });
  });
});
