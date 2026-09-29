(() => {
  const returnLink = document.querySelector('[data-booking-return]');
  if (returnLink) {
    try {
      const referrer = new URL(document.referrer);
      if (referrer.origin === window.location.origin && referrer.pathname === returnLink.dataset.bookingPath) {
        returnLink.addEventListener('click', (event) => {
          event.preventDefault();
          window.history.back();
        });
      }
    } catch (_) {}
    return;
  }

  const form = document.querySelector('[data-booking-draft]');
  if (!form) return;

  const params = new URLSearchParams(window.location.search);
  const draftKey = [
    'clinic-booking-draft',
    params.get('doctor') || '',
    params.get('date') || '',
    params.get('time') || '',
  ].join(':');
  const fields = [...form.querySelectorAll('[name]')].filter((field) =>
    !['submit', 'button', 'file', 'search', 'password'].includes(field.type) &&
    field.name !== 'csrfmiddlewaretoken'
  );
  const clearDraft = () => {
    try {
      sessionStorage.removeItem(draftKey);
    } catch (_) {}
  };

  const saveDraft = () => {
    const values = {};
    fields.forEach((field) => {
      if (field.type === 'checkbox' || field.type === 'radio') {
        values[field.name] ??= { checked: [] };
        if (field.checked) values[field.name].checked.push(field.value);
      } else {
        values[field.name] = { value: field.value };
      }
    });
    try {
      sessionStorage.setItem(draftKey, JSON.stringify(values));
    } catch (_) {}
  };

  try {
    const draft = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
    if (draft) {
      fields.forEach((field) => {
        const value = draft[field.name];
        if (!value) return;
        if (field.type === 'checkbox' || field.type === 'radio') {
          field.checked = value.checked.includes(field.value);
          field.dispatchEvent(new Event('change', { bubbles: true }));
        } else if (field.type !== 'hidden') {
          field.value = value.value;
        }
      });
      sessionStorage.removeItem(draftKey);
    }
  } catch (_) {}

  fields.forEach((field) => {
    field.addEventListener('input', saveDraft);
    field.addEventListener('change', saveDraft);
  });
  document.querySelector('[data-doctor-details]')?.addEventListener('click', saveDraft);
  form.addEventListener('submit', clearDraft);
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) clearDraft();
  });
})();
