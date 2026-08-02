(function () {
  function initializeBroadcastForm(form) {
    var audienceInputs = form.querySelectorAll('input[name="audience"]');
    var recipientBlock = form.querySelector('[data-broadcast-recipient]');
    var recipientSelect = recipientBlock ? recipientBlock.querySelector('select') : null;
    var message = form.querySelector('textarea[name="message"]');
    var counter = form.querySelector('[data-broadcast-count]');

    function updateRecipient() {
      var selected = form.querySelector('input[name="audience"]:checked');
      var showRecipient = selected && selected.value === 'single';
      if (recipientBlock) {
        recipientBlock.hidden = !showRecipient;
      }
      if (recipientSelect) {
        recipientSelect.disabled = !showRecipient;
      }
    }

    function updateCounter() {
      if (message && counter) {
        counter.textContent = String(message.value.length);
      }
    }

    audienceInputs.forEach(function (input) {
      input.addEventListener('change', updateRecipient);
    });
    if (message) {
      message.addEventListener('input', updateCounter);
    }
    form.addEventListener('submit', function () {
      var submitButton = form.querySelector('button[type="submit"]');
      if (submitButton && form.checkValidity()) {
        submitButton.disabled = true;
        submitButton.textContent = 'Надсилання…';
      }
    });

    updateRecipient();
    updateCounter();
  }

  document.querySelectorAll('[data-admin-broadcast-form]').forEach(initializeBroadcastForm);
}());
