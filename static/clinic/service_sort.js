(() => {
  const list = document.querySelector('[data-service-sort]');
  if (!list) return;

  const rows = () => [...list.querySelectorAll('.service-manager-item[data-service-id]')];
  const ids = () => rows().map((row) => row.dataset.serviceId);
  const renumber = () => rows().forEach((row, index) => {
    const label = row.querySelector('.service-manager-label');
    if (label) label.textContent = `Послуга ${index + 1}`;
  });
  let dragged = null;
  let originalRows = [];
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

  list.addEventListener('dragstart', (event) => {
    const handle = event.target.closest('.service-drag-handle');
    if (!handle || saving) return;
    dragged = handle.closest('.service-manager-item');
    originalRows = rows();
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', dragged.dataset.serviceId);
    event.dataTransfer.setDragImage(dragged, dragged.offsetWidth - 12, dragged.offsetHeight / 2);
    requestAnimationFrame(() => dragged?.classList.add('is-dragging'));
  });

  list.addEventListener('dragover', (event) => {
    if (!dragged) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const peers = rows().filter((row) => row !== dragged && !row.hidden);
    const before = peers.find((row) => event.clientY < row.getBoundingClientRect().top + row.offsetHeight / 2);
    if (before) before.before(dragged);
    else if (peers.length) peers[peers.length - 1].after(dragged);
    if (event.clientY < 75) window.scrollBy(0, -20);
    if (event.clientY > window.innerHeight - 75) window.scrollBy(0, 20);
  });

  list.addEventListener('drop', (event) => {
    if (!dragged) return;
    event.preventDefault();
    dragged.classList.remove('is-dragging');
    dragged = null;
    void save(originalRows.map((row) => row.dataset.serviceId));
  });

  list.addEventListener('dragend', () => {
    if (!dragged) return;
    dragged.classList.remove('is-dragging');
    const empty = list.querySelector('[data-live-filter-empty]');
    originalRows.forEach((row) => list.insertBefore(row, empty));
    dragged = null;
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
