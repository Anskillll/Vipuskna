(() => {
  const normalize = (value) => value
    .toLocaleLowerCase('uk-UA')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .replace(/[^\p{Letter}\p{Number}]+/gu, ' ')
    .trim();

  document.querySelectorAll('[data-patient-picker]').forEach((root) => {
    const select = root.querySelector('select');
    const search = root.querySelector('[data-patient-picker-search]');
    const results = root.querySelector('[data-patient-picker-results]');
    const newPatientButton = root.querySelector('[data-patient-picker-new]');
    if (!select || !search || !results || !newPatientButton) return;

    const options = Array.from(select.options)
      .filter((option) => option.value)
      .map((option) => ({
        value: option.value,
        label: option.textContent.trim(),
        search: normalize(option.textContent),
      }));
    let activeIndex = -1;

    const updateContactFields = () => {
      const hideContacts = Boolean(select.value);
      ['id_first_name', 'id_last_name', 'id_phone'].forEach((id) => {
        const input = document.getElementById(id);
        const field = input ? input.closest('.field') : null;
        if (field) field.hidden = hideContacts;
      });
      newPatientButton.classList.toggle('active', !hideContacts);
    };

    const choose = (option) => {
      select.value = option ? option.value : '';
      search.value = option ? option.label : '';
      results.hidden = true;
      activeIndex = -1;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      updateContactFields();
    };

    const resultButtons = () => Array.from(results.querySelectorAll('button'));

    const setActiveResult = (index) => {
      const buttons = resultButtons();
      if (!buttons.length) return;
      activeIndex = Math.max(0, Math.min(index, buttons.length - 1));
      buttons.forEach((button, buttonIndex) => {
        button.classList.toggle('active', buttonIndex === activeIndex);
      });
      buttons[activeIndex].scrollIntoView({ block: 'nearest' });
    };

    const render = () => {
      const terms = normalize(search.value).split(/\s+/).filter(Boolean);
      const matches = options.filter((option) => (
        terms.every((term) => option.search.includes(term))
      ));
      results.replaceChildren();
      activeIndex = -1;

      matches.slice(0, 12).forEach((option) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = option.label;
        button.setAttribute('role', 'option');
        button.addEventListener('click', () => choose(option));
        results.appendChild(button);
      });

      if (!matches.length) {
        const empty = document.createElement('div');
        empty.className = 'patient-picker-empty';
        empty.textContent = 'Пацієнта не знайдено. Перевірте запит або оберіть нового пацієнта.';
        results.appendChild(empty);
      } else if (matches.length > 12) {
        const hint = document.createElement('div');
        hint.className = 'patient-picker-hint';
        hint.textContent = `Знайдено ${matches.length}. Уточніть ім’я або номер телефону.`;
        results.appendChild(hint);
      }
      results.hidden = false;
    };

    search.addEventListener('focus', render);
    search.addEventListener('input', () => {
      if (select.value) select.value = '';
      updateContactFields();
      render();
    });
    search.addEventListener('keydown', (event) => {
      if (select.value && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        search.value = '';
        select.value = '';
        updateContactFields();
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (results.hidden) render();
        setActiveResult(activeIndex + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        setActiveResult(activeIndex - 1);
      } else if (event.key === 'Enter' && activeIndex >= 0) {
        event.preventDefault();
        resultButtons()[activeIndex].click();
      } else if (event.key === 'Escape') {
        results.hidden = true;
      }
    });
    newPatientButton.addEventListener('click', () => {
      choose(null);
      results.hidden = true;
      const firstName = document.getElementById('id_first_name');
      if (firstName) firstName.focus();
    });
    document.addEventListener('click', (event) => {
      if (!root.contains(event.target)) results.hidden = true;
    });

    const selected = options.find((option) => option.value === select.value);
    if (selected) search.value = selected.label;
    root.classList.add('patient-picker-enhanced');
    updateContactFields();
  });
})();
