// Copy button for API keys, share links and any other text.
//
// Two ways to say what to copy:
//   data-copy-target="#selector"  read it from an element on the page
//   data-copy-text="..."          the text itself
//
// The second exists for lists. A row in a table of files has nowhere to point
// at, and adding a hidden element per row purely to give the button a selector
// is markup that exists to satisfy the script rather than the reader.
(function () {
  'use strict';

  function textToCopy(button) {
    if (typeof button.dataset.copyText === 'string') {
      return button.dataset.copyText;
    }
    var target = document.querySelector(button.dataset.copyTarget || '');
    return target ? target.textContent : null;
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('.js-copy');
    if (!button) return;

    var raw = textToCopy(button);
    if (raw === null) return;

    var text = raw.trim();
    if (!text) return;

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
