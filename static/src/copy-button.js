// Copy button for API keys and any other text.
(function () {
  'use strict';

  document.addEventListener('click', function (event) {
    var button = event.target.closest('.js-copy');
    if (!button) return;

    var target = document.querySelector(button.dataset.copyTarget);
    if (!target) return;

    var text = target.textContent.trim();
    var original = button.textContent;

    function done() {
      button.textContent = '✓';
      setTimeout(function () { button.textContent = original; }, 1500);
    }

    // The clipboard API only works on https and localhost, so http needs a
    // fallback.
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done);
      return;
    }

    var helper = document.createElement('textarea');
    helper.value = text;
    helper.style.position = 'fixed';
    helper.style.opacity = '0';
    document.body.appendChild(helper);
    helper.select();
    try { document.execCommand('copy'); done(); } catch (e) { /* ignore */ }
    document.body.removeChild(helper);
  });
})();
