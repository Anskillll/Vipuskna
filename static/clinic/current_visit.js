document.addEventListener('DOMContentLoaded', () => {
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
