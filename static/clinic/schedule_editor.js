document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-schedule-editor]').forEach((form) => {
    const startInput = form.querySelector('[name="start_time"]');
    const endInput = form.querySelector('[name="end_time"]');
    const slotInput = form.querySelector('[name="slot_minutes"]');
    const workingInput = form.querySelector('[name="is_working"]');
    const options = form.querySelector('#id_break_slots');
    const emptyMessage = form.querySelector('[data-break-slot-empty]');
    const field = form.querySelector('[data-break-slot-field]');

    if (!startInput || !endInput || !slotInput || !options || !field) {
      return;
    }

    const minutesFromTime = (value) => {
      const match = /^(\d{2}):(\d{2})/.exec(value || '');
      if (!match) {
        return null;
      }
      return Number(match[1]) * 60 + Number(match[2]);
    };

    const timeFromMinutes = (value) => {
      const hours = String(Math.floor(value / 60)).padStart(2, '0');
      const minutes = String(value % 60).padStart(2, '0');
      return `${hours}:${minutes}`;
    };

    const rebuildBreakSlots = () => {
      const selected = new Set(
        Array.from(options.querySelectorAll('input:checked')).map((input) => input.value),
      );
      const start = minutesFromTime(startInput.value);
      const end = minutesFromTime(endInput.value);
      const slot = Number.parseInt(slotInput.value, 10);
      const isWorking = !workingInput || workingInput.checked;
      const validRange = isWorking && start !== null && end !== null && end > start && slot > 0;

      options.replaceChildren();
      field.classList.toggle('is-disabled', !isWorking);
      if (!validRange) {
        emptyMessage.hidden = false;
        return;
      }

      let count = 0;
      for (let cursor = start; cursor + slot <= end; cursor += slot) {
        const value = timeFromMinutes(cursor);
        const label = document.createElement('label');
        label.className = 'break-slot-option';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.name = 'break_slots';
        input.value = value;
        input.checked = selected.has(value);
        const text = document.createElement('span');
        text.textContent = `${value}-${timeFromMinutes(cursor + slot)}`;
        label.append(input, text);
        options.append(label);
        count += 1;
      }
      emptyMessage.hidden = count > 0;
    };

    [startInput, endInput, slotInput, workingInput].forEach((input) => {
      if (input) {
        input.addEventListener('change', rebuildBreakSlots);
        input.addEventListener('input', rebuildBreakSlots);
      }
    });
    rebuildBreakSlots();
  });
});
