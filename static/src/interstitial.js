// Countdown on the short link interstitial.
//
// The button is in the HTML from the start and is only briefly disabled. If
// this script never loads the button stays live and nobody is stuck: the worst
// case is a few seconds less ad exposure, not a broken link.
(function () {
  'use strict';

  var button = document.querySelector('[data-continue]');
  if (!button) return;

  var label = button.querySelector('[data-continue-label]');
  var timer = button.querySelector('[data-continue-timer]');
  var remaining = parseInt(button.dataset.wait, 10);

  if (!remaining || remaining < 1) return;

  button.disabled = true;

  function tick() {
    if (remaining <= 0) {
      button.disabled = false;
      if (timer) timer.textContent = '';
      return;
    }
    if (timer) timer.textContent = '(' + remaining + ')';
    remaining--;
    window.setTimeout(tick, 1000);
  }

  tick();
})();
