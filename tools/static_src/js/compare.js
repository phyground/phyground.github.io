// Same-prompt comparison page: read prompts_index from inline JSON and render
// the model-by-model comparison for ?prompt_id=<id>. Pure vanilla JS.
(function () {
    'use strict';
    var promptsEl = document.getElementById('prompts-data');
    var paperdemoEl = document.getElementById('paperdemo-data');
    if (!promptsEl) return;

    var prompts = {};
    var paperdemo = [];
    try { prompts = JSON.parse(promptsEl.textContent); } catch (e) { prompts = {}; }
    try { paperdemo = JSON.parse(paperdemoEl.textContent); } catch (e) { paperdemo = []; }

    // Build a paperdemo lookup: src_filename stem → list of {law, model, video_url_hf}.
    var pdByStem = {};
    paperdemo.forEach(function (lawEntry) {
        (lawEntry.videos || []).forEach(function (v) {
            var stem = (v.src_filename || '').replace(/\.[^/.]+$/, '');
            if (!stem) return;
            if (!pdByStem[stem]) pdByStem[stem] = [];
            pdByStem[stem].push({
                law: lawEntry.law,
                model: v.model,
                video_url_hf: v.video_url_hf,
                n_ann: v.n_ann,
                src_filename: v.src_filename,
            });
        });
    });

    // Populate the picker.
    var picker = document.getElementById('prompt-picker');
    var ids = Object.keys(prompts).sort();
    ids.forEach(function (id) {
        var p = prompts[id];
        var opt = document.createElement('option');
        opt.value = id;
        var laws = (p.physical_laws || []).join(',');
        opt.textContent = id + (laws ? ' [' + laws + ']' : '');
        picker.appendChild(opt);
    });

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function render(id) {
        var view = document.getElementById('compare-view');
        var empty = document.getElementById('compare-empty');
        if (!id || !prompts[id]) {
            view.hidden = true;
            empty.hidden = false;
            return;
        }
        var p = prompts[id];
        document.getElementById('compare-title').textContent = id;
        var meta = [];
        if (p.dataset) meta.push('dataset: ' + p.dataset);
        if (p.physical_laws && p.physical_laws.length) meta.push('laws: ' + p.physical_laws.join(', '));
        if (p.difficulty && typeof p.difficulty.phys_micro_avg === 'number') {
            meta.push('phys_micro_avg: ' + p.difficulty.phys_micro_avg.toFixed(2));
        }
        document.getElementById('compare-meta').textContent = meta.join(' · ');
        document.getElementById('compare-prompt-text').textContent = p.prompt || '';

        var grid = document.getElementById('compare-videos');
        grid.innerHTML = '';
        // Score cards from per_model_scores.
        var pms = p.per_model_scores || {};
        var modelKeys = Object.keys(pms).sort();
        // If we have a paperdemo video for this prompt, prepend it.
        var pdVids = pdByStem[id] || [];

        var firstFrame = p.first_frame_url || null;
        var perModelVideos = p.per_model_videos || {};

        // OpenVid real-video context (YouTube, caption, expected outcomes).
        var rvBlock = document.getElementById('compare-realvideo');
        if (rvBlock) rvBlock.remove();
        if (p.realvideo) {
            var rv = p.realvideo;
            var block = document.createElement('section');
            block.id = 'compare-realvideo';
            block.style.margin = '1rem 0';
            var html = '<h3 style="margin-bottom:0.4rem;">Real-video context (OpenVid)</h3>';
            if (rv.youtube_url) {
                html += '<p style="margin-bottom:0.4rem;">YouTube source: <a href="' + escapeHtml(rv.youtube_url) + '" target="_blank">'
                     + escapeHtml(rv.youtube_id || rv.youtube_url) + '</a>';
                if (rv.time_range) {
                    html += ' &middot; segment ' + rv.time_range.start_s + 's&ndash;' + rv.time_range.end_s + 's';
                }
                html += '</p>';
            }
            if (rv.caption) html += '<p class="caveat" style="margin-bottom:0.4rem;"><strong>Caption:</strong> ' + escapeHtml(rv.caption) + '</p>';
            if (rv.expected_outcome && rv.expected_outcome.length) {
                html += '<details style="margin-top:0.4rem;"><summary>Expected outcomes (' + rv.expected_outcome.length + ')</summary><ul>';
                rv.expected_outcome.forEach(function (e) {
                    html += '<li>' + escapeHtml(e) + '</li>';
                });
                html += '</ul></details>';
            }
            block.innerHTML = html;
            view.insertBefore(block, document.getElementById('compare-videos'));
        }

        function card(opts) {
            var model = opts.model;
            var score = opts.score;
            var videoUrl = opts.videoUrl;
            var extra = opts.extra;
            var posterUrl = opts.posterUrl;
            var fig = document.createElement('figure');
            fig.className = 'video-card';
            var inner = '';
            if (videoUrl) {
                inner += '<video src="' + escapeHtml(videoUrl) + '" controls muted loop preload="metadata"';
                if (posterUrl) inner += ' poster="' + escapeHtml(posterUrl) + '"';
                inner += '></video>';
            } else if (posterUrl) {
                inner += '<img src="' + escapeHtml(posterUrl) + '" alt="first frame" style="width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:var(--gray-light);">';
            } else {
                inner += '<div class="placeholder" style="aspect-ratio:16/9;margin:0;border:none;background:var(--gray-light);">no video</div>';
            }
            inner += '<figcaption>';
            inner += '<span class="model">' + escapeHtml(model) + '</span>';
            inner += '<span class="meta">';
            if (typeof score === 'number') inner += 'phys_avg = ' + score.toFixed(2);
            if (extra) { if (typeof score === 'number') inner += ' · '; inner += escapeHtml(extra); }
            inner += '</span>';
            inner += '</figcaption>';
            fig.innerHTML = inner;
            return fig;
        }

        // Batch card inserts so #compare-videos reflows once.
        var frag = document.createDocumentFragment();

        // Reference card: prompt's first_frame as a "real source" tile.
        if (firstFrame) {
            frag.appendChild(card({ model: 'first frame', extra: 'reference', posterUrl: firstFrame }));
        }

        // Find a paperdemo video URL for a given model.
        function pdUrlFor(model) {
            var hit = pdVids.find(function (v) { return v.model === model; });
            return hit ? hit.video_url_hf : null;
        }

        modelKeys.forEach(function (m) {
            // Prefer paperdemo URL when this prompt is curated; otherwise use the
            // per-(model,prompt) HF URL the snapshot precomputed.
            var url = pdUrlFor(m) || perModelVideos[m] || null;
            frag.appendChild(card({ model: m, score: pms[m], videoUrl: url, posterUrl: firstFrame }));
        });

        // Any paperdemo videos for models not in per_model_scores: show too.
        pdVids.forEach(function (pv) {
            if (modelKeys.indexOf(pv.model) === -1) {
                frag.appendChild(card({
                    model: pv.model,
                    videoUrl: pv.video_url_hf,
                    extra: 'paperdemo',
                    posterUrl: firstFrame,
                }));
            }
        });
        grid.appendChild(frag);

        view.hidden = false;
        empty.hidden = true;
    }

    picker.addEventListener('change', function () {
        var id = picker.value;
        var p = new URLSearchParams(window.location.search);
        if (id) p.set('prompt_id', id); else p.delete('prompt_id');
        var qs = p.toString();
        window.history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : ''));
        render(id);
    });

    var initial = new URLSearchParams(window.location.search).get('prompt_id') || '';
    if (initial && prompts[initial]) {
        picker.value = initial;
    }
    render(initial);
})();
