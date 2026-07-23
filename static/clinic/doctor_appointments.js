(() => {
  document.querySelectorAll('[data-appointment-day]').forEach((day) => {
    const toggle = day.querySelector('[data-appointment-day-toggle]');
    if (!toggle) {
      return;
    }

    toggle.addEventListener('click', () => {
      const expanded = toggle.getAttribute('aria-expanded') !== 'true';
      day.classList.toggle('is-expanded', expanded);
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.textContent = expanded
        ? toggle.dataset.collapseLabel
        : toggle.dataset.expandLabel;
    });
  });
})();
