// Theme switch: light, dark, or follow the system.
//
// The choice lands on data-theme at the document root and the CSS does the
// rest. No attribute means follow the system.
(function () {
  'use strict';

  var KEY = 'dup-theme';
  var ORDER = ['system', 'light', 'dark'];
  var root = document.documentElement;

  function apply(theme) {
    if (theme === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', theme);
    }
  }

  function stored() {
    try {
      var value = localStorage.getItem(KEY);
      return ORDER.indexOf(value) === -1 ? 'system' : value;
    } catch (e) {
      // Private browsing may forbid localStorage.
      return 'system';
    }
  }

  // Applied as the file loads, from the head, so the page does not render in
  // the wrong theme and then jump.
  apply(stored());

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-theme-toggle]');
    if (!button) return;

    var next = ORDER[(ORDER.indexOf(stored()) + 1) % ORDER.length];
    try {
      localStorage.setItem(KEY, next);
    } catch (e) { /* never mind; it applies for this visit only */ }
    apply(next);
    button.setAttribute('title', button.dataset['label' + next.charAt(0).toUpperCase() + next.slice(1)] || '');
  });
})();
