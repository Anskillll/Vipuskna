(() => {
  const names = [
    ['Швейцарская клиника', 'Строгая модульная сетка, прямые панели и сдержанная медицинская подача.'],
    ['Ботаническая клиника', 'Природные оттенки, необычно расположенная навигация и асимметричные карточки.'],
    ['Тёплый журнал', 'Сайт как печатный журнал: колонка, крупные заголовки и бумажные карточки.'],
    ['Ночной премиум', 'Тёмные кабинеты, золотые акценты и почти чёрная полноэкранная главная.'],
    ['Яркий поп-арт', 'Плакатные заголовки, наклонные карточки и крупные цветовые пятна.'],
    ['Необрутализм', 'Тяжёлые чёрные рамки, квадратные кнопки, жёсткие тени и узорная подложка.'],
    ['Лавандовый велнес', 'Узкая центральная композиция, мягкие поверхности и симметричная навигация.'],
    ['Скандинавский покой', 'Большие интервалы, тихие цвета и карточки почти без рамок и теней.'],
    ['Аква-стекло', 'Полупрозрачные панели с размытием, цветные засветки и плавающий вид.'],
    ['Медицинский центр', 'Тёмно-синяя шапка, строгие разделители и плотная больничная сетка.'],
    ['Терракотовая студия', 'Земляная палитра, классический шрифт и чередующиеся формы карточек.'],
    ['Чёрно-белый минимализм', 'Монохромные блоки, широкие интервалы и рубленые заголовки.'],
    ['Кобальтовый техно', 'Сетка как на чертеже, компактная панель и синие края карточек.'],
    ['Розовое ателье', 'Редакционная типографика, рамки вокруг фото и пудровый акцент.'],
    ['Мятная забота', 'Округлая шапка-капсула, дружелюбные элементы и свежая палитра.'],
    ['Ретро-практика', 'Кремовая бумага, двойные рамки и вывески старой медицинской клиники.'],
    ['Ночной лес', 'Тёмно-зелёные панели, светлый текст и вертикальные акценты.'],
    ['Чистая клиника', 'Белый просторный интерфейс с точечными красными медицинскими метками.'],
    ['Цифровой градиент', 'Переходы цвета, светящиеся вкладки и современная цифровая подача.'],
    ['Песочная органика', 'Асимметричные карточки, оливковые детали и натуральная типографика.'],
  ];
  const palette = document.getElementById('theme-lab');
  const options = document.getElementById('theme-lab-options');
  if (!palette || !options) return;

  const read = (key) => {
    try { return localStorage.getItem(key); } catch { return null; }
  };
  const write = (key, value) => {
    try { localStorage.setItem(key, value); } catch { /* Preview still works for this page. */ }
  };
  const labRequested = window.location.hash === '#theme-lab';
  if (labRequested) {
    write('medclinic-theme-lab', 'on');
    if (!read('medclinic-theme')) write('medclinic-theme', '1');
    history.replaceState(null, '', `${location.pathname}${location.search}`);
  }
  const theme = read('medclinic-theme');
  if (theme && /^(?:[1-9]|1[0-9]|20)$/.test(theme)) document.body.dataset.siteTheme = theme;
  const active = labRequested || read('medclinic-theme-lab') === 'on';
  palette.hidden = !active;
  if (!active) return;

  names.forEach(([name, description], index) => {
    const number = index + 1;
    const button = document.createElement('button');
    button.className = 'theme-lab-option';
    button.type = 'button';
    button.dataset.theme = String(number);
    button.setAttribute('aria-pressed', String(theme === String(number)));
    button.innerHTML = `<span class="theme-lab-number">${String(number).padStart(2, '0')}</span><span class="theme-lab-option-copy"><strong>${name}</strong><small>${description}</small></span><span class="theme-lab-swatch" aria-hidden="true"><i></i><i></i><i></i></span>`;
    button.addEventListener('click', () => select(number, name));
    options.append(button);
  });

  const current = document.getElementById('theme-lab-current');
  function select(number, name) {
    document.body.dataset.siteTheme = String(number);
    write('medclinic-theme', String(number));
    current.textContent = `Сейчас включена тема ${String(number).padStart(2, '0')} · ${name}`;
    options.querySelectorAll('.theme-lab-option').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.theme === String(number)));
    });
  }
  if (theme && names[Number(theme) - 1]) select(Number(theme), names[Number(theme) - 1][0]);
  else current.textContent = 'Сейчас исходный стиль сайта. Выбери тему ниже.';

  document.getElementById('theme-lab-toggle').addEventListener('click', (event) => {
    const collapsed = palette.classList.toggle('is-collapsed');
    event.currentTarget.setAttribute('aria-expanded', String(!collapsed));
  });
  document.getElementById('theme-lab-hide').addEventListener('click', () => {
    palette.classList.add('is-collapsed');
    document.getElementById('theme-lab-toggle').setAttribute('aria-expanded', 'false');
  });
  document.getElementById('theme-lab-reset').addEventListener('click', () => {
    delete document.body.dataset.siteTheme;
    try { localStorage.removeItem('medclinic-theme'); } catch { /* Preview still works for this page. */ }
    options.querySelectorAll('.theme-lab-option').forEach((button) => button.setAttribute('aria-pressed', 'false'));
    current.textContent = 'Сейчас исходный стиль сайта. Выбери тему ниже.';
  });
  document.getElementById('theme-lab-exit').addEventListener('click', () => {
    try { localStorage.removeItem('medclinic-theme-lab'); } catch { /* The current page can still close the panel. */ }
    palette.hidden = true;
  });
})();
