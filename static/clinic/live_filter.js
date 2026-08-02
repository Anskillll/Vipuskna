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

  document.querySelectorAll('[data-live-filter], [data-live-pagination]').forEach((root) => {
    const input = root.querySelector('[data-live-filter-input]');
    const items = Array.from(root.querySelectorAll('[data-live-filter-item]'));
    const count = root.querySelector('[data-live-filter-count]');
    const empty = root.querySelector('[data-live-filter-empty]');
    const clearButton = root.querySelector('[data-live-filter-clear]');
    const valueButtons = root.querySelectorAll('[data-live-filter-value]');
    const requestedPageSize = Number.parseInt(root.dataset.pageSize || '10', 10);
    const pageSize = requestedPageSize > 0 ? requestedPageSize : 10;
    const paginationEnabled = !root.hasAttribute('data-live-filter-no-pagination');
    if (!items.length) {
      if (count) {
        count.textContent = 'Усього: 0';
      }
      return;
    }
    const groups = Array.from(root.querySelectorAll('[data-live-filter-group]'));
    const indexedItems = items.map((item) => {
      const extra = item.dataset.filterExtra || '';
      const searchableText = item.hasAttribute('data-filter-extra-only')
        ? extra
        : `${item.textContent} ${extra}`;
      return {
        item,
        search: searchableParts(searchableText),
      };
    });
    const pagination = document.createElement('nav');
    pagination.className = 'live-pagination';
    pagination.setAttribute('aria-label', 'Сторінки списку');
    pagination.hidden = true;
    root.appendChild(pagination);

    let currentPage = 1;
    let matchingItems = indexedItems;

    const pageNumbers = (totalPages) => {
      if (totalPages <= 7) {
        return Array.from({ length: totalPages }, (_, index) => index + 1);
      }

      const pages = [1];
      const start = Math.max(2, currentPage - 1);
      const end = Math.min(totalPages - 1, currentPage + 1);
      if (start > 2) {
        pages.push(null);
      }
      for (let page = start; page <= end; page += 1) {
        pages.push(page);
      }
      if (end < totalPages - 1) {
        pages.push(null);
      }
      pages.push(totalPages);
      return pages;
    };

    const pageButton = (label, page, options = {}) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.dataset.liveFilterPage = String(page);
      button.setAttribute('aria-label', options.ariaLabel || `Сторінка ${page}`);
      button.disabled = Boolean(options.disabled);
      if (options.current) {
        button.classList.add('active');
        button.setAttribute('aria-current', 'page');
      }
      return button;
    };

    const renderPagination = (totalPages) => {
      pagination.replaceChildren();
      pagination.hidden = totalPages <= 1;
      if (totalPages <= 1) {
        return;
      }

      pagination.appendChild(pageButton('‹', currentPage - 1, {
        ariaLabel: 'Попередня сторінка',
        disabled: currentPage === 1,
      }));

      pageNumbers(totalPages).forEach((page) => {
        if (page === null) {
          const separator = document.createElement('span');
          separator.className = 'live-pagination-ellipsis';
          separator.textContent = '…';
          separator.setAttribute('aria-hidden', 'true');
          pagination.appendChild(separator);
          return;
        }
        pagination.appendChild(pageButton(String(page), page, {
          current: page === currentPage,
        }));
      });

      pagination.appendChild(pageButton('›', currentPage + 1, {
        ariaLabel: 'Наступна сторінка',
        disabled: currentPage === totalPages,
      }));
    };

    const renderPage = (scrollToTop = false) => {
      const totalPages = paginationEnabled
        ? Math.max(1, Math.ceil(matchingItems.length / pageSize))
        : 1;
      currentPage = Math.min(Math.max(currentPage, 1), totalPages);
      const pageStart = (currentPage - 1) * pageSize;
      const visibleItems = new Set(
        (paginationEnabled
          ? matchingItems.slice(pageStart, pageStart + pageSize)
          : matchingItems
        ).map(({ item }) => item),
      );

      indexedItems.forEach(({ item }) => {
        item.hidden = !visibleItems.has(item);
      });

      groups.forEach((group) => {
        const groupItems = Array.from(group.querySelectorAll('[data-live-filter-item]'));
        const groupVisibleCount = groupItems.filter((item) => !item.hidden).length;
        const keepEmpty = group.hasAttribute('data-live-filter-keep-empty');
        const hasActiveFilter = Boolean(input && input.value.trim());
        group.hidden = groupVisibleCount === 0 && !(keepEmpty && !hasActiveFilter);
        const groupCount = group.querySelector('[data-live-filter-group-count]');
        if (groupCount) {
          const template = groupCount.dataset.countTemplate || '{count}';
          groupCount.textContent = template.replace('{count}', groupVisibleCount);
        }
      });

      if (count) {
        count.textContent = input && input.value.trim()
          ? `Знайдено: ${matchingItems.length} з ${items.length}`
          : `Усього: ${items.length}`;
      }
      if (empty) {
        empty.hidden = matchingItems.length !== 0;
      }
      if (clearButton) {
        clearButton.disabled = !input.value;
      }

      renderPagination(paginationEnabled ? Math.ceil(matchingItems.length / pageSize) : 1);
      if (scrollToTop) {
        const target = root.querySelector('.live-filter-bar') || root;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    };

    const applyFilter = (resetPage = true) => {
      const query = searchableParts(input ? input.value : '');
      root.classList.toggle('is-filtering', Boolean(query.normalized));
      const terms = query.normalized ? query.normalized.split(/\s+/) : [];
      matchingItems = indexedItems.filter(({ search }) => (
        terms.every((term) => (
          search.normalized.includes(term)
          || search.compact.includes(term.replace(/\s+/g, ''))
        ))
      ));
      if (resetPage) {
        currentPage = 1;
      }
      renderPage();
    };

    if (input) {
      input.addEventListener('input', applyFilter);
      input.addEventListener('search', applyFilter);
      valueButtons.forEach((button) => {
        button.addEventListener('click', () => {
          input.value = button.dataset.liveFilterValue || '';
          applyFilter();
          input.focus();
        });
      });
      if (clearButton) {
        clearButton.addEventListener('click', () => {
          input.value = '';
          applyFilter();
          input.focus();
        });
      }
    }
    pagination.addEventListener('click', (event) => {
      const button = event.target.closest('[data-live-filter-page]');
      if (!button || button.disabled) {
        return;
      }
      currentPage = Number.parseInt(button.dataset.liveFilterPage, 10);
      renderPage(true);
    });
    applyFilter();

    const activeItem = root.querySelector('[data-live-filter-active]');
    if (activeItem) {
      const activeIndex = matchingItems.findIndex(({ item }) => item === activeItem);
      if (activeIndex >= 0) {
        currentPage = Math.floor(activeIndex / pageSize) + 1;
        renderPage();
      }
    }
  });
})();
