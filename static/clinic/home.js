(() => {
  const home = document.querySelector('.public-home');
  if (!home) {
    return;
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
    let activeGallerySlide = 0;
    let galleryTimer = null;

    const showGallerySlide = (index) => {
      activeGallerySlide = (index + gallerySlides.length) % gallerySlides.length;
      gallerySlides.forEach((slide, slideIndex) => {
        const isActive = slideIndex === activeGallerySlide;
        slide.classList.toggle('is-active', isActive);
        slide.setAttribute('aria-hidden', String(!isActive));
      });
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
      if (reducedMotion || gallerySlides.length < 2 || document.hidden) {
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
    gallery.addEventListener('mouseenter', stopGallerySlider);
    gallery.addEventListener('mouseleave', startGallerySlider);
    gallery.addEventListener('focusin', stopGallerySlider);
    gallery.addEventListener('focusout', startGallerySlider);
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
  }

  const layer = home.querySelector('[data-particle-effect]');
  if (!layer || reducedMotion) {
    return;
  }

  const effect = layer.dataset.particleEffect;
  const isTeeth = effect === 'teeth';
  const isCustom = effect === 'custom' && layer.dataset.particleImage;
  const isDentalField = effect === 'dental_field';
  const amount = isDentalField
    ? (window.innerWidth < 680 ? 110 : 260)
    : (window.innerWidth < 680 ? 12 : 24);
  const particles = [];
  const pointer = { x: -1000, y: -1000 };

  if (isDentalField) {
    window.addEventListener('pointermove', (event) => {
      const rect = layer.getBoundingClientRect();
      pointer.x = event.clientX - rect.left;
      pointer.y = event.clientY - rect.top;
    });
    window.addEventListener('pointerleave', () => {
      pointer.x = -1000;
      pointer.y = -1000;
    });
  }

  for (let index = 0; index < amount; index += 1) {
    const element = document.createElement(isCustom ? 'img' : 'span');
    const dentalKind = index % 3;
    element.className = isCustom
      ? 'home-particle custom-particle'
      : (isDentalField
        ? `home-particle dental-particle ${
          dentalKind === 0 ? 'dental-tooth' : (dentalKind === 1 ? 'braced-tooth' : 'dental-braces')
        }`
        : (isTeeth ? 'home-particle tooth' : 'home-particle medical-cross'));

    if (isCustom) {
      element.src = layer.dataset.particleImage;
      element.alt = '';
    } else if (isDentalField) {
      element.textContent = dentalKind === 2 ? '▦' : '🦷';
    } else {
      element.textContent = isTeeth ? '🦷' : '+';
    }

    layer.appendChild(element);
    particles.push({
      index,
      element,
      x: Math.random() * Math.max(1, layer.clientWidth - 32),
      y: Math.random() * Math.max(1, layer.clientHeight - 32),
      vx: (isDentalField ? Math.random() * 0.35 + 0.08 : Math.random() * 2.2 + 1.6)
        * (Math.random() > 0.5 ? 1 : -1),
      vy: (isDentalField ? Math.random() * 0.35 + 0.08 : Math.random() * 2.2 + 1.6)
        * (Math.random() > 0.5 ? 1 : -1),
      rotation: Math.random() * 360,
      spin: (Math.random() * 2 + 1) * (Math.random() > 0.5 ? 1 : -1),
    });
  }

  const separateDentalParticles = () => {
    const cellSize = 28;
    const minimumDistance = 22;
    const grid = new Map();

    particles.forEach((particle) => {
      const cellX = Math.floor(particle.x / cellSize);
      const cellY = Math.floor(particle.y / cellSize);
      const key = `${cellX}:${cellY}`;
      if (!grid.has(key)) {
        grid.set(key, []);
      }
      grid.get(key).push(particle);
    });

    particles.forEach((particle) => {
      const cellX = Math.floor(particle.x / cellSize);
      const cellY = Math.floor(particle.y / cellSize);
      for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
        for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
          const neighbours = grid.get(`${cellX + offsetX}:${cellY + offsetY}`) || [];
          neighbours.forEach((other) => {
            if (other.index <= particle.index) {
              return;
            }
            const dx = particle.x - other.x;
            const dy = particle.y - other.y;
            const distance = Math.sqrt(dx * dx + dy * dy) || 0.01;
            if (distance >= minimumDistance) {
              return;
            }
            const force = ((minimumDistance - distance) / minimumDistance) * 0.18;
            const forceX = (dx / distance) * force;
            const forceY = (dy / distance) * force;
            particle.vx += forceX;
            particle.vy += forceY;
            other.vx -= forceX;
            other.vy -= forceY;
          });
        }
      }
    });
  };

  const moveParticles = () => {
    const width = layer.clientWidth;
    const height = layer.clientHeight;
    if (isDentalField) {
      separateDentalParticles();
    }

    particles.forEach((particle) => {
      const size = particle.element.offsetWidth;
      if (isDentalField) {
        const dx = particle.x + size / 2 - pointer.x;
        const dy = particle.y + size / 2 - pointer.y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance > 0 && distance < 150) {
          const force = ((150 - distance) / 150) * 1.25;
          particle.vx += (dx / distance) * force;
          particle.vy += (dy / distance) * force;
        }
        particle.vx *= 0.985;
        particle.vy *= 0.985;
        const speed = Math.sqrt(particle.vx * particle.vx + particle.vy * particle.vy);
        if (speed > 5) {
          particle.vx = (particle.vx / speed) * 5;
          particle.vy = (particle.vy / speed) * 5;
        }
      }

      particle.x += particle.vx;
      particle.y += particle.vy;
      particle.rotation += particle.spin;
      if (particle.x <= 0 || particle.x + size >= width) {
        particle.vx *= -1;
        particle.x = Math.max(0, Math.min(particle.x, width - size));
      }
      if (particle.y <= 0 || particle.y + size >= height) {
        particle.vy *= -1;
        particle.y = Math.max(0, Math.min(particle.y, height - size));
      }
      particle.element.style.transform = `translate(${particle.x}px, ${particle.y}px) rotate(${particle.rotation}deg)`;
    });
    window.requestAnimationFrame(moveParticles);
  };

  moveParticles();
})();
