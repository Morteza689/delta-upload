// Tilt the landing page scene with the pointer.
//
// No library: it sets two CSS variables and the browser's own transforms do
// the rest. If the file never loads, the scene keeps its starting angle.
(function () {
  'use strict';

  var scene = document.querySelector('[data-hero-scene]');
  if (!scene) return;

  var stage = scene.querySelector('[data-hero-stage]');
  if (!stage) return;

  // Anyone who asked for reduced motion should not get an animated scene.
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  if (reduced.matches) return;

  var MAX_TILT = 14;
  var frame = null;

  function apply(x, y) {
    frame = null;
    var rect = scene.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    // Pointer position within the scene, from -0.5 to 0.5.
    var dx = (x - rect.left) / rect.width - 0.5;
    var dy = (y - rect.top) / rect.height - 0.5;

    stage.style.setProperty('--ry', (dx * MAX_TILT * 2).toFixed(2) + 'deg');
    stage.style.setProperty('--rx', (-dy * MAX_TILT).toFixed(2) + 'deg');
  }

  function onMove(event) {
    // rAF so a layout is not forced for every pixel of pointer movement.
    if (frame) return;
    var x = event.clientX;
    var y = event.clientY;
    frame = window.requestAnimationFrame(function () { apply(x, y); });
  }

  function reset() {
    stage.style.setProperty('--ry', '-18deg');
    stage.style.setProperty('--rx', '12deg');
  }

  scene.addEventListener('mousemove', onMove);
  scene.addEventListener('mouseleave', reset);
})();
