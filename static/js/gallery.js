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

    // Honour ?law=<x> AND ?model=<key> on first load so deep-links from the
    // home Featured Comparison and per-row leaderboard "Videos" buttons land
    // on the right pane + section.
    var params = new URLSearchParams(window.location.search);
    var lawParam = params.get('law');
    var modelParam = params.get('model');
    if (modelParam) {
        show('by-model');
        var mbtn = document.querySelector('#model-nav .gallery-nav-item[data-model="' + modelParam + '"]');
        if (mbtn) mbtn.click();
    } else if (lawParam) {
        show('by-law');
        var btn = document.querySelector('#law-nav .gallery-nav-item[data-law="' + lawParam + '"]');
        if (btn) btn.click();
    }

    // ── Card → modal (by-law pane only) ─────────────────────
    var promptsEl = document.getElementById('prompts-data');
    var prompts = {};
    if (promptsEl) {
        try { prompts = JSON.parse(promptsEl.textContent); } catch (e) { prompts = {}; }
    }
    var modal = document.getElementById('card-modal');
    if (!modal) return;
    var modalTitle = document.getElementById('modal-title');
    var modalMeta = document.getElementById('modal-meta');
    var modalPrompt = document.getElementById('modal-prompt');
    var modalScores = document.getElementById('modal-scores');
    var modalFrameWrap = document.getElementById('modal-frame-wrap');
    var modalFrame = document.getElementById('modal-frame');
    var modalLink = document.getElementById('modal-link');
    var closeBtn = modal.querySelector('.modal-close');

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function openModal(card) {
        var pid = card.dataset.promptId;
        var model = card.dataset.model;
        var law = card.dataset.law;
        var p = prompts[pid] || null;

        modalTitle.textContent = (p && p.prompt_id) || pid || 'Video detail';
        var metaParts = [];
        if (model) metaParts.push('model: ' + model);
        if (law) metaParts.push('law: ' + law.replace(/_/g, ' '));
        if (card.dataset.nAnn) metaParts.push('n_ann: ' + card.dataset.nAnn);
        if (p && p.dataset) metaParts.push('source: ' + p.dataset);
        if (p && p.physical_laws && p.physical_laws.length) metaParts.push('laws: ' + p.physical_laws.join(', '));
        modalMeta.textContent = metaParts.join(' · ');

        modalPrompt.textContent = (p && p.prompt) || '(no prompt text in snapshot for this video)';

        if (p && p.first_frame_url) {
            modalFrame.src = p.first_frame_url;
            modalFrameWrap.style.display = '';
        } else {
            modalFrameWrap.style.display = 'none';
        }

        if (p && p.per_model_scores && Object.keys(p.per_model_scores).length) {
            var rows = Object.keys(p.per_model_scores).sort().map(function (m) {
                var v = p.per_model_scores[m];
                return '<tr><td>' + escapeHtml(m) + '</td>'
                     + '<td class="num">' + (typeof v === 'number' ? v.toFixed(2) : escapeHtml(v)) + '</td></tr>';
            }).join('');
            modalScores.innerHTML = '<h3>Per-model phys score</h3><table class="lb"><thead><tr><th>Model</th><th class="num">phys_avg</th></tr></thead><tbody>' + rows + '</tbody></table>';
        } else {
            modalScores.innerHTML = '<p style="color:var(--gray-dark);">No per-model scores in snapshot for this prompt.</p>';
        }

        var compareHref = '../compare/?prompt_id=' + encodeURIComponent(pid || '');
        modalLink.innerHTML = pid
            ? 'Open <a href="' + escapeHtml(compareHref) + '">compare view for ' + escapeHtml(pid) + '</a>.'
            : '';

        if (typeof modal.showModal === 'function') modal.showModal();
        else modal.setAttribute('open', '');
    }
    function closeModal() {
        if (typeof modal.close === 'function') modal.close();
        else modal.removeAttribute('open');
    }
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', function (ev) {
        if (ev.target === modal) closeModal();
    });

    document.querySelectorAll('.video-card-clickable .video-card-info').forEach(function (btn) {
        btn.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var card = btn.closest('.video-card-clickable');
            if (card) openModal(card);
        });
    });
})();
