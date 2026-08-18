// Live preview of the amount that will arrive.
//
// The fee comes out of the requested amount. Without this, someone asks for
// $10, receives $9, and opens a ticket. The rules table is further down the
// page, so nothing is lost if the script does not load.
(function () {
    'use strict';

    const box = document.querySelector('[data-net-preview]');
    if (!box) return;

    const amountInput = document.querySelector('#id_withdrawal_amount');
    const accountSelect = document.querySelector('#id_account');
    if (!amountInput || !accountSelect) return;

    let rules = {};
    try {
        rules = JSON.parse(box.dataset.rules || '{}');
    } catch (error) {
        return;
    }

    const labels = {
        fee: box.dataset.labelFee || '',
        net: box.dataset.labelNet || '',
        min: box.dataset.labelMin || '',
    };

    const feeCell = box.querySelector('[data-net-fee]');
    const netCell = box.querySelector('[data-net-amount]');
    const noteCell = box.querySelector('[data-net-note]');

    function methodOf(optionValue) {
        // The select value is a destination id; each option carries its
        // method in data-method.
        const option = accountSelect.querySelector('option[value="' + optionValue + '"]');
        return option ? option.dataset.method : '';
    }

    function money(cents) {
        return '$' + (cents / 100).toFixed(2);
    }

    function update() {
        const rule = rules[methodOf(accountSelect.value)];
        if (!rule) {
            box.hidden = true;
            return;
        }

        const dollars = parseFloat(amountInput.value);
        if (!isFinite(dollars) || dollars <= 0) {
            box.hidden = true;
            return;
        }

        const gross = Math.round(dollars * 100);
        box.hidden = false;
        feeCell.textContent = rule.fee ? money(rule.fee) : '—';

        if (gross < rule.min) {
            netCell.textContent = '—';
            noteCell.textContent = labels.min.replace('%s', money(rule.min));
            noteCell.hidden = false;
            return;
        }

        netCell.textContent = money(Math.max(0, gross - rule.fee));
        noteCell.hidden = true;
    }

    amountInput.addEventListener('input', update);
    accountSelect.addEventListener('change', update);
    update();
})();
