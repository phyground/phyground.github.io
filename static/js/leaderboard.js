// Leaderboard: filter selects, sortable column headers, expandable rows,
// and shareable URL query params (?evaluator=…&dataset=…&subset=…&schema=…).
// Pure vanilla JS, no dependencies.
(function () {
    'use strict';
    var table = document.getElementById('lb-table');
    var form = document.getElementById('lb-filters');
    if (!table || !form) return;

    var rows = Array.prototype.slice.call(table.tBodies[0].rows);
    var dataRows = rows.filter(function (r) { return r.classList.contains('lb-row'); });

    // Populate filter selects from the data.
    var FIELDS = ['evaluator', 'dataset', 'subset', 'schema'];
    FIELDS.forEach(function (field) {
        var sel = form.querySelector('#f-' + field);
        if (!sel) return;
        var values = {};
        dataRows.forEach(function (r) { values[r.dataset[field] || ''] = true; });
        delete values['']; // we already have an "all" option
        Object.keys(values).sort().forEach(function (v) {
            var opt = document.createElement('option');
            opt.value = v; opt.textContent = v;
            sel.appendChild(opt);
        });
    });

    // Read filters from URL.
    var params = new URLSearchParams(window.location.search);
    FIELDS.forEach(function (field) {
        var sel = form.querySelector('#f-' + field);
        if (sel && params.has(field)) sel.value = params.get(field);
    });

    function applyFilters() {
        var filters = {};
        FIELDS.forEach(function (field) {
            var sel = form.querySelector('#f-' + field);
            if (sel) filters[field] = sel.value;
        });
        // Update URL (replaceState so we don't pile history entries).
        var p = new URLSearchParams();
        FIELDS.forEach(function (field) {
            if (filters[field]) p.set(field, filters[field]);
        });
        var qs = p.toString();
        var newUrl = window.location.pathname + (qs ? '?' + qs : '');
        window.history.replaceState(null, '', newUrl);
        // Filter rows. Track user expand state on row.dataset.expanded so it
        // survives a filter-out → filter-in round trip.
        dataRows.forEach(function (r) {
            var keep = FIELDS.every(function (field) {
                return !filters[field] || r.dataset[field] === filters[field];
            });
            r.classList.toggle('hidden', !keep);
            var detail = r.nextElementSibling;
            if (detail && detail.classList.contains('lb-detail')) {
                detail.hidden = !keep || r.dataset.expanded !== '1';
            }
        });
    }
    form.addEventListener('change', applyFilters);
    applyFilters();

    // Sortable columns. data-type="num" → numeric sort via data-val; "str" → text.
    var ths = table.tHead.rows[0].cells;
    var currentSort = { col: null, dir: 1 };
    function getCellValue(row, idx, type) {
        var c = row.cells[idx];
        if (!c) return type === 'num' ? -Infinity : '';
        var val = c.getAttribute('data-val');
        if (val === null) val = c.textContent;
        if (type === 'num') {
            var n = parseFloat(val);
            return isNaN(n) ? -Infinity : n;
        }
        return val.toLowerCase();
    }
    function sortBy(idx, type) {
        var dir = (currentSort.col === idx) ? -currentSort.dir : 1;
        currentSort = { col: idx, dir: dir };
        var pairs = [];
        dataRows.forEach(function (r) {
            var detail = r.nextElementSibling;
            pairs.push({
                row: r,
                detail: (detail && detail.classList.contains('lb-detail')) ? detail : null,
                key: getCellValue(r, idx, type),
            });
        });
        pairs.sort(function (a, b) {
            if (a.key < b.key) return -dir;
            if (a.key > b.key) return dir;
            return 0;
        });
        var tbody = table.tBodies[0];
        var frag = document.createDocumentFragment();
        pairs.forEach(function (p) {
            frag.appendChild(p.row);
            if (p.detail) frag.appendChild(p.detail);
        });
        tbody.appendChild(frag);
        // aria-sort indicator.
        for (var i = 0; i < ths.length; i++) ths[i].removeAttribute('aria-sort');
        ths[idx].setAttribute('aria-sort', dir === 1 ? 'ascending' : 'descending');
    }
    for (var i = 0; i < ths.length; i++) {
        (function (idx) {
            var th = ths[idx];
            var type = th.getAttribute('data-type');
            if (!th.hasAttribute('data-sort')) return;
            th.addEventListener('click', function () { sortBy(idx, type || 'str'); });
        })(i);
    }

    // Expand rows. Persist state on row.dataset.expanded so applyFilters can
    // restore it after a filter-out → filter-in cycle.
    table.addEventListener('click', function (ev) {
        var btn = ev.target.closest('.lb-expand');
        if (!btn) return;
        var row = btn.closest('tr.lb-row');
        if (!row) return;
        var detail = row.nextElementSibling;
        if (!detail || !detail.classList.contains('lb-detail')) return;
        var newOpen = row.dataset.expanded !== '1';
        row.dataset.expanded = newOpen ? '1' : '0';
        detail.hidden = !newOpen;
        btn.setAttribute('aria-expanded', String(newOpen));
        btn.textContent = newOpen ? '−' : ('+' + (row.dataset.historyLen || '?'));
    });
})();
