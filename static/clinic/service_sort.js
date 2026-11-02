(() => {
  const list = document.querySelector('[data-service-sort]');
  if (!list) return;

  const rows = () => [...list.querySelectorAll('.service-manager-item[data-service-id]')];
  const ids = () => rows().map((row) => row.dataset.serviceId);
  const renumber = () => rows().forEach((row, index) => {
    const label = row.querySelector('.service-manager-label');
    if (label) label.textContent = `Послуга ${index + 1}`;
  });
  let drag = null;
  let saving = false;

  const save = async (previousIds) => {
    const order = ids();
    if (order.every((id, index) => id === previousIds[index])) return;
    saving = true;
    const body = new FormData();
    body.set('action', 'reorder');
    body.set('csrfmiddlewaretoken', list.dataset.csrfToken);
    order.forEach((id) => body.append('service_id', id));
    try {
      const response = await fetch(window.location.pathname, {
        method: 'POST',
        body,
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error('Reorder failed');
      renumber();
    } catch {
      window.location.reload();
    } finally {
      saving = false;
    }
  };

  const movePlaceholder = (clientY) => {
    const peers = rows().filter((row) => row !== drag.row && !row.hidden);
    const before = peers.find((row) => clientY < row.getBoundingClientRect().top + row.offsetHeight / 2);
    const position = peers.filter((row) => row.compareDocumentPosition(drag.placeholder) & Node.DOCUMENT_POSITION_FOLLOWING).length;
    const nextPosition = before ? peers.indexOf(before) : peers.length;
    if (position === nextPosition || !peers.length) return;
    const previousTops = peers.map((row) => row.getBoundingClientRect().top);
    if (before) before.before(drag.placeholder);
    else peers[peers.length - 1].after(drag.placeholder);
    peers.forEach((row, index) => {
      const distance = previousTops[index] - row.getBoundingClientRect().top;
      if (Math.abs(distance) > 1 && row.animate) {
        row.animate([
          { transform: `translateY(${distance}px)` },
          { transform: 'translateY(0)' },
        ], { duration: 180, easing: 'cubic-bezier(.2, .7, .2, 1)' });
      }
    });
  };

  const positionCard = () => {
    if (!drag || !drag.active) return;
    const distance = drag.clientY + window.scrollY - drag.startY - drag.startScrollY;
    const maxTop = Math.max(0, list.clientHeight - drag.height);
    drag.row.style.top = `${Math.max(0, Math.min(maxTop, drag.top + distance))}px`;
    movePlaceholder(drag.clientY);
  };

  const autoScroll = () => {
    if (!drag || !drag.active) return;
    const edge = 75;
    const speed = drag.clientY < edge ? -16 : drag.clientY > window.innerHeight - edge ? 16 : 0;
    if (speed) window.scrollBy(0, speed);
    positionCard();
    drag.frame = requestAnimationFrame(autoScroll);
  };

  const startDrag = () => {
    const { row } = drag;
    const rect = row.getBoundingClientRect();
    const listRect = list.getBoundingClientRect();
    const placeholder = document.createElement('div');
    placeholder.className = 'service-sort-placeholder';
    placeholder.style.height = `${rect.height}px`;
    row.before(placeholder);
    drag.placeholder = placeholder;
    drag.height = rect.height;
    drag.top = rect.top - listRect.top;
    drag.active = true;
    row.style.position = 'absolute';
    row.style.top = `${drag.top}px`;
    row.style.left = '0';
    row.style.width = `${rect.width}px`;
    row.classList.add('is-dragging');
    list.classList.add('is-sorting');
    drag.frame = requestAnimationFrame(autoScroll);
  };

  const finishDrag = (cancelled) => {
    if (!drag) return;
    const current = drag;
    drag = null;
    if (current.frame) cancelAnimationFrame(current.frame);
    if (current.handle.hasPointerCapture(current.pointerId)) {
      current.handle.releasePointerCapture(current.pointerId);
    }
    if (!current.active) return;
    const previousIds = current.originalRows.map((row) => row.dataset.serviceId);
    current.placeholder.before(current.row);
    current.placeholder.remove();
    current.row.style.position = '';
    current.row.style.top = '';
    current.row.style.left = '';
    current.row.style.width = '';
    current.row.classList.remove('is-dragging');
    list.classList.remove('is-sorting');
    if (cancelled) {
      const empty = list.querySelector('[data-live-filter-empty]');
      current.originalRows.forEach((row) => list.insertBefore(row, empty));
    } else {
      void save(previousIds);
    }
  };

  list.addEventListener('pointerdown', (event) => {
    const handle = event.target.closest('.service-drag-handle');
    if (!handle || saving || drag || (event.pointerType === 'mouse' && event.button !== 0)) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    drag = {
      row: handle.closest('.service-manager-item'),
      handle,
      pointerId: event.pointerId,
      originalRows: rows(),
      startY: event.clientY,
      startScrollY: window.scrollY,
      clientY: event.clientY,
      active: false,
    };
  });

  list.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag.clientY = event.clientY;
    if (!drag.active && Math.abs(event.clientY - drag.startY) >= 5) startDrag();
    positionCard();
  });

  list.addEventListener('pointerup', (event) => {
    if (drag && event.pointerId === drag.pointerId) finishDrag(false);
  });
  list.addEventListener('pointercancel', (event) => {
    if (drag && event.pointerId === drag.pointerId) finishDrag(true);
  });

  list.addEventListener('keydown', (event) => {
    const handle = event.target.closest('.service-drag-handle');
    if (!handle || !event.altKey || saving) return;
    const offset = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
    if (!offset) return;
    const row = handle.closest('.service-manager-item');
    const visibleRows = rows().filter((item) => !item.hidden);
    const index = visibleRows.indexOf(row);
    const peer = visibleRows[index + offset];
    if (!peer) return;
    event.preventDefault();
    const previousIds = ids();
    if (offset < 0) peer.before(row);
    else peer.after(row);
    handle.focus();
    void save(previousIds);
  });
})();
