// Refresh the captcha image without reloading the page.
// Without it, anyone who cannot read the image has to fill the form again.
(function () {
  'use strict';

  document.addEventListener('click', function (event) {
    var button = event.target.closest('.js-captcha-refresh');
    if (!button) return;

    event.preventDefault();
    var wrapper = button.parentElement;

    fetch(button.dataset.refreshUrl, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var image = wrapper.querySelector('img');
        var hidden = wrapper.querySelector('input[type="hidden"]');
        var text = wrapper.querySelector('input[type="text"]');

        if (image) image.src = data.image_url;
        if (hidden) hidden.value = data.key;
        if (text) { text.value = ''; text.focus(); }
      })
      .catch(function () {
        // If the refresh fails, reloading the page always works.
      });
  });
})();
