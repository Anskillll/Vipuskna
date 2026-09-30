(() => {
  const form = document.querySelector('.clinic-filters');
  if (!form) return;
  const search = form.querySelector('[name="q"]');
  const focusKey = `clinic-filter-focus:${location.pathname}:${form.querySelector('[name="tab"]')?.value || ''}`;
  let timer;

  if (search && sessionStorage.getItem(focusKey)) {
    const position = Number(sessionStorage.getItem(focusKey));
    sessionStorage.removeItem(focusKey);
    search.focus();
    search.setSelectionRange(position, position);
  }
  search?.addEventListener('input', () => {
    sessionStorage.setItem(focusKey, String(search.selectionStart));
    clearTimeout(timer);
    timer = setTimeout(() => form.requestSubmit(), 450);
  });
  form.querySelectorAll('select, input[type="date"]').forEach((control) => {
    control.addEventListener('change', () => form.requestSubmit());
  });
  form.querySelector('[data-clear-clinic-filter]')?.addEventListener('click', () => {
    if (!search?.value) return;
    search.value = '';
    sessionStorage.removeItem(focusKey);
    form.requestSubmit();
  });
})();
