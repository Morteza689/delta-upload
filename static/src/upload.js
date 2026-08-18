// Upload with a progress percentage.
//
// XMLHttpRequest rather than fetch: fetch still has no upload progress event,
// and for a multi-gigabyte file the percentage is exactly what the user needs.
//
// Without JavaScript the form submits normally and the upload still happens,
// just without the bar.
(function () {
  'use strict';

  var form = document.querySelector('[data-upload-form]');
  if (!form || !window.FormData || !window.XMLHttpRequest) return;

  var box = form.querySelector('[data-upload-progress]');
  var fill = form.querySelector('[data-upload-fill]');
  var percentText = form.querySelector('[data-upload-percent]');
  var statusText = form.querySelector('[data-upload-status]');
  var sizeText = form.querySelector('[data-upload-size]');
  var submitBtn = form.querySelector('[type="submit"]');
  var cancelBtn = form.querySelector('[data-upload-cancel]');
  var fileInput = form.querySelector('input[type="file"]');
  var messages = JSON.parse(form.dataset.uploadMessages || '{}');

  var request = null;
  var startedAt = 0;

  function humanSize(bytes) {
    var units = ['B', 'KB', 'MB', 'GB', 'TB'];
    var size = bytes;
    var unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
      size /= 1024;
      unit++;
    }
    return (unit === 0 ? size : size.toFixed(1)) + ' ' + units[unit];
  }

  function setProgress(loaded, total) {
    var percent = total ? Math.round((loaded / total) * 100) : 0;
    if (fill) fill.style.width = percent + '%';
    // The sign comes from the page, so it follows the language like
    // every other label here.
    if (percentText) {
      percentText.textContent = percent + (messages.percent || '%');
    }
    if (sizeText) sizeText.textContent = humanSize(loaded) + ' / ' + humanSize(total);

    // Speed and time left, from the average so far.
    if (statusText && loaded > 0) {
      var elapsed = (Date.now() - startedAt) / 1000;
      if (elapsed > 0.5) {
        var speed = loaded / elapsed;
        var remaining = speed > 0 ? Math.round((total - loaded) / speed) : 0;
        statusText.textContent =
          humanSize(speed) + '/s · ' + formatSeconds(remaining) + ' ' + (messages.remaining || '');
      }
    }
  }

  function formatSeconds(seconds) {
    if (seconds < 60) return seconds + 's';
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + 'm ' + (seconds % 60) + 's';
    return Math.floor(minutes / 60) + 'h ' + (minutes % 60) + 'm';
  }

  function showBox(visible) {
    if (box) box.hidden = !visible;
    if (cancelBtn) cancelBtn.hidden = !visible;
    if (submitBtn) submitBtn.disabled = visible;
  }

  function fail(message) {
    showBox(false);
    if (statusText) statusText.textContent = '';
    var note = form.querySelector('[data-upload-error]');
    if (note) {
      note.textContent = message;
      note.hidden = false;
    }
  }

  form.addEventListener('submit', function (event) {
    if (!fileInput || !fileInput.files.length) return;   // Let the normal form report the error.

    event.preventDefault();

    var note = form.querySelector('[data-upload-error]');
    if (note) note.hidden = true;

    request = new XMLHttpRequest();
    request.open('POST', form.action || window.location.href, true);
    request.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

    startedAt = Date.now();
    showBox(true);
    setProgress(0, fileInput.files[0].size);

    request.upload.addEventListener('progress', function (e) {
      if (e.lengthComputable) setProgress(e.loaded, e.total);
    });

    request.addEventListener('load', function () {
      if (request.status >= 200 && request.status < 300) {
        if (statusText) statusText.textContent = messages.done || '';
        // The server names the next destination; without one, go to the file list.
        var target = form.dataset.uploadSuccessUrl;
        try {
          var payload = JSON.parse(request.responseText);
          if (payload && payload.redirect) target = payload.redirect;
        } catch (e) { /* the response was not JSON */ }
        window.location.href = target;
        return;
      }

      // Validation error: the server returns the form HTML with the messages.
      if (request.status === 422 || request.status === 400) {
        document.open();
        document.write(request.responseText);
        document.close();
        return;
      }
      fail(messages.failed || 'Upload failed');
    });

    request.addEventListener('error', function () {
      fail(messages.network || 'Network error');
    });
    request.addEventListener('abort', function () {
      showBox(false);
      if (statusText) statusText.textContent = '';
    });

    request.send(new FormData(form));
  });

  if (cancelBtn) {
    cancelBtn.addEventListener('click', function () {
      if (request) request.abort();
    });
  }

  // Warn if the page is closed mid-upload.
  window.addEventListener('beforeunload', function (event) {
    if (request && request.readyState !== 4 && request.readyState !== 0) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
})();
