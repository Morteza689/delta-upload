// Keep the Telegram transfer percentages moving without reloading the page.
//
// Every number is already in the HTML when the page loads, so without this
// script the page is still correct, just frozen until the next reload. That is
// why it only ever rewrites text and a bar width, and never builds a row.
//
// Polling stops as soon as nothing is in flight, so an idle page costs one
// request rather than one every few seconds for as long as it stays open.
(function () {
    'use strict';

    const table = document.querySelector('[data-telegram-files]');
    if (!table) return;

    const INTERVAL = 4000;
    // A transfer that is not moving is usually a worker that is not running,
    // and hammering the server will not start it.
    const MAX_IDLE_POLLS = 15;

    let idlePolls = 0;
    let timer = null;

    function cell(id) {
        return table.querySelector('[data-telegram-row="' + id + '"]');
    }

    function paint(row) {
        const target = cell(row.id);
        if (!target) return false;

        const label = target.querySelector('[data-telegram-label]');
        if (label && label.textContent !== row.label) {
            label.textContent = row.label;
        }

        const percent = target.querySelector('[data-telegram-percent]');
        const fill = target.querySelector('[data-telegram-fill]');
        if (!percent || !fill) {
            // The row finished and now renders without a bar. A reload is the
            // honest way to show its final state rather than faking one here.
            return true;
        }

        // Reuse whatever sign the page already rendered, so this does
        // not have to know the language.
        percent.textContent = row.percent + (percent.dataset.sign || '%');
        fill.style.width = row.percent + '%';
        return false;
    }

    function poll() {
        fetch(window.location.pathname + '?progress=1', {
            headers: {'X-Requested-With': 'XMLHttpRequest'},
            credentials: 'same-origin',
        })
            .then(function (response) {
                if (!response.ok) throw new Error(response.status);
                return response.json();
            })
            .then(function (data) {
                let needsReload = false;
                let moved = false;

                (data.files || []).forEach(function (row) {
                    const target = cell(row.id);
                    if (!target) return;
                    const before = (target.querySelector('[data-telegram-fill]') || {}).style;
                    const width = before ? before.width : null;
                    if (paint(row)) needsReload = true;
                    const after = (target.querySelector('[data-telegram-fill]') || {}).style;
                    if (width !== null && after && after.width !== width) moved = true;
                });

                if (needsReload) {
                    window.location.reload();
                    return;
                }

                idlePolls = moved ? 0 : idlePolls + 1;
                if (data.busy && idlePolls < MAX_IDLE_POLLS) {
                    timer = window.setTimeout(poll, INTERVAL);
                }
            })
            .catch(function () {
                // A failed poll is not worth telling the user about: the
                // numbers on the page are still the last ones the server sent.
            });
    }

    if (table.querySelector('[data-telegram-fill]')) {
        timer = window.setTimeout(poll, INTERVAL);
    }

    // A hidden tab does not need updating, and browsers throttle its timers
    // into a burst of requests the moment it comes back.
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            window.clearTimeout(timer);
        } else if (table.querySelector('[data-telegram-fill]')) {
            idlePolls = 0;
            timer = window.setTimeout(poll, INTERVAL);
        }
    });
})();
