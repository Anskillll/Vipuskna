(() => {
  const visibleTime = 1800;
  const animationTime = 200;

  document.querySelectorAll("[data-auto-dismiss]").forEach((message) => {
    window.setTimeout(() => {
      message.classList.add("is-dismissing");

      window.setTimeout(() => {
        const container = message.parentElement;
        message.remove();

        if (container && container.children.length === 0) {
          container.remove();
        }
      }, animationTime);
    }, visibleTime);
  });
})();
