(() => {
  const home = document.querySelector('.public-home');
  if (!home) {
    return;
  }

  const feedbackLink = home.querySelector('[data-home-feedback]');
  if (feedbackLink) {
    const desktopInput = window.matchMedia('(hover: hover) and (pointer: fine)');
    const phoneHref = feedbackLink.getAttribute('href');
    const updateFeedbackLink = () => {
      if (desktopInput.matches) {
        feedbackLink.removeAttribute('href');
        feedbackLink.setAttribute('aria-disabled', 'true');
        feedbackLink.setAttribute('tabindex', '-1');
      } else {
        feedbackLink.setAttribute('href', phoneHref);
        feedbackLink.removeAttribute('aria-disabled');
        feedbackLink.removeAttribute('tabindex');
      }
    };
    updateFeedbackLink();
    desktopInput.addEventListener('change', updateFeedbackLink);
  }

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const hero = home.querySelector('[data-home-hero]');
  const topbar = document.querySelector('[data-home-reveal-header]');
  const slides = Array.from(home.querySelectorAll('[data-home-slide]'));
  const dotsRoot = home.querySelector('[data-home-slider-dots]');
  const previousButton = home.querySelector('[data-home-slide-prev]');
  const nextButton = home.querySelector('[data-home-slide-next]');
  let activeSlide = 0;
  let slideTimer = null;

  const updateTopbar = () => {
    if (!topbar) {
      return;
    }
    topbar.classList.toggle('is-visible', window.scrollY > 64);
  };

  const showSlide = (index) => {
    if (!slides.length) {
      return;
    }
    activeSlide = (index + slides.length) % slides.length;
    slides.forEach((slide, slideIndex) => {
      const isActive = slideIndex === activeSlide;
      slide.classList.toggle('is-active', isActive);
      slide.setAttribute('aria-hidden', String(!isActive));
    });
    if (dotsRoot) {
      Array.from(dotsRoot.children).forEach((dot, dotIndex) => {
        const isActive = dotIndex === activeSlide;
        dot.classList.toggle('is-active', isActive);
        dot.setAttribute('aria-current', isActive ? 'true' : 'false');
      });
    }
  };

  const stopSlider = () => {
    if (slideTimer) {
      window.clearInterval(slideTimer);
      slideTimer = null;
    }
  };

  const startSlider = () => {
    stopSlider();
    if (reducedMotion || slides.length < 2 || document.hidden) {
      return;
    }
    slideTimer = window.setInterval(() => showSlide(activeSlide + 1), 10000);
  };

  if (dotsRoot) {
    slides.forEach((slide, index) => {
      const dot = document.createElement('button');
      dot.type = 'button';
      dot.setAttribute('aria-label', `Показати фото ${index + 1}`);
      dot.addEventListener('click', () => {
        showSlide(index);
        startSlider();
      });
      dotsRoot.appendChild(dot);
    });
  }

  if (previousButton) {
    previousButton.addEventListener('click', () => {
      showSlide(activeSlide - 1);
      startSlider();
    });
  }
  if (nextButton) {
    nextButton.addEventListener('click', () => {
      showSlide(activeSlide + 1);
      startSlider();
    });
  }
  if (hero) {
    hero.addEventListener('mouseenter', stopSlider);
    hero.addEventListener('mouseleave', startSlider);
  }

  document.addEventListener('visibilitychange', startSlider);
  window.addEventListener('scroll', updateTopbar, { passive: true });
  showSlide(0);
  startSlider();
  updateTopbar();

  const gallery = home.querySelector('[data-home-gallery-slider]');
  if (gallery) {
    const gallerySlides = Array.from(gallery.querySelectorAll('[data-home-gallery-slide]'));
    const galleryDotsRoot = gallery.querySelector('[data-home-gallery-dots]');
    const galleryPreviousButton = gallery.querySelector('[data-home-gallery-prev]');
    const galleryNextButton = gallery.querySelector('[data-home-gallery-next]');
    const galleryCount = gallery.querySelector('[data-home-gallery-count]');
    let activeGallerySlide = 0;
    let galleryTimer = null;
    let galleryHovered = false;

    const showGallerySlide = (index) => {
      if (!gallerySlides.length) return;
      activeGallerySlide = (index + gallerySlides.length) % gallerySlides.length;
      gallerySlides.forEach((slide, slideIndex) => {
        const isActive = slideIndex === activeGallerySlide;
        let offset = (slideIndex - activeGallerySlide + gallerySlides.length) % gallerySlides.length;
        if (offset > gallerySlides.length / 2) offset -= gallerySlides.length;
        slide.dataset.position = String(Math.max(-2, Math.min(2, offset)));
        slide.classList.toggle('is-active', isActive);
        slide.setAttribute('aria-hidden', String(!isActive));
      });
      if (galleryCount) galleryCount.textContent = `${String(activeGallerySlide + 1).padStart(2, '0')} / ${String(gallerySlides.length).padStart(2, '0')}`;
      if (galleryDotsRoot) {
        Array.from(galleryDotsRoot.children).forEach((dot, dotIndex) => {
          const isActive = dotIndex === activeGallerySlide;
          dot.classList.toggle('is-active', isActive);
          dot.setAttribute('aria-current', isActive ? 'true' : 'false');
        });
      }
    };

    const stopGallerySlider = () => {
      if (galleryTimer) {
        window.clearInterval(galleryTimer);
        galleryTimer = null;
      }
    };

    const startGallerySlider = () => {
      stopGallerySlider();
      if (reducedMotion || galleryHovered || gallery.contains(document.activeElement) || gallerySlides.length < 2 || document.hidden) {
        return;
      }
      galleryTimer = window.setInterval(
        () => showGallerySlide(activeGallerySlide + 1),
        5000,
      );
    };

    if (galleryDotsRoot) {
      gallerySlides.forEach((slide, index) => {
        const dot = document.createElement('button');
        dot.type = 'button';
        dot.setAttribute('aria-label', `Показати фотографію ${index + 1}`);
        dot.addEventListener('click', () => {
          showGallerySlide(index);
          startGallerySlider();
        });
        galleryDotsRoot.appendChild(dot);
      });
    }

    galleryPreviousButton?.addEventListener('click', () => {
      showGallerySlide(activeGallerySlide - 1);
      startGallerySlider();
    });
    galleryNextButton?.addEventListener('click', () => {
      showGallerySlide(activeGallerySlide + 1);
      startGallerySlider();
    });
    gallery.addEventListener('mouseenter', () => { galleryHovered = true; stopGallerySlider(); });
    gallery.addEventListener('mouseleave', () => { galleryHovered = false; startGallerySlider(); });
    gallery.addEventListener('focusin', stopGallerySlider);
    gallery.addEventListener('focusout', () => window.setTimeout(startGallerySlider, 0));
    gallery.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      showGallerySlide(activeGallerySlide + (event.key === 'ArrowLeft' ? -1 : 1));
    });
    let swipeStart = null;
    gallery.addEventListener('pointerdown', (event) => {
      if (event.pointerType === 'mouse' || event.target.closest('button')) return;
      swipeStart = { x: event.clientX, y: event.clientY };
      stopGallerySlider();
    });
    gallery.addEventListener('pointerup', (event) => {
      if (!swipeStart) return;
      const dx = event.clientX - swipeStart.x;
      const dy = event.clientY - swipeStart.y;
      if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
        showGallerySlide(activeGallerySlide + (dx < 0 ? 1 : -1));
      }
      swipeStart = null;
      startGallerySlider();
    });
    gallery.addEventListener('pointercancel', () => { swipeStart = null; startGallerySlider(); });
    document.addEventListener('visibilitychange', startGallerySlider);

    showGallerySlide(0);
    startGallerySlider();
  }

  const loginModal = document.querySelector('[data-home-login-modal]');
  if (loginModal) {
    const loginDialog = loginModal.querySelector('.home-login-dialog');
    const loginOpeners = Array.from(document.querySelectorAll('[data-home-login-open]'));
    const loginClosers = Array.from(loginModal.querySelectorAll('[data-home-login-close]'));
    let loginReturnFocus = null;
    let loginCloseTimer = null;

    const loginFocusableElements = () => Array.from(
      loginDialog.querySelectorAll(
        'a[href], button:not([disabled]):not([tabindex="-1"]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
      ),
    );

    const openLoginModal = (trigger = null) => {
      if (loginCloseTimer) {
        window.clearTimeout(loginCloseTimer);
        loginCloseTimer = null;
      }
      loginReturnFocus = trigger || document.activeElement;
      loginModal.hidden = false;
      loginModal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('home-login-open');
      window.requestAnimationFrame(() => {
        loginModal.classList.add('is-open');
        loginDialog.focus({ preventScroll: true });
      });
    };

    const closeLoginModal = () => {
      loginModal.classList.remove('is-open');
      loginModal.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('home-login-open');
      loginCloseTimer = window.setTimeout(() => {
        loginModal.hidden = true;
        loginReturnFocus?.focus({ preventScroll: true });
      }, reducedMotion ? 0 : 210);
    };

    loginOpeners.forEach((opener) => {
      opener.addEventListener('click', () => openLoginModal(opener));
    });
    loginClosers.forEach((closer) => {
      closer.addEventListener('click', closeLoginModal);
    });
    loginModal.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeLoginModal();
        return;
      }
      if (event.key !== 'Tab') {
        return;
      }
      const focusable = loginFocusableElements();
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    if (loginModal.classList.contains('is-open')) {
      document.body.classList.add('home-login-open');
      window.requestAnimationFrame(() => loginDialog.focus({ preventScroll: true }));
    }

    const phoneLoginWaiting = loginModal.querySelector('[data-phone-login-waiting]');
    if (phoneLoginWaiting) {
      const statusElement = phoneLoginWaiting.querySelector('[data-phone-login-status]');
      const messageElement = phoneLoginWaiting.querySelector('[data-phone-login-message]');
      const spinnerElement = phoneLoginWaiting.querySelector('[data-phone-login-spinner]');
      const retryElement = phoneLoginWaiting.querySelector('[data-phone-login-retry]');
      const csrfElement = phoneLoginWaiting.querySelector('[name="csrfmiddlewaretoken"]');
      const statusUrl = phoneLoginWaiting.dataset.statusUrl;
      let phoneLoginTimer = null;
      let expiresIn = null;

      const formatRemainingTime = (seconds) => {
        const safeSeconds = Math.max(0, Number(seconds) || 0);
        const minutes = Math.floor(safeSeconds / 60);
        const remainder = safeSeconds % 60;
        return `${minutes}:${String(remainder).padStart(2, '0')}`;
      };

      const showPhoneLoginResult = (message, state = 'pending') => {
        statusElement.classList.toggle('is-error', state === 'error');
        statusElement.classList.toggle('is-approved', state === 'approved');
        spinnerElement.hidden = state !== 'pending';
        messageElement.textContent = message;
      };

      const stopPhoneLoginPolling = () => {
        if (phoneLoginTimer) {
          window.clearTimeout(phoneLoginTimer);
          phoneLoginTimer = null;
        }
      };

      const schedulePhoneLoginPoll = (delay = 1800) => {
        stopPhoneLoginPolling();
        phoneLoginTimer = window.setTimeout(checkPhoneLoginStatus, delay);
      };

      const checkPhoneLoginStatus = async () => {
        try {
          const response = await window.fetch(statusUrl, {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {
              'X-CSRFToken': csrfElement?.value || '',
              'X-Requested-With': 'XMLHttpRequest',
            },
          });
          const data = await response.json();
          if (data.status === 'approved' && data.redirect_url) {
            showPhoneLoginResult('Вхід підтверджено. Відкриваємо ваш кабінет…', 'approved');
            window.setTimeout(() => window.location.assign(data.redirect_url), 350);
            return;
          }
          if (data.status === 'pending') {
            expiresIn = Number(data.expires_in) || 0;
            showPhoneLoginResult(
              `Очікуємо підтвердження у Telegram · ${formatRemainingTime(expiresIn)}`,
            );
            schedulePhoneLoginPoll();
            return;
          }

          showPhoneLoginResult(
            data.message || 'Запит більше не діє. Введіть номер телефону ще раз.',
            'error',
          );
          retryElement.hidden = false;
        } catch (error) {
          showPhoneLoginResult('Не вдалося перевірити відповідь. Пробуємо ще раз…');
          schedulePhoneLoginPoll(3500);
        }
      };

      const countdownTimer = window.setInterval(() => {
        if (expiresIn === null || expiresIn <= 0 || spinnerElement.hidden) {
          return;
        }
        expiresIn -= 1;
        messageElement.textContent = (
          `Очікуємо підтвердження у Telegram · ${formatRemainingTime(expiresIn)}`
        );
      }, 1000);

      window.addEventListener('pagehide', () => {
        stopPhoneLoginPolling();
        window.clearInterval(countdownTimer);
      }, { once: true });
      schedulePhoneLoginPoll(450);
    }
  }

  const adminLoginDialog = document.querySelector('[data-admin-login-dialog]');
  if (adminLoginDialog) {
    const openAdminLogin = () => {
      if (!adminLoginDialog.open) adminLoginDialog.showModal();
    };
    const closeAdminLogin = () => {
      if (adminLoginDialog.open) adminLoginDialog.close();
    };

    document.querySelectorAll('[data-admin-login-open]').forEach((opener) => {
      opener.addEventListener('click', (event) => {
        event.preventDefault();
        openAdminLogin();
      });
    });
    adminLoginDialog.querySelectorAll('[data-admin-login-close]').forEach((closer) => {
      closer.addEventListener('click', closeAdminLogin);
    });
    adminLoginDialog.addEventListener('click', (event) => {
      if (event.target === adminLoginDialog) closeAdminLogin();
    });
    adminLoginDialog.addEventListener('close', () => {
      if (window.location.pathname === adminLoginDialog.dataset.adminLoginPath) {
        window.location.assign(adminLoginDialog.dataset.homePath);
      }
    });

    if (adminLoginDialog.dataset.autoOpen === 'true') openAdminLogin();
  }

})();
