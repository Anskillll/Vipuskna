(() => {
  const normalize = (value) => value
    .toLocaleLowerCase('uk-UA')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .replace(/[^\p{Letter}\p{Number}]+/gu, ' ')
    .trim();

  const searchableParts = (value) => {
    const normalized = normalize(value);
    return {
      normalized,
      compact: normalized.replace(/\s+/g, ''),
    };
  };

  document.querySelectorAll('[data-live-filter]').forEach((root) => {
    const input = root.querySelector('[data-live-filter-input]');
    const items = Array.from(root.querySelectorAll('[data-live-filter-item]'));
    if (!input) {
      return;
    }

    const count = root.querySelector('[data-live-filter-count]');
    const empty = root.querySelector('[data-live-filter-empty]');
    if (!items.length) {
      if (count) {
        count.textContent = 'Усього: 0';
      }
      return;
    }
    const groups = Array.from(root.querySelectorAll('[data-live-filter-group]'));
    const indexedItems = items.map((item) => {
      const extra = item.dataset.filterExtra || '';
      return {
        item,
        search: searchableParts(`${item.textContent} ${extra}`),
      };
    });

    const applyFilter = () => {
      const query = searchableParts(input.value);
      const terms = query.normalized ? query.normalized.split(/\s+/) : [];
      let visibleCount = 0;

      indexedItems.forEach(({ item, search }) => {
        const matches = terms.every((term) => (
          search.normalized.includes(term)
          || search.compact.includes(term.replace(/\s+/g, ''))
        ));
        item.hidden = !matches;
        if (matches) {
          visibleCount += 1;
        }
      });

      groups.forEach((group) => {
        const groupItems = Array.from(group.querySelectorAll('[data-live-filter-item]'));
        const groupVisibleCount = groupItems.filter((item) => !item.hidden).length;
        group.hidden = groupVisibleCount === 0;
        const groupCount = group.querySelector('[data-live-filter-group-count]');
        if (groupCount) {
          const template = groupCount.dataset.countTemplate || '{count}';
          groupCount.textContent = template.replace('{count}', groupVisibleCount);
        }
      });

      if (count) {
        count.textContent = input.value.trim()
          ? `Знайдено: ${visibleCount} з ${items.length}`
          : `Усього: ${items.length}`;
      }
      if (empty) {
        empty.hidden = visibleCount !== 0;
      }
    };

    input.addEventListener('input', applyFilter);
    input.addEventListener('search', applyFilter);
    applyFilter();
  });
})();
