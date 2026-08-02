(() => {
  const pad = (value) => String(value).padStart(2, '0');

  const parseDate = (value) => {
    const parts = String(value || '').split('-').map(Number);
    if (parts.length !== 3 || parts.some(Number.isNaN)) {
      return null;
    }
    return new Date(parts[0], parts[1] - 1, parts[2]);
  };

  const formatInputDate = (date) => (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
  );

  const formatVisibleDate = (date) => (
    `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`
  );

  const mondayWeekday = (date) => (date.getDay() + 6) % 7;
  const sameDate = (left, right) => (
    left && right && formatInputDate(left) === formatInputDate(right)
  );

  const setupPicker = (picker) => {
    const input = picker.querySelector('[data-working-date-input]');
    const trigger = picker.querySelector('[data-working-date-trigger]');
    const visibleValue = picker.querySelector('[data-working-date-value]');
    const popover = picker.querySelector('[data-working-date-popover]');
    const monthLabel = picker.querySelector('[data-working-date-month]');
    const grid = picker.querySelector('[data-working-date-grid]');
    const previousButton = picker.querySelector('[data-working-date-prev]');
    const nextButton = picker.querySelector('[data-working-date-next]');
    const minimumDate = parseDate(picker.dataset.minDate);
    const selectedDate = parseDate(input.value);
    const workingWeekdays = new Set(
      (picker.dataset.workingWeekdays || '')
        .split(',')
        .filter(Boolean)
        .map(Number),
    );
    const monthFormatter = new Intl.DateTimeFormat('uk-UA', {
      month: 'long',
      year: 'numeric',
    });
    let displayedMonth = new Date(
      (selectedDate || minimumDate || new Date()).getFullYear(),
      (selectedDate || minimumDate || new Date()).getMonth(),
      1,
    );

    const setOpen = (isOpen) => {
      popover.hidden = !isOpen;
      trigger.setAttribute('aria-expanded', String(isOpen));
    };

    const render = () => {
      const currentSelection = parseDate(input.value);
      const firstDay = new Date(displayedMonth.getFullYear(), displayedMonth.getMonth(), 1);
      const lastDay = new Date(displayedMonth.getFullYear(), displayedMonth.getMonth() + 1, 0);
      monthLabel.textContent = monthFormatter.format(firstDay);
      grid.replaceChildren();

      for (let index = 0; index < mondayWeekday(firstDay); index += 1) {
        const spacer = document.createElement('span');
        spacer.className = 'working-date-spacer';
        grid.append(spacer);
      }

      for (let day = 1; day <= lastDay.getDate(); day += 1) {
        const date = new Date(displayedMonth.getFullYear(), displayedMonth.getMonth(), day);
        const button = document.createElement('button');
        const isTooEarly = minimumDate && date < minimumDate;
        const isWorkingDay = workingWeekdays.has(mondayWeekday(date));
        button.type = 'button';
        button.className = 'working-date-day';
        button.textContent = String(day);
        button.disabled = isTooEarly || !isWorkingDay;
        button.setAttribute('aria-label', formatVisibleDate(date));

        if (sameDate(date, currentSelection)) {
          button.classList.add('is-selected');
          button.setAttribute('aria-current', 'date');
        }
        if (sameDate(date, new Date())) {
          button.classList.add('is-today');
        }

        button.addEventListener('click', () => {
          input.value = formatInputDate(date);
          visibleValue.textContent = formatVisibleDate(date);
          setOpen(false);
          input.dispatchEvent(new Event('change', { bubbles: true }));
        });
        grid.append(button);
      }

      const previousMonthEnd = new Date(
        displayedMonth.getFullYear(),
        displayedMonth.getMonth(),
        0,
      );
      previousButton.disabled = Boolean(minimumDate && previousMonthEnd < minimumDate);
    };

    trigger.hidden = false;
    picker.classList.add('is-enhanced');
    visibleValue.textContent = selectedDate ? formatVisibleDate(selectedDate) : 'Оберіть дату';

    trigger.addEventListener('click', () => {
      const willOpen = popover.hidden;
      setOpen(willOpen);
      if (willOpen) {
        render();
      }
    });
    previousButton.addEventListener('click', () => {
      displayedMonth = new Date(displayedMonth.getFullYear(), displayedMonth.getMonth() - 1, 1);
      render();
    });
    nextButton.addEventListener('click', () => {
      displayedMonth = new Date(displayedMonth.getFullYear(), displayedMonth.getMonth() + 1, 1);
      render();
    });
    document.addEventListener('click', (event) => {
      if (!picker.contains(event.target)) {
        setOpen(false);
      }
    });
    picker.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        setOpen(false);
        trigger.focus();
      }
    });
  };

  document.querySelectorAll('[data-working-date-picker]').forEach(setupPicker);
})();
