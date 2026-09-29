(() => {
  const picker = document.querySelector('[data-reschedule-time-picker]');
  if (!picker) return;

  const form = picker.closest('form');
  const date = form.querySelector('[data-working-date-input]');
  const duration = form.querySelector('[name="duration_minutes"]');
  const input = picker.querySelector('[name="time"]');
  const trigger = picker.querySelector('[data-reschedule-time-trigger]');
  const value = picker.querySelector('[data-reschedule-time-value]');
  const options = picker.querySelector('[data-reschedule-time-options]');
  const status = picker.querySelector('[data-reschedule-time-status]');
  if (!date || !duration || !input) return;

  let requestNumber = 0;
  input.required = false;
  picker.classList.add('is-enhanced');
  trigger.hidden = false;

  const close = () => {
    options.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
  };

  const select = (time) => {
    input.value = time;
    value.textContent = time || 'Оберіть час';
    options.querySelectorAll('button').forEach((button) => {
      button.classList.toggle('active', button.dataset.time === time);
    });
  };

  const refresh = async ({ clear = false, adjustDuration = false } = {}) => {
    const currentRequest = ++requestNumber;
    close();
    if (clear) select('');
    trigger.disabled = true;
    options.replaceChildren();
    if (!date.value) {
      status.textContent = 'Спочатку оберіть дату.';
      return;
    }
    status.textContent = 'Шукаємо вільний час…';

    const url = new URL(picker.dataset.slotsUrl, window.location.href);
    url.searchParams.set('date', date.value);
    url.searchParams.set('duration_minutes', duration.value);
    try {
      const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('Unable to load times');
      const data = await response.json();
      if (currentRequest !== requestNumber) return;

      if (data.slot_minutes) {
        duration.min = data.slot_minutes;
        duration.step = data.slot_minutes;
        if (adjustDuration && !data.duration_valid) {
          duration.value = data.slot_minutes;
          refresh();
          return;
        }
      }

      const times = data.times || [];
      select(times.includes(input.value.slice(0, 5)) ? input.value.slice(0, 5) : '');
      times.forEach((time) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'time-pill';
        button.dataset.time = time;
        button.textContent = time;
        button.setAttribute('aria-label', `Обрати ${time}`);
        if (input.value.slice(0, 5) === time) button.classList.add('active');
        button.addEventListener('click', () => {
          select(time);
          close();
          trigger.focus();
        });
        options.append(button);
      });
      trigger.disabled = times.length === 0;
      status.textContent = !data.duration_valid && data.slot_minutes
        ? `Тривалість має бути кратною ${data.slot_minutes} хв.`
        : times.length ? `Доступно ${times.length} варіантів часу.` : 'На цей день вільного часу немає.';
    } catch {
      if (currentRequest === requestNumber) {
        status.textContent = 'Не вдалося завантажити час. Змініть дату або тривалість і спробуйте ще раз.';
      }
    }
  };

  trigger.addEventListener('click', () => {
    options.hidden = !options.hidden;
    trigger.setAttribute('aria-expanded', String(!options.hidden));
  });
  date.addEventListener('change', () => refresh({ clear: true, adjustDuration: true }));
  duration.addEventListener('change', () => refresh({ clear: true }));
  document.addEventListener('click', (event) => {
    if (!picker.contains(event.target)) close();
  });
  picker.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      close();
      trigger.focus();
    }
  });
  form.addEventListener('submit', (event) => {
    if (!input.value) {
      event.preventDefault();
      status.textContent = 'Оберіть доступний час.';
      trigger.focus();
    }
  });

  refresh();
})();
