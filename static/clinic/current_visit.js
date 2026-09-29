document.addEventListener('DOMContentLoaded', () => {
  const banner = document.querySelector('[data-current-visit-banner]');
  const dismissBannerButton = document.querySelector('[data-current-visit-dismiss]');

  if (banner && dismissBannerButton) {
    const storageKey = `clinic-current-visit-dismissed:${banner.dataset.currentVisitBanner}`;
    try {
      banner.hidden = sessionStorage.getItem(storageKey) === '1';
    } catch {
      // The banner can still be dismissed for this page when session storage is unavailable.
    }
    dismissBannerButton.addEventListener('click', () => {
      banner.hidden = true;
      try {
        sessionStorage.setItem(storageKey, '1');
      } catch {
        // Dismissal remains effective until the current page is reloaded.
      }
    });
  }

  const dialog = document.querySelector('[data-current-records-dialog]');
  const openButton = document.querySelector('[data-current-records-open]');
  const closeButton = document.querySelector('[data-current-records-close]');

  if (!dialog || !openButton || !closeButton) {
    return;
  }

  const closeDialog = () => {
    dialog.close();
    openButton.focus();
  };

  openButton.addEventListener('click', () => {
    dialog.showModal();
  });

  closeButton.addEventListener('click', closeDialog);

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      closeDialog();
    }
  });
});
