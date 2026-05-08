// Video Gallery: by-law / by-model tab switch + nav-item activation.
(function () {
    'use strict';
    var tabs = document.querySelectorAll('.gallery-tab');
    if (!tabs.length) return;

    function show(mode) {
        tabs.forEach(function (t) {
            var on = t.dataset.mode === mode;
            t.classList.toggle('active', on);
            t.setAttribute('aria-selected', String(on));
        });
        document.getElementById('pane-by-law').hidden = mode !== 'by-law';
        document.getElementById('pane-by-model').hidden = mode !== 'by-model';
    }
    tabs.forEach(function (t) {
        t.addEventListener('click', function () { show(t.dataset.mode); });
    });

    // Nav within each pane: clicking a law/model button reveals its section.
    function wirePane(navId, contentId, attr) {
        var nav = document.getElementById(navId);
        var content = document.getElementById(contentId);
        if (!nav || !content) return;
        var items = nav.querySelectorAll('.gallery-nav-item');
        var sections = content.querySelectorAll('.gallery-section');
        // Mark first item active by default.
        if (items[0]) items[0].classList.add('active');
        items.forEach(function (item) {
            item.addEventListener('click', function () {
                var key = item.dataset[attr];
                items.forEach(function (i) { i.classList.toggle('active', i === item); });
                sections.forEach(function (s) { s.hidden = s.dataset[attr] !== key; });
            });
        });
    }
    wirePane('law-nav', 'law-content', 'law');
    wirePane('model-nav', 'model-content', 'model');

    // Honour ?law=<x> on first load so home's "All <law> videos →" link works.
    var params = new URLSearchParams(window.location.search);
    var lawParam = params.get('law');
    if (lawParam) {
        show('by-law');
        var btn = document.querySelector('#law-nav .gallery-nav-item[data-law="' + lawParam + '"]');
        if (btn) btn.click();
    }
})();
