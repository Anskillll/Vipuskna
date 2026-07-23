(() => {
  const returnLabel = (pathname) => {
    if (pathname.startsWith('/panel/')) return 'до панелі адміністратора';
    if (pathname.startsWith('/doctor/requests/')) return 'до моїх заявок';
    if (pathname.startsWith('/doctor/appointments/')) return 'до прийомів';
    if (pathname.startsWith('/doctor/patients/')) return 'до пацієнтів';
    if (pathname.startsWith('/doctor/schedule/')) return 'до графіка';
    if (pathname.startsWith('/doctor/services/')) return 'до послуг';
    if (pathname.startsWith('/doctor/news/')) return 'до моїх новин';
    if (pathname.startsWith('/doctor/')) return 'до кабінету лікаря';
    if (pathname.startsWith('/patient/')) return 'до мого кабінету';
    if (pathname.startsWith('/doctors/')) return 'до лікарів';
    if (pathname.startsWith('/booking/')) return 'до запису';
    if (pathname === '/') return 'на головну';
    return 'на попередню сторінку';
  };

  document.querySelectorAll('[data-history-back]').forEach((button) => {
    const label = button.querySelector('[data-history-back-label]');
    let canReturnThroughHistory = false;

    if (document.referrer) {
      const previousUrl = new URL(document.referrer);
      canReturnThroughHistory = (
        previousUrl.origin === window.location.origin
        && previousUrl.href !== window.location.href
      );
      if (canReturnThroughHistory && label) {
        label.textContent = `Повернутися ${returnLabel(previousUrl.pathname)}`;
      }
    }

    button.addEventListener('click', () => {
      if (canReturnThroughHistory) {
        window.history.back();
        return;
      }
      window.location.assign(button.dataset.fallbackUrl || '/');
    });
  });
})();
