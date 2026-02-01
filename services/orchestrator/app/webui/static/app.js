(function () {
  function getBodyAttr(name) {
    try {
      return (document.body && document.body.getAttribute(name)) || '';
    } catch (e) {
      return '';
    }
  }

  var COPY_LABEL = getBodyAttr('data-copy-label') || 'Copy';
  var COPIED_LABEL = getBodyAttr('data-copied-label') || 'Copied';
  var COPY_FAILED = getBodyAttr('data-copy-failed') || 'Copy failed';

  // Client-side error reporting (writes to data/logs/desktop-client.log)
  var _owbLastClientErrKey = '';
  var _owbLastClientErrAt = 0;

  function _postClientLog(payload) {
    try {
      var token = getBodyAttr('data-admin-token') || '';
      if (!token) return;
      var r = new XMLHttpRequest();
      r.open('POST', '/api/client_log?token=' + encodeURIComponent(token), true);
      r.timeout = 2500;
      r.setRequestHeader('Content-Type', 'application/json');
      r.send(JSON.stringify(payload || {}));
    } catch (e) {}
  }

  function _reportClientError(level, message, stack, extra) {
    try {
      var now = Date.now ? Date.now() : (new Date().getTime());
      var key = String(level || '') + '|' + String(message || '') + '|' + String(stack || '');
      if (key && key === _owbLastClientErrKey && (now - _owbLastClientErrAt) < 1200) return;
      _owbLastClientErrKey = key;
      _owbLastClientErrAt = now;
      _postClientLog({
        level: String(level || 'error'),
        message: String(message || ''),
        url: (location && location.href) ? String(location.href) : '',
        stack: String(stack || ''),
        user_agent: (navigator && navigator.userAgent) ? String(navigator.userAgent) : '',
        ts_ms: now,
        extra: extra || {}
      });
    } catch (e2) {}
  }

  try {
    window.addEventListener('error', function (ev) {
      try {
        var msg = ev && ev.message ? String(ev.message) : 'window.error';
        var stack = '';
        try { stack = (ev.error && ev.error.stack) ? String(ev.error.stack) : ''; } catch (e0) { stack = ''; }
        _reportClientError('error', msg, stack, {
          filename: ev && ev.filename ? String(ev.filename) : '',
          lineno: ev && ev.lineno ? Number(ev.lineno) : 0,
          colno: ev && ev.colno ? Number(ev.colno) : 0
        });
      } catch (e1) {}
    }, true);
  } catch (e2) {}

  try {
    window.addEventListener('unhandledrejection', function (ev) {
      try {
        var reason = ev && ev.reason != null ? ev.reason : null;
        var msg = '';
        var stack = '';
        if (reason && typeof reason === 'object') {
          try { msg = String(reason.message || reason.name || 'unhandledrejection'); } catch (e0) { msg = 'unhandledrejection'; }
          try { stack = String(reason.stack || ''); } catch (e1) { stack = ''; }
        } else {
          msg = String(reason || 'unhandledrejection');
        }
        _reportClientError('error', msg, stack, { kind: 'unhandledrejection' });
      } catch (e2) {}
    });
  } catch (e3) {}

  function _copyFallback(text) {
    return new Promise(function (resolve, reject) {
      try {
        var ta = document.createElement('textarea');
        ta.value = String(text || '');
        ta.setAttribute('readonly', 'true');
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        ta.style.top = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, ta.value.length);
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
        try { ta.remove(); } catch (e2) { document.body.removeChild(ta); }
        if (ok) resolve();
        else reject(new Error('copy_failed'));
      } catch (e3) {
        reject(e3);
      }
    });
  }

  function copyText(text) {
    var s = String(text || '');
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(s).catch(function () { return _copyFallback(s); });
      }
    } catch (e) {}
    return _copyFallback(s);
  }

  function stripCitationMarkers(text) {
    var s = String(text || '');
    // Internal validation markers rendered as hover footnotes in the UI.
    s = s.replace(/\[chunk:[A-Za-z0-9_-]+\]/g, '');
    // Cleanup spacing after stripping.
    s = s.replace(/[ \t]{2,}/g, ' ');
    s = s.replace(/ +\n/g, '\n');
    return s;
  }

  function _flash(btn, ok) {
    if (!btn) return;
    var orig = btn.getAttribute('data-copy-orig');
    if (!orig) {
      orig = btn.textContent || '';
      btn.setAttribute('data-copy-orig', orig);
    }
    if (ok) btn.classList.add('copied');
    btn.textContent = ok ? '✓' : '×';
    btn.title = ok ? COPIED_LABEL : COPY_FAILED;
    setTimeout(function () {
      try { btn.classList.remove('copied'); } catch (e) {}
      btn.textContent = orig;
      btn.title = COPY_LABEL;
    }, 900);
  }

  function _closest(el, selector) {
    var cur = el;
    while (cur && cur !== document.body) {
      try {
        if (cur.matches && cur.matches(selector)) return cur;
      } catch (e) {}
      cur = cur.parentNode;
    }
    return null;
  }

  function _textFromTarget(selector) {
    if (!selector) return '';
    var el = null;
    try { el = document.querySelector(selector); } catch (e) { el = null; }
    if (!el) return '';
    try {
      if (typeof el.value === 'string') return el.value || '';
    } catch (e2) {}
    try { return el.textContent || ''; } catch (e3) { return ''; }
  }

  function enhancePreCopy() {
    var pres = document.querySelectorAll('pre.pre');
    if (!pres || !pres.length) return;
    for (var i = 0; i < pres.length; i++) {
      var pre = pres[i];
      try {
        if (pre.querySelector && pre.querySelector('button.copy-btn')) continue;
      } catch (e) {}
      pre.classList.add('copyable');
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'copy-btn copy-corner';
      btn.title = COPY_LABEL;
      btn.setAttribute('aria-label', COPY_LABEL);
      btn.textContent = '⧉';
      try { btn._owbCopyText = null; } catch (e0) {}
      try { btn.setAttribute('data-copy-target', '#' + _ensureId(pre)); } catch (e1) {}
      pre.appendChild(btn);
    }
  }

  var _owbFieldIdSeq = 0;

  function _ensureId(el) {
    try {
      if (el && el.id) return el.id;
    } catch (e) {}
    _owbFieldIdSeq += 1;
    var id = 'owb-field-' + String(_owbFieldIdSeq);
    try { el.id = id; } catch (e2) {}
    try { if (el && el.id) return el.id; } catch (e3) {}
    return id;
  }

  function _isBadInputType(t) {
    var tt = String(t || '').toLowerCase();
    return (
      tt === 'hidden' ||
      tt === 'checkbox' ||
      tt === 'radio' ||
      tt === 'file' ||
      tt === 'submit' ||
      tt === 'button' ||
      tt === 'reset'
    );
  }

  function enhanceFieldCopy() {
    var fields = document.querySelectorAll('textarea, input, select');
    if (!fields || !fields.length) return;
    for (var i = 0; i < fields.length; i++) {
      var el = fields[i];
      if (!el) continue;
      try {
        if (
          (el.getAttribute && (el.getAttribute('data-no-copy') === '1' || el.getAttribute('data-owb-no-copy') === '1')) ||
          (el.id && String(el.id) === 'chat-input')
        ) {
          continue;
        }
      } catch (eNo) {}
      if (el.tagName && String(el.tagName).toLowerCase() === 'input') {
        var t = '';
        try { t = el.getAttribute('type') || ''; } catch (e0) { t = ''; }
        if (_isBadInputType(t)) continue;
      }

      var p = null;
      try { p = el.parentNode; } catch (e1) { p = null; }
      if (p && p.classList && p.classList.contains('copy-field')) continue;

      var wrap = document.createElement('div');
      wrap.className = 'copy-field copyable';
      try {
        if (p) p.insertBefore(wrap, el);
        wrap.appendChild(el);
      } catch (e2) {
        continue;
      }

      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'copy-btn copy-corner';
      btn.textContent = '⧉';
      btn.title = COPY_LABEL;
      btn.setAttribute('aria-label', COPY_LABEL);
      btn.setAttribute('data-copy-target', '#' + _ensureId(el));
      wrap.appendChild(btn);
    }
  }

  function enhanceStatusCopy() {
    var els = document.querySelectorAll('[id$=\"-status\"]');
    if (!els || !els.length) return;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (!el || !el.id) continue;
      try {
        if (el.getAttribute && (el.getAttribute('data-no-copy') === '1' || el.getAttribute('data-owb-no-copy') === '1')) continue;
      } catch (eNo) {}
      try {
        if (el.getAttribute('data-owb-copy-enhanced') === '1') continue;
        el.setAttribute('data-owb-copy-enhanced', '1');
      } catch (e0) {}

      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'copy-btn copy-inline';
      btn.textContent = '⧉';
      btn.title = COPY_LABEL;
      btn.setAttribute('aria-label', COPY_LABEL);
      btn.setAttribute('data-copy-target', '#' + String(el.id));

      try {
        if (el.insertAdjacentElement) el.insertAdjacentElement('afterend', btn);
        else if (el.parentNode) el.parentNode.insertBefore(btn, el.nextSibling);
      } catch (e1) {}
    }
  }

  document.addEventListener('click', function (e) {
    var btn = _closest(e && e.target, 'button.copy-btn');
    if (!btn) return;
    try { if (e && e.preventDefault) e.preventDefault(); } catch (e0) {}
    try { if (e && e.stopPropagation) e.stopPropagation(); } catch (e1) {}

    var text = '';
    try {
      if (btn._owbCopyText != null) {
        text = String(btn._owbCopyText);
      } else {
        var direct = btn.getAttribute('data-copy-text');
        if (direct != null) text = String(direct);
        else text = _textFromTarget(btn.getAttribute('data-copy-target') || '');
      }
    } catch (e2) {
      text = '';
    }

    try { text = stripCitationMarkers(text); } catch (eStrip) {}
    copyText(text).then(function () { _flash(btn, true); }).catch(function () { _flash(btn, false); });
  });

  try { enhancePreCopy(); } catch (e) {}
  try { enhanceFieldCopy(); } catch (e2) {}
  try { enhanceStatusCopy(); } catch (e3) {}

  // Expose a tiny helper for dynamically-rendered items (chat/timeline).
  window.__owbCopy = { label: COPY_LABEL, strip: stripCitationMarkers };
})();

(function () {
  var root = document.getElementById('task-root');
  if (!root) return;

  var taskId = root.getAttribute('data-task-id');
  var adminToken = root.getAttribute('data-admin-token') || (document.body ? document.body.getAttribute('data-admin-token') : '') || '';
  var timeline = document.getElementById('timeline');
  var chat = document.getElementById('chat');
  var wbThread = document.getElementById('wb-thread');
  var chatForm = document.getElementById('chat-form');
  var chatInput = document.getElementById('chat-input');
  var chatSend = document.getElementById('chat-send');
  var chatStatus = document.getElementById('chat-status');
  var taskStatusEl = document.querySelector('[data-task-status]');
  var taskUpdatedEl = document.querySelector('[data-task-updated]');
  var cancelForm = document.getElementById('task-cancel-form');
  var cancelBtn = document.getElementById('task-cancel');
  var cancelStatus = document.getElementById('task-cancel-status');
  var netStatusEl = document.getElementById('net-status');

  var netFailCount = 0;
  var netLastMsg = '';
  var netLastUpdateMs = 0;

  // Citation evidence index (chunk_id -> info), lazy-loaded for hover tooltips.
  var citeIndex = null;
  var citeInFlight = false;
  var citeLastFetchMs = 0;
  var citeWarnings = null;

  // Right panel (files + preview/editor)
  var wbGrid = document.getElementById('wb-grid');
  var wbRightToggle = document.getElementById('wb-right-toggle');
  var wbSplitter = document.getElementById('wb-splitter');
  var wbFileSelect = document.getElementById('wb-file-select');
  var wbFileOpenBtn = document.getElementById('wb-file-open');
  var wbFileRevealBtn = document.getElementById('wb-file-reveal');
  var wbFileDownload = document.getElementById('wb-file-download');
  var wbPptSaveBtn = document.getElementById('wb-ppt-save');
  var wbFileHint = document.getElementById('wb-file-hint');
  var wbViewer = document.getElementById('wb-viewer');
  var wbCiteWarnings = document.getElementById('wb-cite-warnings');
  var wbCiteWarningsBody = document.getElementById('wb-cite-warnings-body');

  var filesAutoExpand = true;
  var filesInFlight = false;
  var filesLastFetchMs = 0;
  var fileIndex = {};
  var currentFileId = '';
  var currentFileKind = '';
  var currentFileMetaSig = '';
  var webUrl = '';
  var pptState = null;

  // Pending approval (chat-based)
  var pendingApproval = null; // { stepId, tool, scope }

  function _isZh() {
    try { return (document.documentElement && String(document.documentElement.lang || '').indexOf('zh') === 0); } catch (e) { return false; }
  }

  function _setPendingApproval(stepId, tool, scope) {
    var sid = String(stepId || '').replace(/^\s+|\s+$/g, '');
    if (!sid) return;
    pendingApproval = {
      stepId: sid,
      tool: String(tool || '').replace(/^\s+|\s+$/g, ''),
      scope: String(scope || '').replace(/^\s+|\s+$/g, '')
    };
  }

  function _clearPendingApproval() {
    pendingApproval = null;
  }

  function _isSidebarCollapsed() {
    try { return !!(wbGrid && wbGrid.classList && wbGrid.classList.contains('right-collapsed')); } catch (e) { return false; }
  }

  function _setSidebarCollapsed(collapsed) {
    if (!wbGrid || !wbGrid.classList) return;
    try {
      if (collapsed) wbGrid.classList.add('right-collapsed');
      else wbGrid.classList.remove('right-collapsed');
    } catch (e) {}
  }

  if (wbRightToggle) {
    wbRightToggle.addEventListener('click', function () {
      filesAutoExpand = false;
      _setSidebarCollapsed(!_isSidebarCollapsed());
    });
  }

  // Chat/preview splitter (persisted)
  (function () {
    if (!wbGrid || !wbSplitter) return;
    var KEY = 'owb.wbLeftWidth';
    function _get() {
      try {
        var n = parseInt((window.localStorage && window.localStorage.getItem(KEY)) || '', 10);
        return isNaN(n) ? 0 : n;
      } catch (e) {
        return 0;
      }
    }
    function _apply(px) {
      try { wbGrid.style.setProperty('--owb-wb-left-w', String(px) + 'px'); } catch (e) {}
    }
    var saved = _get();
    if (saved) _apply(saved);

    var dragging = false;
    var startX = 0;
    var startW = 0;
    var lastW = 0;
    var downEvt = (window.PointerEvent ? 'pointerdown' : 'mousedown');
    var moveEvt = (window.PointerEvent ? 'pointermove' : 'mousemove');
    var upEvt = (window.PointerEvent ? 'pointerup' : 'mouseup');
    var cancelEvt = (window.PointerEvent ? 'pointercancel' : 'mouseleave');

    function clamp(w) {
      var min = 360;
      var max = 0;
      try {
        var total = wbGrid.getBoundingClientRect().width || 0;
        max = Math.max(min + 80, total - 320);
      } catch (e) { max = 0; }
      if (!max || isNaN(max)) max = 920;
      return Math.max(min, Math.min(max, w));
    }

    function onMove(e) {
      if (!dragging) return;
      var dx = 0;
      try { dx = Number((e && e.clientX) || 0) - startX; } catch (e0) { dx = 0; }
      var w = clamp(startW + dx);
      lastW = w;
      _apply(w);
    }
    function stop() {
      if (!dragging) return;
      dragging = false;
      try { wbSplitter.classList.remove('dragging'); } catch (e0) {}
      try {
        if (window.localStorage && lastW) window.localStorage.setItem(KEY, String(parseInt(lastW, 10) || 0));
      } catch (e1) {}
      try { window.removeEventListener(moveEvt, onMove); } catch (e2) {}
      try { window.removeEventListener(upEvt, stop); } catch (e3) {}
      try { window.removeEventListener(cancelEvt, stop); } catch (e4) {}
    }

    wbSplitter.addEventListener(downEvt, function (e) {
      try { if (_isSidebarCollapsed()) return; } catch (e0) {}
      try {
        if (e && e.button != null && e.button !== 0) return;
        if (e && e.preventDefault) e.preventDefault();
      } catch (e1) {}
      dragging = true;
      try { wbSplitter.classList.add('dragging'); } catch (e2) {}
      try { startX = Number((e && e.clientX) || 0); } catch (e3) { startX = 0; }
      try {
        var left = wbGrid.querySelector('.wb2-left');
        startW = left ? (left.getBoundingClientRect().width || 0) : 0;
      } catch (e4) { startW = 0; }
      lastW = startW;
      try { window.addEventListener(moveEvt, onMove); } catch (e5) {}
      try { window.addEventListener(upEvt, stop); } catch (e6) {}
      try { window.addEventListener(cancelEvt, stop); } catch (e7) {}
    });
  })();

  function _clear(el) {
    if (!el) return;
    try { while (el.firstChild) el.removeChild(el.firstChild); } catch (e) {}
  }

  function _setText(el, text) {
    if (!el) return;
    try { el.textContent = String(text || ''); } catch (e) {}
  }

  function _citeLabel() {
    try {
      return (document.body && document.body.getAttribute('data-sources-label')) || 'Sources';
    } catch (e) {
      return 'Sources';
    }
  }

  function _citeStr(attr, fallback) {
    try {
      var v = (document.body && document.body.getAttribute(attr)) || '';
      v = String(v || '').replace(/^\s+|\s+$/g, '');
      return v || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function _citeLoading() { return _citeStr('data-cite-loading', 'Loading\u2026'); }
  function _citeUnverified() { return _citeStr('data-cite-unverified', 'Unverified source.'); }
  function _citeNoMatch() { return _citeStr('data-cite-no-match', 'Unverified source (no matching evidence chunk).'); }
  function _citeChunkNotFound() { return _citeStr('data-cite-chunk-not-found', 'Evidence chunk not found.'); }

  function _renderCiteWarnings(warnings) {
    if (!wbCiteWarnings || !wbCiteWarningsBody) return;
    var list = (warnings && Object.prototype.toString.call(warnings) === '[object Array]') ? warnings : [];
    if (!list.length) {
      try { wbCiteWarnings.style.display = 'none'; } catch (e0) {}
      _clear(wbCiteWarningsBody);
      return;
    }
    try { wbCiteWarnings.style.display = ''; } catch (e1) {}
    _clear(wbCiteWarningsBody);
    for (var i = 0; i < list.length; i++) {
      var w = list[i] || {};
      var unv = [];
      try {
        if (w.unverified && Object.prototype.toString.call(w.unverified) === '[object Array]') unv = w.unverified;
        else if (w.unverified_refs && Object.prototype.toString.call(w.unverified_refs) === '[object Array]') unv = w.unverified_refs;
      } catch (eU) { unv = []; }
      var reasons = [];
      try { if (w.reasons && Object.prototype.toString.call(w.reasons) === '[object Array]') reasons = w.reasons; } catch (eR) { reasons = []; }
      var line = '';
      if (unv && unv.length) {
        var prefix = _citeStr('data-cite-warn-unverified-prefix', 'Unverified references:');
        var shown = unv.slice(0, 3).map(function (s) { return String(s || '').replace(/^\\s+|\\s+$/g, ''); }).filter(Boolean);
        line = prefix + ' ' + shown.join('  |  ');
        if (unv.length > shown.length) line += ' (+' + String(unv.length - shown.length) + ')';
      } else if (reasons && reasons.length) {
        line = _citeStr('data-cite-warn-reasons-prefix', 'Citation check:') + ' ' + reasons.join(', ');
      } else if (w.reason) {
        line = _citeStr('data-cite-warn-reasons-prefix', 'Citation check:') + ' ' + String(w.reason || '');
      } else {
        line = _citeStr('data-cite-warn-reasons-prefix', 'Citation check:') + ' warning';
      }
      var div = document.createElement('div');
      div.className = 'cite-warning-item';
      div.textContent = line;
      wbCiteWarningsBody.appendChild(div);
    }
  }

  function _applyCitePopover(sup) {
    if (!sup) return;
    var chunkId = '';
    try { chunkId = sup.getAttribute('data-chunk-id') || ''; } catch (e0) { chunkId = ''; }
    var pop = null;
    try { pop = sup.querySelector && sup.querySelector('.cite-pop'); } catch (e1) { pop = null; }
    if (!pop) return;
    if (!chunkId) {
      pop.textContent = _citeNoMatch();
      return;
    }
    if (!citeIndex) {
      pop.textContent = _citeLoading();
      return;
    }
    var info = citeIndex[chunkId];
    if (!info) {
      pop.textContent = _citeChunkNotFound();
      return;
    }
    var lines = [];
    if (info.title) lines.push(String(info.title));
    if (info.url) lines.push(String(info.url));
    if (info.snippet) lines.push(String(info.snippet));
    pop.textContent = lines.join('\n');
  }

  function _refreshAllCitePopovers() {
    var nodes = null;
    try { nodes = document.querySelectorAll('.cite[data-chunk-id]'); } catch (e) { nodes = null; }
    if (nodes && nodes.length) {
      for (var i = 0; i < nodes.length; i++) {
        try { _applyCitePopover(nodes[i]); } catch (e2) {}
      }
    }

    // Update auto-generated Sources list rows.
    var rows = null;
    try { rows = document.querySelectorAll('.cite-source-row[data-auto=\"1\"][data-chunk-id]'); } catch (e3) { rows = null; }
    if (!rows || !rows.length) return;
    for (var j = 0; j < rows.length; j++) {
      var row = rows[j];
      if (!row) continue;
      var cid = '';
      try { cid = row.getAttribute('data-chunk-id') || ''; } catch (eCid) { cid = ''; }
      if (!cid) continue;
      var span = null;
      try { span = row.querySelector && row.querySelector('.cite-source-text'); } catch (eSp) { span = null; }
      if (!span) continue;
      if (!citeIndex) { span.textContent = _citeLoading(); continue; }
      var info = citeIndex[cid];
      if (!info) { span.textContent = _citeChunkNotFound(); continue; }
      try {
        var t = String(info.title || info.url || cid || '');
        if (info.url && info.title) t = String(info.title || '') + ' - ' + String(info.url || '');
        span.textContent = t;
      } catch (eTxt) {}
    }
  }

  function _ensureCiteIndex() {
    if (!adminToken) return;
    var now = Date.now ? Date.now() : (new Date().getTime());
    if ((now - citeLastFetchMs) < 4000) return;
    if (citeInFlight) return;
    citeInFlight = true;
    xhr('GET', '/api/tasks/' + encodeURIComponent(taskId) + '/citations?token=' + encodeURIComponent(adminToken), null, 12000, function (status, text) {
      citeInFlight = false;
      citeLastFetchMs = Date.now ? Date.now() : (new Date().getTime());
      if (!(status >= 200 && status < 300)) return;
      var data = null;
      try { data = JSON.parse(text || '{}'); } catch (e) { data = null; }
      if (!data || data.ok !== true) return;
      citeIndex = data.chunks || {};
      citeWarnings = data.warnings || [];
      try { _renderCiteWarnings(citeWarnings); } catch (eW) {}
      try { _refreshAllCitePopovers(); } catch (e2) {}
    });
  }

  function _makeCiteSup(num, chunkId) {
    var sup = document.createElement('sup');
    sup.className = 'cite' + (chunkId ? '' : ' cite-missing');
    try { sup.setAttribute('data-cite-num', String(num)); } catch (e0) {}
    if (chunkId) {
      try { sup.setAttribute('data-chunk-id', String(chunkId)); } catch (e1) {}
    }
    var label = document.createElement('span');
    label.className = 'cite-label';
    label.textContent = '[' + String(num) + ']';
    var pop = document.createElement('span');
    pop.className = 'cite-pop';
    pop.textContent = chunkId ? _citeLoading() : _citeUnverified();
    sup.appendChild(label);
    sup.appendChild(pop);
    try { _applyCitePopover(sup); } catch (e2) {}
    return sup;
  }

  function _renderTextWithCitations(container, raw) {
    if (!container) return;
    var text = String(raw || '');
    var savedCopyBtn = null;
    try {
      savedCopyBtn = container.querySelector && container.querySelector('button.copy-btn.copy-corner');
      if (savedCopyBtn && savedCopyBtn.parentNode === container) {
        try { container.removeChild(savedCopyBtn); } catch (eRm) {}
      } else {
        savedCopyBtn = null;
      }
    } catch (eKeep) {
      savedCopyBtn = null;
    }
    try { while (container.firstChild) container.removeChild(container.firstChild); } catch (e0) {}

    // Split markdown footnotes (prefer rendering as hoverable superscripts).
    var lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
    var defStart = -1;
    for (var i = 0; i < lines.length; i++) {
      if (/^\[\^\d+\]:/.test(lines[i] || '')) { defStart = i; break; }
    }
    if (defStart > 0 && /^(sources|来源|参考|参考资料)[:：]?\s*$/i.test((lines[defStart - 1] || '').trim())) {
      defStart = defStart - 1;
    }
    var body = (defStart >= 0 ? lines.slice(0, defStart) : lines).join('\n');
    var defs = defStart >= 0 ? lines.slice(defStart) : [];

    var footMap = {};
    var sources = [];
    for (var j = 0; j < defs.length; j++) {
      var ln = defs[j] || '';
      var m = ln.match(/^\[\^(\d+)\]:\s*(.+)$/);
      if (!m) continue;
      var n = String(m[1] || '');
      var rest = String(m[2] || '');
      var cm = rest.match(/\[chunk:([A-Za-z0-9_-]+)\]/);
      if (cm) footMap[n] = cm[1];
      var clean = rest.replace(/\[chunk:[A-Za-z0-9_-]+\]/g, '').replace(/[ \t]{2,}/g, ' ').trim();
      sources.push({ num: n, text: clean, chunk: footMap[n] || '' });
    }

    var maxNum = 0;
    for (var k = 0; k < sources.length; k++) {
      var nn = parseInt(sources[k].num, 10);
      if (!isNaN(nn) && nn > maxNum) maxNum = nn;
    }
    var nextNum = maxNum + 1;
    var chunkNums = {};
    var needsIndex = false;

    var re = /\[\^(\d+)\]|\[chunk:([A-Za-z0-9_-]+)\]/g;
    var last = 0;
    var mm;
    while ((mm = re.exec(body)) !== null) {
      var pre = body.slice(last, mm.index);
      if (pre) container.appendChild(document.createTextNode(pre));
      if (mm[1]) {
        var n2 = String(mm[1] || '');
        var cid2 = footMap[n2] || '';
        container.appendChild(_makeCiteSup(n2, cid2));
        if (cid2) needsIndex = true;
      } else if (mm[2]) {
        var cid3 = String(mm[2] || '');
        if (!chunkNums[cid3]) {
          chunkNums[cid3] = nextNum;
          nextNum += 1;
        }
        container.appendChild(_makeCiteSup(chunkNums[cid3], cid3));
        needsIndex = true;
      }
      last = re.lastIndex;
    }
    var tail = body.slice(last);
    if (tail) container.appendChild(document.createTextNode(tail));

    // Auto-generate a Sources list for inline chunk citations (when the model didn't provide footnote defs).
    var autoSources = [];
    try {
      for (var kcid in chunkNums) {
        if (!Object.prototype.hasOwnProperty.call(chunkNums, kcid)) continue;
        autoSources.push({ num: String(chunkNums[kcid]), text: '', chunk: String(kcid || ''), auto: true });
      }
      autoSources.sort(function (a, b) { return parseInt(a.num, 10) - parseInt(b.num, 10); });
    } catch (eAuto) { autoSources = []; }
    var allSources = sources.slice(0);
    for (var as = 0; as < autoSources.length; as++) {
      allSources.push(autoSources[as]);
    }

    if (allSources.length > 0) {
      var details = document.createElement('details');
      details.className = 'cite-sources';
      var summary = document.createElement('summary');
      summary.textContent = _citeLabel();
      details.appendChild(summary);
      var list = document.createElement('div');
      list.className = 'cite-sources-list';
      for (var s = 0; s < allSources.length; s++) {
        var src = allSources[s] || {};
        var row = document.createElement('div');
        row.className = 'cite-source-row';
        if (src.chunk) {
          try { row.setAttribute('data-chunk-id', String(src.chunk)); } catch (eCid) {}
        }
        if (src.auto) {
          try { row.setAttribute('data-auto', '1'); } catch (eAuto1) {}
        }
        var numSpan = document.createElement('span');
        numSpan.className = 'cite-source-num';
        numSpan.textContent = '[' + String(src.num || '') + '] ';
        var txtSpan = document.createElement('span');
        txtSpan.className = 'cite-source-text';
        txtSpan.textContent = String(src.text || '');
        if (src.auto && src.chunk) {
          if (citeIndex && citeIndex[src.chunk]) {
            try {
              var info = citeIndex[src.chunk] || {};
              var t = String(info.title || info.url || src.chunk || '');
              if (info.url && info.title) t = String(info.title || '') + ' - ' + String(info.url || '');
              txtSpan.textContent = t;
            } catch (eAutoTxt) {}
          } else {
            txtSpan.textContent = _citeLoading();
          }
        }
        row.appendChild(numSpan);
        row.appendChild(txtSpan);
        list.appendChild(row);
      }
      details.appendChild(list);
      container.appendChild(details);
    }

    if (needsIndex) _ensureCiteIndex();

    if (savedCopyBtn) {
      try { savedCopyBtn._owbCopyText = String(text || ''); } catch (eSet) {}
      try { container.appendChild(savedCopyBtn); } catch (ePut) {}
    }
  }

  var _mdReady = null;
  function _ensureMarked() {
    if (_mdReady) return _mdReady;
    _mdReady = _loadScriptOnce('/static/vendor/marked.min.js', 'marked').catch(function () { return null; });
    return _mdReady;
  }

  function _escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;');
  }

  function _sanitizeHtml(html) {
    var tpl = document.createElement('template');
    tpl.innerHTML = String(html || '');
    var forbidden = { SCRIPT: 1, STYLE: 1, IFRAME: 1, OBJECT: 1, EMBED: 1, LINK: 1, META: 1, BASE: 1 };
    var walker = null;
    try {
      walker = document.createTreeWalker(tpl.content, 1, null);
    } catch (e0) {
      walker = null;
    }
    var rm = [];
    if (walker) {
      while (walker.nextNode()) {
        var el = walker.currentNode;
        if (!el || !el.tagName) continue;
        if (forbidden[String(el.tagName).toUpperCase()]) {
          rm.push(el);
          continue;
        }
        try {
          var attrs = el.attributes ? Array.prototype.slice.call(el.attributes) : [];
          for (var i = 0; i < attrs.length; i++) {
            var a = attrs[i];
            if (!a) continue;
            var n = String(a.name || '').toLowerCase();
            var v = String(a.value || '');
            if (n.indexOf('on') === 0) el.removeAttribute(a.name);
            if (n === 'style') el.removeAttribute(a.name);
            if (n === 'srcdoc') el.removeAttribute(a.name);
            if ((n === 'href' || n === 'src') && /^\s*javascript:/i.test(v)) el.removeAttribute(a.name);
          }
        } catch (e1) {}
        if (String(el.tagName).toUpperCase() === 'A') {
          try { el.setAttribute('target', '_blank'); } catch (e2) {}
          try { el.setAttribute('rel', 'noreferrer noopener'); } catch (e3) {}
        }
      }
    }
    for (var j = 0; j < rm.length; j++) {
      try { rm[j].remove(); } catch (e4) { try { if (rm[j].parentNode) rm[j].parentNode.removeChild(rm[j]); } catch (e5) {} }
    }
    return tpl.innerHTML;
  }

  function _makeCiteSupHtml(num, chunkId) {
    var n = String(num || '').replace(/^\s+|\s+$/g, '');
    var cid = String(chunkId || '').replace(/^\s+|\s+$/g, '');
    var cls = 'cite' + (cid ? '' : ' cite-missing');
    var pop = cid ? _citeLoading() : _citeUnverified();
    var attrs = 'class=\"' + _escapeHtml(cls) + '\" data-cite-num=\"' + _escapeHtml(n) + '\"';
    if (cid) attrs += ' data-chunk-id=\"' + _escapeHtml(cid) + '\"';
    return '<sup ' + attrs + '><span class=\"cite-label\">[' + _escapeHtml(n) + ']</span><span class=\"cite-pop\">' + _escapeHtml(pop) + '</span></sup>';
  }

  function _renderMarkdownWithCitations(container, raw) {
    if (!container) return;
    var text = String(raw || '');
    try { container._owbRaw = text; } catch (e0) {}

    // If markdown isn't ready, render plain text (with citations) and upgrade later.
    if (!(window.marked && window.marked.parse)) {
      try { _renderTextWithCitations(container, text); } catch (e1) { container.textContent = text; }
      try {
        _ensureMarked().then(function () {
          try {
            if (!container) return;
            if (container._owbRaw !== text) return;
            if (!(window.marked && window.marked.parse)) return;
            _renderMarkdownWithCitations(container, text);
          } catch (e2) {}
        });
      } catch (e3) {}
      return;
    }

    try { while (container.firstChild) container.removeChild(container.firstChild); } catch (e4) {}

    // Split markdown footnotes (prefer rendering as hoverable superscripts).
    var lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
    var defStart = -1;
    for (var i = 0; i < lines.length; i++) {
      if (/^\[\^\d+\]:/.test(lines[i] || '')) { defStart = i; break; }
    }
    if (defStart > 0 && /^(sources|来源|参考|参考资料)[:：]?\s*$/i.test((lines[defStart - 1] || '').trim())) {
      defStart = defStart - 1;
    }
    var body = (defStart >= 0 ? lines.slice(0, defStart) : lines).join('\n');
    var defs = defStart >= 0 ? lines.slice(defStart) : [];

    var footMap = {};
    var sources = [];
    for (var j = 0; j < defs.length; j++) {
      var ln = defs[j] || '';
      var m = ln.match(/^\[\^(\d+)\]:\s*(.+)$/);
      if (!m) continue;
      var n = String(m[1] || '');
      var rest = String(m[2] || '');
      var cm = rest.match(/\[chunk:([A-Za-z0-9_-]+)\]/);
      if (cm) footMap[n] = cm[1];
      var clean = rest.replace(/\[chunk:[A-Za-z0-9_-]+\]/g, '').replace(/[ \t]{2,}/g, ' ').trim();
      sources.push({ num: n, text: clean, chunk: footMap[n] || '' });
    }

    var maxNum = 0;
    for (var k = 0; k < sources.length; k++) {
      var nn = parseInt(sources[k].num, 10);
      if (!isNaN(nn) && nn > maxNum) maxNum = nn;
    }
    var nextNum = maxNum + 1;
    var chunkNums = {};
    var needsIndex = false;

    var re = /\[\^(\d+)\]|\[chunk:([A-Za-z0-9_-]+)\]/g;
    var last = 0;
    var mm;
    var bodyWithSup = '';
    while ((mm = re.exec(body)) !== null) {
      bodyWithSup += body.slice(last, mm.index);
      if (mm[1]) {
        var n2 = String(mm[1] || '');
        var cid2 = footMap[n2] || '';
        bodyWithSup += _makeCiteSupHtml(n2, cid2);
        if (cid2) needsIndex = true;
      } else if (mm[2]) {
        var cid3 = String(mm[2] || '');
        if (!chunkNums[cid3]) {
          chunkNums[cid3] = nextNum;
          nextNum += 1;
        }
        bodyWithSup += _makeCiteSupHtml(chunkNums[cid3], cid3);
        needsIndex = true;
      }
      last = re.lastIndex;
    }
    bodyWithSup += body.slice(last);

    // Auto-generate a Sources list for inline chunk citations (when the model didn't provide footnote defs).
    var autoSources = [];
    try {
      for (var kcid in chunkNums) {
        if (!Object.prototype.hasOwnProperty.call(chunkNums, kcid)) continue;
        autoSources.push({ num: String(chunkNums[kcid]), text: '', chunk: String(kcid || ''), auto: true });
      }
      autoSources.sort(function (a, b) { return parseInt(a.num, 10) - parseInt(b.num, 10); });
    } catch (eAuto) { autoSources = []; }
    var allSources = sources.slice(0);
    for (var as = 0; as < autoSources.length; as++) allSources.push(autoSources[as]);

    var html = '';
    try { html = window.marked.parse(String(bodyWithSup || '')); } catch (eParse) { html = '<pre class=\"pre\">' + _escapeHtml(body) + '</pre>'; }
    html = _sanitizeHtml(html);

    var wrap = document.createElement('div');
    wrap.className = 'md-body';
    wrap.innerHTML = html || '';
    container.appendChild(wrap);

    try {
      var citeNodes = wrap.querySelectorAll ? wrap.querySelectorAll('sup.cite') : null;
      if (citeNodes && citeNodes.length) {
        for (var ci = 0; ci < citeNodes.length; ci++) {
          try { _applyCitePopover(citeNodes[ci]); } catch (eC) {}
        }
      }
    } catch (eC2) {}

    if (allSources.length > 0) {
      var details = document.createElement('details');
      details.className = 'cite-sources';
      var summary = document.createElement('summary');
      summary.textContent = _citeLabel();
      details.appendChild(summary);
      var list = document.createElement('div');
      list.className = 'cite-sources-list';
      for (var s = 0; s < allSources.length; s++) {
        var src = allSources[s] || {};
        var row = document.createElement('div');
        row.className = 'cite-source-row';
        if (src.chunk) {
          try { row.setAttribute('data-chunk-id', String(src.chunk)); } catch (eCid) {}
        }
        if (src.auto) {
          try { row.setAttribute('data-auto', '1'); } catch (eAuto1) {}
        }
        var numSpan = document.createElement('span');
        numSpan.className = 'cite-source-num';
        numSpan.textContent = '[' + String(src.num || '') + '] ';
        var txtSpan = document.createElement('span');
        txtSpan.className = 'cite-source-text';
        txtSpan.textContent = String(src.text || '');
        if (src.auto && src.chunk) {
          if (citeIndex && citeIndex[src.chunk]) {
            try {
              var info = citeIndex[src.chunk] || {};
              var t = String(info.title || info.url || src.chunk || '');
              if (info.url && info.title) t = String(info.title || '') + ' - ' + String(info.url || '');
              txtSpan.textContent = t;
            } catch (eAutoTxt) {}
          } else {
            txtSpan.textContent = _citeLoading();
          }
        }
        row.appendChild(numSpan);
        row.appendChild(txtSpan);
        list.appendChild(row);
      }
      details.appendChild(list);
      container.appendChild(details);
    }

    if (needsIndex) _ensureCiteIndex();
  }

  function _setHint(text) {
    if (!wbFileHint) return;
    try { wbFileHint.textContent = String(text || ''); } catch (e) {}
  }

  function _clearViewer() {
    if (!wbViewer) return;
    _clear(wbViewer);
  }

  function _fileRawUrl(fileId, download) {
    if (!fileId) return '';
    var url = '/api/tasks/' + encodeURIComponent(taskId) + '/files/raw/' + encodeURIComponent(fileId) + '?token=' + encodeURIComponent(adminToken);
    if (download) url += '&download=1';
    return url;
  }

  function _isDebugFile(f) {
    var name = String((f && f.name) || '').toLowerCase();
    var rel = String((f && f.rel) || '').toLowerCase();
    var group = String((f && f.group) || '').toLowerCase();
    if (group !== 'outputs') return false;
    // The run report is still accessible, but should not force-open the preview panel.
    if (name === 'report.md' || name === 'report.html') return true;
    if (rel.endsWith('/report.md') || rel.endsWith('/report.html')) return true;
    return false;
  }

  function _kindPrio(kind) {
    var k = String(kind || '').toLowerCase();
    if (k === 'pptx') return 0;
    if (k === 'pdf') return 1;
    if (k === 'docx') return 2;
    if (k === 'xlsx' || k === 'xls' || k === 'csv' || k === 'tsv') return 3;
    if (k === 'html' || k === 'htm') return 4;
    if (k === 'md' || k === 'markdown') return 5;
    if (k === 'png' || k === 'jpg' || k === 'jpeg' || k === 'gif' || k === 'webp' || k === 'bmp' || k === 'svg') return 6;
    if (k === 'mp3' || k === 'wav' || k === 'm4a' || k === 'ogg') return 7;
    if (k === 'mp4' || k === 'webm' || k === 'mov') return 8;
    return 50;
  }

  function _loadScriptOnce(src, globalName) {
    return new Promise(function (resolve, reject) {
      try {
        if (globalName && window[globalName]) return resolve(window[globalName]);
      } catch (e0) {}
      var id = 'owb-script-' + String(src || '').replace(/[^a-zA-Z0-9_-]+/g, '-');
      try {
        var existing = document.getElementById(id);
        if (existing) {
          existing.addEventListener('load', function () { resolve(globalName ? window[globalName] : true); });
          existing.addEventListener('error', function () { reject(new Error('script_load_failed')); });
          return;
        }
      } catch (e1) {}
      var s = document.createElement('script');
      s.id = id;
      s.src = src;
      s.async = true;
      s.onload = function () { resolve(globalName ? window[globalName] : true); };
      s.onerror = function () { reject(new Error('script_load_failed')); };
      document.head.appendChild(s);
    });
  }

  function _renderIframe(url) {
    _clearViewer();
    if (!wbViewer) return;
    var frame = document.createElement('iframe');
    frame.className = 'wb-frame';
    frame.src = url;
    frame.setAttribute('referrerpolicy', 'no-referrer');
    wbViewer.appendChild(frame);
  }

  function _renderImage(url) {
    _clearViewer();
    if (!wbViewer) return;
    var img = document.createElement('img');
    img.className = 'wb-image';
    img.src = url;
    wbViewer.appendChild(img);
  }

  function _renderTextDoc(html) {
    _clearViewer();
    if (!wbViewer) return;
    var wrap = document.createElement('div');
    wrap.className = 'wb-doc';
    wrap.innerHTML = html || '';
    wbViewer.appendChild(wrap);
  }

  function _renderWeb() {
    _clearViewer();
    if (!wbViewer) return;
    var wrap = document.createElement('div');
    wrap.className = 'wb-web';
    var bar = document.createElement('div');
    bar.className = 'wb-web-bar';
    var input = document.createElement('input');
    input.type = 'text';
    input.placeholder = 'https://';
    input.value = webUrl || '';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Go';
    bar.appendChild(input);
    bar.appendChild(btn);
    var frame = document.createElement('iframe');
    frame.className = 'wb-frame';
    wrap.appendChild(bar);
    wrap.appendChild(frame);
    wbViewer.appendChild(wrap);

    function go() {
      var v = String(input.value || '').replace(/^\\s+|\\s+$/g, '');
      if (!v) return;
      if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
      webUrl = v;
      frame.src = v;
    }
    btn.addEventListener('click', go);
    input.addEventListener('keydown', function (e) {
      if (e && (e.key === 'Enter' || e.keyCode === 13)) {
        try { if (e.preventDefault) e.preventDefault(); } catch (e0) {}
        go();
      }
    });
    if (webUrl) frame.src = webUrl;
  }

  function _renderMarkdown(fileId) {
    var url = _fileRawUrl(fileId, false);
    _setHint('Loading\u2026');
    _loadScriptOnce('/static/vendor/marked.min.js', 'marked').then(function () {
      return fetch(url).then(function (r) { return r.text(); });
    }).then(function (txt) {
      try {
        if (window.marked && window.marked.parse) {
          _renderTextDoc('<div class="md-body">' + window.marked.parse(String(txt || '')) + '</div>');
        } else {
          _renderTextDoc('<pre class=\"pre\">' + String(txt || '').replace(/</g, '&lt;') + '</pre>');
        }
      } catch (e1) {
        _renderTextDoc('<pre class=\"pre\">' + String(txt || '').replace(/</g, '&lt;') + '</pre>');
      }
      _setHint('');
    }).catch(function () {
      _renderTextDoc('<div class=\"muted\">Failed to load markdown.</div>');
      _setHint('');
    });
  }

  function _renderDocx(fileId) {
    var url = _fileRawUrl(fileId, false);
    _setHint('Loading\u2026');
    _loadScriptOnce('/static/vendor/mammoth.browser.min.js', 'mammoth').then(function () {
      return fetch(url).then(function (r) { return r.arrayBuffer(); });
    }).then(function (buf) {
      if (!window.mammoth || !window.mammoth.convertToHtml) throw new Error('mammoth_missing');
      return window.mammoth.convertToHtml({ arrayBuffer: buf });
    }).then(function (res) {
      var html = (res && res.value) ? String(res.value) : '';
      _renderTextDoc('<div class="docx-body">' + html + '</div>');
      _setHint('');
    }).catch(function () {
      _renderTextDoc('<div class=\"muted\">Failed to preview DOCX. Use Open/Download.</div>');
      _setHint('');
    });
  }

  function _fileSize(fileId) {
    try {
      var f = fileIndex[String(fileId || '')];
      return f ? Number(f.size || 0) : 0;
    } catch (e) {
      return 0;
    }
  }

  function _escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function _renderCode(fileId, kind) {
    var url = _fileRawUrl(fileId, false);
    var size = _fileSize(fileId);
    var zh = _isZh();
    if (size && size > 1500000) {
      _renderTextDoc('<div class=\"muted\">' + (zh ? '文件过大，建议使用“打开/下载”。' : 'File is too large to preview. Use Open/Download.') + '</div>');
      return;
    }
    _setHint('Loading\u2026');
    fetch(url).then(function (r) { return r.text(); }).then(function (txt) {
      var lang = String(kind || '').toLowerCase();
      var map = { js: 'javascript', ts: 'typescript', py: 'python', yml: 'yaml', md: 'markdown', sh: 'bash', c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp' };
      if (map[lang]) lang = map[lang];

      _clearViewer();
      var pre = document.createElement('pre');
      pre.className = 'wb-code';
      var code = document.createElement('code');
      code.className = 'hljs' + (lang ? (' language-' + lang) : '');
      code.textContent = String(txt || '');
      pre.appendChild(code);
      if (wbViewer) wbViewer.appendChild(pre);

      _loadScriptOnce('/static/vendor/highlight.min.js', 'hljs').then(function () {
        try { if (window.hljs && window.hljs.highlightElement) window.hljs.highlightElement(code); } catch (e0) {}
      }).catch(function () { /* no highlight */ });
      _setHint('');
    }).catch(function () {
      _renderTextDoc('<div class=\"muted\">' + (zh ? '无法预览该文件。请使用“打开/下载”。' : 'Failed to preview. Use Open/Download.') + '</div>');
      _setHint('');
    });
  }

  function _renderDelimited(fileId, kind) {
    var url = _fileRawUrl(fileId, false);
    var size = _fileSize(fileId);
    var zh = _isZh();
    if (size && size > 2500000) {
      _renderTextDoc('<div class=\"muted\">' + (zh ? '文件过大，建议使用“打开/下载”。' : 'File is too large to preview. Use Open/Download.') + '</div>');
      return;
    }
    var delim = (String(kind || '').toLowerCase() === 'tsv') ? '\\t' : ',';
    _setHint('Loading\u2026');
    fetch(url).then(function (r) { return r.text(); }).then(function (txt) {
      var lines = String(txt || '').split(/\\r?\\n/);
      var rows = [];
      for (var i = 0; i < lines.length; i++) {
        if (rows.length >= 220) break;
        var line = lines[i];
        if (!line) continue;
        rows.push(line.split(delim));
      }
      _renderTable(rows, { zh: zh, title: String(kind || '').toUpperCase() });
      _setHint('');
    }).catch(function () {
      _renderTextDoc('<div class=\"muted\">' + (zh ? '无法预览表格文件。' : 'Failed to preview table.') + '</div>');
      _setHint('');
    });
  }

  function _renderTable(rows, opts) {
    var zh = !!(opts && opts.zh);
    var data = (rows && Object.prototype.toString.call(rows) === '[object Array]') ? rows : [];
    if (!data.length) {
      _renderTextDoc('<div class=\"muted\">' + (zh ? '空表格。' : 'Empty table.') + '</div>');
      return;
    }
    var maxCols = 0;
    for (var i = 0; i < data.length; i++) {
      try { maxCols = Math.max(maxCols, (data[i] || []).length); } catch (e0) {}
    }
    maxCols = Math.min(maxCols, 40);

    _clearViewer();
    var wrap = document.createElement('div');
    wrap.className = 'wb-table-wrap';
    var table = document.createElement('table');
    table.className = 'wb-table';

    var thead = document.createElement('thead');
    var htr = document.createElement('tr');
    for (var c = 0; c < maxCols; c++) {
      var th = document.createElement('th');
      th.textContent = String(c + 1);
      htr.appendChild(th);
    }
    thead.appendChild(htr);
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    var maxRows = Math.min(data.length, 200);
    for (var r = 0; r < maxRows; r++) {
      var tr = document.createElement('tr');
      var row = data[r] || [];
      for (var cc = 0; cc < maxCols; cc++) {
        var td = document.createElement('td');
        td.textContent = (row[cc] == null) ? '' : String(row[cc]);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    if (wbViewer) wbViewer.appendChild(wrap);
  }

  function _renderXlsx(fileId) {
    var url = _fileRawUrl(fileId, false);
    var size = _fileSize(fileId);
    var zh = _isZh();
    if (size && size > 12000000) {
      _renderTextDoc('<div class=\"muted\">' + (zh ? '文件过大，建议使用“打开/下载”。' : 'File is too large to preview. Use Open/Download.') + '</div>');
      return;
    }
    _setHint('Loading\u2026');
    _loadScriptOnce('/static/vendor/xlsx.full.min.js', 'XLSX').then(function () {
      return fetch(url).then(function (r) { return r.arrayBuffer(); });
    }).then(function (buf) {
      if (!window.XLSX || !window.XLSX.read) throw new Error('xlsx_missing');
      var wb = window.XLSX.read(buf, { type: 'array' });
      var names = (wb && wb.SheetNames) ? wb.SheetNames : [];
      if (!names || !names.length) {
        _renderTextDoc('<div class=\"muted\">' + (zh ? '未找到工作表。' : 'No sheets found.') + '</div>');
        _setHint('');
        return;
      }

      _clearViewer();
      var root = document.createElement('div');
      root.className = 'wb-xlsx';
      var tabs = document.createElement('div');
      tabs.className = 'wb-xlsx-tabs';
      var body = document.createElement('div');
      body.className = 'wb-xlsx-body';
      root.appendChild(tabs);
      root.appendChild(body);
      if (wbViewer) wbViewer.appendChild(root);

      function renderSheet(name) {
        try { while (body.firstChild) body.removeChild(body.firstChild); } catch (e0) {}
        var sheet = wb.Sheets[name];
        if (!sheet) return;
        var rows = window.XLSX.utils.sheet_to_json(sheet, { header: 1, raw: false, defval: '' }) || [];
        var wrap = document.createElement('div');
        wrap.className = 'wb-table-wrap';
        body.appendChild(wrap);
        // Reuse table renderer into the wrapper
        var maxCols = 0;
        for (var i = 0; i < rows.length; i++) {
          try { maxCols = Math.max(maxCols, (rows[i] || []).length); } catch (e1) {}
        }
        maxCols = Math.min(maxCols, 40);
        var table = document.createElement('table');
        table.className = 'wb-table';
        var thead = document.createElement('thead');
        var htr = document.createElement('tr');
        for (var c = 0; c < maxCols; c++) {
          var th = document.createElement('th');
          th.textContent = String(c + 1);
          htr.appendChild(th);
        }
        thead.appendChild(htr);
        table.appendChild(thead);
        var tbody = document.createElement('tbody');
        var maxRows = Math.min(rows.length, 200);
        for (var r = 0; r < maxRows; r++) {
          var tr = document.createElement('tr');
          var row = rows[r] || [];
          for (var cc = 0; cc < maxCols; cc++) {
            var td = document.createElement('td');
            td.textContent = (row[cc] == null) ? '' : String(row[cc]);
            tr.appendChild(td);
          }
          tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        wrap.appendChild(table);
      }

      function renderTabs(active) {
        try { while (tabs.firstChild) tabs.removeChild(tabs.firstChild); } catch (e0) {}
        for (var i = 0; i < names.length; i++) {
          (function (nm) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'wb-xlsx-tab' + (nm === active ? ' active' : '');
            b.textContent = nm;
            b.addEventListener('click', function () {
              renderTabs(nm);
              renderSheet(nm);
            });
            tabs.appendChild(b);
          })(names[i]);
        }
      }

      renderTabs(names[0]);
      renderSheet(names[0]);
      _setHint('');
    }).catch(function () {
      _renderTextDoc('<div class=\"muted\">' + (zh ? '无法预览 Excel 文件。' : 'Failed to preview Excel.') + '</div>');
      _setHint('');
    });
  }

  function _renderAudio(url) {
    _clearViewer();
    if (!wbViewer) return;
    var wrap = document.createElement('div');
    wrap.className = 'wb-media';
    var el = document.createElement('audio');
    el.controls = true;
    el.className = 'wb-audio';
    el.src = url;
    wrap.appendChild(el);
    wbViewer.appendChild(wrap);
  }

  function _renderVideo(url) {
    _clearViewer();
    if (!wbViewer) return;
    var wrap = document.createElement('div');
    wrap.className = 'wb-media';
    var el = document.createElement('video');
    el.controls = true;
    el.className = 'wb-video';
    el.src = url;
    wrap.appendChild(el);
    wbViewer.appendChild(wrap);
  }

  function _renderPptEditor(fileId) {
    pptState = { fileId: fileId, deck: null, slideIndex: 0, dirty: false, editable: false };
    _clearViewer();
    _setHint('Loading\u2026');
    if (wbPptSaveBtn) {
      try { wbPptSaveBtn.style.display = 'none'; } catch (e0) {}
      try { wbPptSaveBtn.disabled = true; } catch (e1) {}
    }
    var wrap = document.createElement('div');
    wrap.className = 'ppt-editor';
    wrap.innerHTML = '<div class=\"ppt-loading\">Loading PPT\u2026</div>';
    if (wbViewer) wbViewer.appendChild(wrap);

    xhr('GET', '/api/tasks/' + encodeURIComponent(taskId) + '/ppt/state?token=' + encodeURIComponent(adminToken) + '&file_id=' + encodeURIComponent(fileId), null, 20000, function (status, text) {
      if (!(status >= 200 && status < 300)) {
        _renderTextDoc('<div class=\"muted\">Failed to load PPT state.</div>');
        _setHint('');
        return;
      }
      var data;
      try { data = JSON.parse(text || '{}'); } catch (e) { data = null; }
      if (!data || data.ok !== true) {
        _renderTextDoc('<div class=\"muted\">Failed to load PPT state.</div>');
        _setHint('');
        return;
      }
      if (!data.editable) {
        _renderTextDoc('<div class=\"muted\">This PPT is not editable yet. Use Open/Download.</div>');
        _setHint('');
        return;
      }
      var deck = data.deck || {};
      if (!deck || !deck.slides || Object.prototype.toString.call(deck.slides) !== '[object Array]') {
        _renderTextDoc('<div class=\"muted\">Missing slide source for this PPT. Use Open/Download.</div>');
        _setHint('');
        return;
      }
      pptState.deck = deck;
      pptState.editable = true;
      pptState.slideIndex = 0;
      pptState.dirty = false;
      _renderPptUi();
      _setHint('');
      if (wbPptSaveBtn) {
        try { wbPptSaveBtn.style.display = ''; } catch (e2) {}
        try { wbPptSaveBtn.disabled = false; } catch (e3) {}
      }
    });
  }

  function _pptSetDirty(v) {
    if (!pptState) return;
    pptState.dirty = !!v;
    if (!wbPptSaveBtn) return;
    try { wbPptSaveBtn.disabled = !pptState.editable || !pptState.dirty; } catch (e0) {}
  }

  function _renderPptUi() {
    if (!pptState || !pptState.deck || !wbViewer) return;
    var deck = pptState.deck || {};
    var slides = (deck.slides && Object.prototype.toString.call(deck.slides) === '[object Array]') ? deck.slides : [];
    if (!slides.length) {
      _renderTextDoc('<div class=\"muted\">No slides.</div>');
      return;
    }

    _clearViewer();
    var root = document.createElement('div');
    root.className = 'ppt-root';

    var left = document.createElement('div');
    left.className = 'ppt-left';
    var addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'ppt-add-slide';
    addBtn.textContent = '+ ' + (document.documentElement.lang.indexOf('zh') === 0 ? '新增幻灯片' : 'New slide');
    left.appendChild(addBtn);
    var list = document.createElement('div');
    list.className = 'ppt-slide-list';
    left.appendChild(list);

    var main = document.createElement('div');
    main.className = 'ppt-main';
    var toolbar = document.createElement('div');
    toolbar.className = 'ppt-toolbar';

    var delSlideBtn = document.createElement('button');
    delSlideBtn.type = 'button';
    delSlideBtn.className = 'ppt-tool';
    delSlideBtn.textContent = (document.documentElement.lang.indexOf('zh') === 0 ? '删除本页' : 'Delete slide');

    var addBulletBtn = document.createElement('button');
    addBulletBtn.type = 'button';
    addBulletBtn.className = 'ppt-tool';
    addBulletBtn.textContent = (document.documentElement.lang.indexOf('zh') === 0 ? '新增要点' : 'Add bullet');

    var themeSel = document.createElement('select');
    themeSel.className = 'ppt-theme';
    var themes = ['modern', 'academic', 'story'];
    for (var ti = 0; ti < themes.length; ti++) {
      var opt = document.createElement('option');
      opt.value = themes[ti];
      opt.textContent = themes[ti];
      themeSel.appendChild(opt);
    }
    try { themeSel.value = String(deck.theme || 'modern'); } catch (eT) {}

    toolbar.appendChild(addBulletBtn);
    toolbar.appendChild(delSlideBtn);
    toolbar.appendChild(themeSel);
    main.appendChild(toolbar);

    var canvasWrap = document.createElement('div');
    canvasWrap.className = 'ppt-canvas-wrap';
    var canvas = document.createElement('div');
    canvas.className = 'ppt-canvas';
    canvasWrap.appendChild(canvas);
    main.appendChild(canvasWrap);

    var notesWrap = document.createElement('div');
    notesWrap.className = 'ppt-notes';
    var notesLabel = document.createElement('div');
    notesLabel.className = 'ppt-notes-label';
    notesLabel.textContent = (document.documentElement.lang.indexOf('zh') === 0 ? '讲稿 / 备注' : 'Notes');
    var notesTa = document.createElement('textarea');
    notesTa.className = 'ppt-notes-ta';
    notesWrap.appendChild(notesLabel);
    notesWrap.appendChild(notesTa);
    main.appendChild(notesWrap);

    root.appendChild(left);
    root.appendChild(main);
    wbViewer.appendChild(root);

    function renderSlideList() {
      _clear(list);
      for (var i = 0; i < slides.length; i++) {
        (function (idx) {
          var s = slides[idx] || {};
          var it = document.createElement('button');
          it.type = 'button';
          it.className = 'ppt-slide-item' + (idx === pptState.slideIndex ? ' active' : '');
          var title = String(s.title || '').replace(/^\\s+|\\s+$/g, '');
          it.textContent = String(idx + 1) + '. ' + (title || (document.documentElement.lang.indexOf('zh') === 0 ? '未命名' : 'Untitled'));
          it.addEventListener('click', function () {
            pptState.slideIndex = idx;
            renderSlideList();
            renderCanvas();
          });
          list.appendChild(it);
        })(i);
      }
    }

    function _escape(s) {
      return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function renderCanvas() {
      var s = slides[pptState.slideIndex] || {};
      _clear(canvas);

      var title = document.createElement('div');
      title.className = 'ppt-title';
      title.contentEditable = 'true';
      title.innerHTML = _escape(s.title || '');
      title.addEventListener('input', function () {
        s.title = title.textContent || '';
        slides[pptState.slideIndex] = s;
        _pptSetDirty(true);
        renderSlideList();
      });
      canvas.appendChild(title);

      var ul = document.createElement('ul');
      ul.className = 'ppt-bullets';
      var bullets = (s.bullets && Object.prototype.toString.call(s.bullets) === '[object Array]') ? s.bullets : [];
      for (var bi = 0; bi < bullets.length; bi++) {
        (function (bidx) {
          var li = document.createElement('li');
          li.className = 'ppt-bullet';
          li.contentEditable = 'true';
          li.innerHTML = _escape(bullets[bidx] || '');
          li.addEventListener('input', function () {
            bullets[bidx] = li.textContent || '';
            s.bullets = bullets;
            slides[pptState.slideIndex] = s;
            _pptSetDirty(true);
          });
          ul.appendChild(li);
        })(bi);
      }
      canvas.appendChild(ul);

      try { notesTa.value = String(s.notes || ''); } catch (eN) {}
    }

    addBtn.addEventListener('click', function () {
      slides.push({ title: (document.documentElement.lang.indexOf('zh') === 0 ? '新幻灯片' : 'New slide'), bullets: [], notes: '', citations: [] });
      pptState.slideIndex = slides.length - 1;
      _pptSetDirty(true);
      renderSlideList();
      renderCanvas();
    });

    delSlideBtn.addEventListener('click', function () {
      if (slides.length <= 1) return;
      slides.splice(pptState.slideIndex, 1);
      if (pptState.slideIndex >= slides.length) pptState.slideIndex = slides.length - 1;
      _pptSetDirty(true);
      renderSlideList();
      renderCanvas();
    });

    addBulletBtn.addEventListener('click', function () {
      var s = slides[pptState.slideIndex] || {};
      var bullets = (s.bullets && Object.prototype.toString.call(s.bullets) === '[object Array]') ? s.bullets : [];
      bullets.push(document.documentElement.lang.indexOf('zh') === 0 ? '新要点' : 'New bullet');
      s.bullets = bullets;
      slides[pptState.slideIndex] = s;
      _pptSetDirty(true);
      renderCanvas();
    });

    notesTa.addEventListener('input', function () {
      var s = slides[pptState.slideIndex] || {};
      s.notes = notesTa.value || '';
      slides[pptState.slideIndex] = s;
      _pptSetDirty(true);
    });

    themeSel.addEventListener('change', function () {
      deck.theme = themeSel.value || 'modern';
      pptState.deck = deck;
      _pptSetDirty(true);
    });

    renderSlideList();
    renderCanvas();
    _pptSetDirty(false);
  }

  function _savePptDeck() {
    if (!pptState || !pptState.editable || !pptState.deck || !pptState.fileId) return;
    if (!pptState.dirty) return;
    if (wbPptSaveBtn) {
      try { wbPptSaveBtn.disabled = true; } catch (e0) {}
    }
    _setHint('Saving\u2026');
    xhr('POST', '/api/tasks/' + encodeURIComponent(taskId) + '/ppt/save?token=' + encodeURIComponent(adminToken), { file_id: pptState.fileId, deck: pptState.deck }, 60000, function (status, text) {
      if (wbPptSaveBtn) {
        try { wbPptSaveBtn.disabled = false; } catch (e1) {}
      }
      if (status >= 200 && status < 300) {
        _pptSetDirty(false);
        _setHint(document.documentElement.lang.indexOf('zh') === 0 ? '已保存' : 'Saved');
        try { refreshFiles(true); } catch (e2) {}
        setTimeout(function () { try { _setHint(''); } catch (e3) {} }, 1200);
        return;
      }
      _setHint((text || '').slice(0, 200) || ('HTTP ' + status));
    });
  }

  function _selectFile(fileId) {
    currentFileId = String(fileId || '');
    currentFileKind = '';
    currentFileMetaSig = '';
    if (wbFileDownload) {
      try { wbFileDownload.href = '#'; } catch (e0) {}
    }
    if (wbPptSaveBtn) {
      try { wbPptSaveBtn.style.display = 'none'; } catch (e1) {}
    }
    pptState = null;

    if (!currentFileId) {
      _clearViewer();
      var hasAny = false;
      try { hasAny = Object.keys(fileIndex || {}).length > 0; } catch (e0) { hasAny = false; }
      if (hasAny) _setHint(document.documentElement.lang.indexOf('zh') === 0 ? '请选择要预览的文件' : 'Select a file to preview');
      else _setHint(document.documentElement.lang.indexOf('zh') === 0 ? '暂无可预览文件' : 'No preview file');
      return;
    }
    if (currentFileId === '__web__') {
      _setHint('');
      currentFileMetaSig = '__web__';
      _renderWeb();
      return;
    }
    var f = fileIndex[currentFileId];
    if (!f) {
      _clearViewer();
      _setHint('');
      return;
    }
    var kind = String(f.kind || '').toLowerCase();
    currentFileKind = kind;
    try {
      currentFileMetaSig = String(currentFileId || '') + '|' + String(kind || '') + '|' + String(f.mtime || 0) + '|' + String(f.size || 0);
    } catch (eSig) {
      currentFileMetaSig = '';
    }
    if (wbFileDownload) {
      try {
        wbFileDownload.href = _fileRawUrl(currentFileId, true);
        wbFileDownload.target = '_blank';
      } catch (e2) {}
    }
    _setHint('');

    if (kind === 'pptx') {
      _renderPptEditor(currentFileId);
      return;
    }
    if (kind === 'pdf' || kind === 'html' || kind === 'htm') {
      _renderIframe(_fileRawUrl(currentFileId, false));
      return;
    }
    if (kind === 'md' || kind === 'markdown') {
      _renderMarkdown(currentFileId);
      return;
    }
    if (kind === 'docx') {
      _renderDocx(currentFileId);
      return;
    }
    if (kind === 'xlsx' || kind === 'xls') {
      _renderXlsx(currentFileId);
      return;
    }
    if (kind === 'csv' || kind === 'tsv') {
      _renderDelimited(currentFileId, kind);
      return;
    }
    if (kind === 'png' || kind === 'jpg' || kind === 'jpeg' || kind === 'gif' || kind === 'webp' || kind === 'bmp' || kind === 'svg') {
      _renderImage(_fileRawUrl(currentFileId, false));
      return;
    }
    if (kind === 'mp3' || kind === 'wav' || kind === 'm4a' || kind === 'ogg') {
      _renderAudio(_fileRawUrl(currentFileId, false));
      return;
    }
    if (kind === 'mp4' || kind === 'webm' || kind === 'mov') {
      _renderVideo(_fileRawUrl(currentFileId, false));
      return;
    }
    if (kind === 'txt' || kind === 'log' || kind === 'json' || kind === 'yaml' || kind === 'yml' || kind === 'toml' || kind === 'ini' || kind === 'cfg' || kind === 'py' || kind === 'js' || kind === 'ts' || kind === 'tsx' || kind === 'jsx' || kind === 'css' || kind === 'java' || kind === 'go' || kind === 'rs' || kind === 'sh' || kind === 'bat' || kind === 'ps1') {
      _renderCode(currentFileId, kind);
      return;
    }
    _renderTextDoc('<div class=\"muted\">No built-in preview for this file. Use Open/Download.</div>');
  }

  function _renderFileSelect(files, defaultId, forceSelect) {
    if (!wbFileSelect) return;
    var list = (files && Object.prototype.toString.call(files) === '[object Array]') ? files : [];
    var deliverables = [];
    var debug = [];
    fileIndex = {};
    for (var i = 0; i < list.length; i++) {
      var f = list[i] || {};
      if (!f.id) continue;
      fileIndex[f.id] = f;
      if (_isDebugFile(f)) debug.push(f);
      else deliverables.push(f);
    }
    deliverables.sort(function (a, b) {
      var pa = _kindPrio(a.kind);
      var pb = _kindPrio(b.kind);
      if (pa !== pb) return pa - pb;
      var ma = Number(a.mtime || 0);
      var mb = Number(b.mtime || 0);
      return mb - ma;
    });

    debug.sort(function (a, b) { return String(a.rel || '').localeCompare(String(b.rel || '')); });

    _clear(wbFileSelect);
    var opt0 = document.createElement('option');
    opt0.value = '';
    opt0.textContent = (document.documentElement.lang.indexOf('zh') === 0 ? '选择文件…' : 'Select a file…');
    wbFileSelect.appendChild(opt0);

    var optWeb = document.createElement('option');
    optWeb.value = '__web__';
    optWeb.textContent = (document.documentElement.lang.indexOf('zh') === 0 ? '网页 (URL)…' : 'Web (URL)…');
    wbFileSelect.appendChild(optWeb);

    if (deliverables.length) {
      var og = document.createElement('optgroup');
      og.label = (document.documentElement.lang.indexOf('zh') === 0 ? '文件' : 'Files');
      for (var d = 0; d < deliverables.length; d++) {
        var f1 = deliverables[d] || {};
        var o1 = document.createElement('option');
        o1.value = String(f1.id || '');
        o1.textContent = String(f1.name || '');
        og.appendChild(o1);
      }
      wbFileSelect.appendChild(og);
    }
    if (debug.length) {
      var og2 = document.createElement('optgroup');
      og2.label = (document.documentElement.lang.indexOf('zh') === 0 ? '调试/报告' : 'Debug');
      for (var g = 0; g < debug.length; g++) {
        var f2 = debug[g] || {};
        var o2 = document.createElement('option');
        o2.value = String(f2.id || '');
        o2.textContent = String(f2.name || '');
        og2.appendChild(o2);
      }
      wbFileSelect.appendChild(og2);
    }

    var chosen = '';
    if (currentFileId === '__web__') chosen = '__web__';
    else if (currentFileId && fileIndex[currentFileId]) chosen = currentFileId;
    if (!chosen) {
      var def = String(defaultId || '');
      if (def && fileIndex[def] && !_isDebugFile(fileIndex[def])) chosen = def;
    }
    if (!chosen && deliverables.length) chosen = String(deliverables[0].id || '');

    try { wbFileSelect.value = chosen || ''; } catch (eSel) {}
    // Avoid re-rendering the preview on every file poll. Only reselect when the target changed,
    // or when the selected file was updated.
    var shouldReselect = false;
    var cur = String(currentFileId || '');
    var next = String(chosen || '');
    if (cur !== next) shouldReselect = true;
    else if (next && currentFileMetaSig) {
      try {
        var nf = fileIndex[next];
        if (nf) {
          var nextSig = String(next || '') + '|' + String((nf.kind || '')).toLowerCase() + '|' + String(nf.mtime || 0) + '|' + String(nf.size || 0);
          if (nextSig !== currentFileMetaSig) shouldReselect = true;
        }
      } catch (eSig2) {}
    }
    // Never interrupt an in-progress PPT edit due to background polling.
    try {
      if (pptState && pptState.dirty && cur === next) shouldReselect = false;
    } catch (ePpt) {}
    if (shouldReselect) _selectFile(chosen || '');

    var hasDeliverables = deliverables.length > 0;
    if (filesAutoExpand && _isSidebarCollapsed() && hasDeliverables) {
      _setSidebarCollapsed(false);
    }
    if (!hasDeliverables && _isSidebarCollapsed()) {
      _setHint(document.documentElement.lang.indexOf('zh') === 0 ? '暂无可预览文件' : 'No preview file');
    }
  }

  if (wbFileSelect) {
    wbFileSelect.addEventListener('change', function () {
      try { _selectFile(wbFileSelect.value || ''); } catch (e) {}
    });
  }

  if (wbFileOpenBtn) {
    wbFileOpenBtn.addEventListener('click', function () {
      if (!currentFileId || currentFileId === '__web__') return;
      xhr('POST', '/api/tasks/' + encodeURIComponent(taskId) + '/files/open?token=' + encodeURIComponent(adminToken), { file_id: currentFileId, reveal: false }, 12000, function () {});
    });
  }
  if (wbFileRevealBtn) {
    wbFileRevealBtn.addEventListener('click', function () {
      if (!currentFileId || currentFileId === '__web__') return;
      xhr('POST', '/api/tasks/' + encodeURIComponent(taskId) + '/files/open?token=' + encodeURIComponent(adminToken), { file_id: currentFileId, reveal: true }, 12000, function () {});
    });
  }
  if (wbPptSaveBtn) {
    wbPptSaveBtn.addEventListener('click', function () {
      try { _savePptDeck(); } catch (e) {}
    });
  }

  function _shouldPollFiles() {
    var cur = '';
    try { cur = taskStatusEl ? (taskStatusEl.textContent || '') : ''; } catch (e0) { cur = ''; }
    var s = String(cur || '').toLowerCase();
    return (s === 'queued' || s === 'planning' || s === 'running' || s === 'waiting_approval');
  }

  function refreshFiles(force) {
    if (!wbGrid) return;
    if (!adminToken) return;
    if (filesInFlight) return;
    var now = Date.now ? Date.now() : (new Date().getTime());
    if (!force && (now - filesLastFetchMs) < 2500) return;

    filesInFlight = true;
    xhr('GET', '/api/tasks/' + encodeURIComponent(taskId) + '/files?token=' + encodeURIComponent(adminToken), null, 12000, function (status, text) {
      filesInFlight = false;
      filesLastFetchMs = Date.now ? Date.now() : (new Date().getTime());
      if (!(status >= 200 && status < 300)) return;
      var data;
      try { data = JSON.parse(text || '{}'); } catch (e) { data = null; }
      if (!data || data.ok !== true) return;
      try { _renderFileSelect(data.files || [], data.default_id || '', !!force); } catch (e2) {}
    });
  }

  function xhr(method, url, body, timeoutMs, cb) {
    try {
      var r = new XMLHttpRequest();
      r.open(method, url, true);
      r.timeout = timeoutMs || 8000;
      if (body) r.setRequestHeader('Content-Type', 'application/json');
      r.onreadystatechange = function () {
        if (r.readyState !== 4) return;
        cb(r.status, r.responseText || '');
      };
      r.ontimeout = function () { cb(0, 'timeout'); };
      r.onerror = function () { cb(0, 'error'); };
      r.send(body ? JSON.stringify(body) : null);
    } catch (e) {
      cb(0, 'exception');
    }
  }

  function _nowMs() {
    try { return Date.now ? Date.now() : (new Date().getTime()); } catch (e) { return 0; }
  }

  function _humanizeHttp(status, text) {
    var zh = _isZh();
    var s = Number(status || 0);
    var msg = String(text || '').replace(/^\\s+|\\s+$/g, '');
    if (s === 0) return zh ? '后端连接失败/超时（检查网络或重启应用）' : 'Backend unreachable / timed out (check network or restart app)';
    if (s === 401 || s === 403) return zh ? '权限校验失败（token 失效/不匹配），请重启应用' : 'Unauthorized (token invalid). Restart the app.';
    if (s === 404) return zh ? '资源不存在（可能任务已被删除）' : 'Not found (maybe task was deleted)';
    if (s === 409) return zh ? '任务正忙/冲突，请稍后重试' : 'Conflict/busy. Please retry.';
    if (s >= 500) return (zh ? '后端错误：' : 'Server error: ') + ((msg && msg.slice(0, 160)) || ('HTTP ' + s));
    return (msg && msg.slice(0, 160)) || ('HTTP ' + s);
  }

  function _setNetStatus(level, msg) {
    if (!netStatusEl) return;
    var cls = 'muted wb2-net-status';
    if (level === 'warn') cls += ' warn';
    if (level === 'bad') cls += ' bad';
    try { netStatusEl.className = cls; } catch (e0) {}
    try { netStatusEl.textContent = String(msg || ''); } catch (e1) {}
  }

  function _noteNetOk() {
    netFailCount = 0;
    netLastMsg = '';
    netLastUpdateMs = 0;
    _setNetStatus('', '');
  }

  function _noteNetFail(status, text) {
    netFailCount = (netFailCount || 0) + 1;
    if (netFailCount < 2) return;
    var msg = _humanizeHttp(status, text);
    var now = _nowMs();
    if (msg === netLastMsg && now && netLastUpdateMs && (now - netLastUpdateMs) < 2500) return;
    netLastMsg = msg;
    netLastUpdateMs = now;
    var level = (Number(status || 0) === 0 || Number(status || 0) >= 500 || Number(status || 0) === 401 || Number(status || 0) === 403) ? 'bad' : 'warn';
    _setNetStatus(level, msg);
  }

  function fmtTs(ts) {
    try { return new Date(ts * 1000).toLocaleString(); } catch (e) { return ''; }
  }

  var canceling = false;
  var cancelRequested = false;

  function updateCancelUI(status) {
    if (!cancelBtn) return;
    var s = String(status || '').toLowerCase();
    var busy = (s === 'queued' || s === 'planning' || s === 'running' || s === 'waiting_approval');
    if (!busy) cancelRequested = false;
    try { cancelBtn.style.display = busy ? '' : 'none'; } catch (e0) {}
    try { cancelBtn.disabled = (!busy) || canceling || cancelRequested; } catch (e1) {}
  }

  function cancelTask() {
    if (!cancelBtn || canceling || cancelRequested) return;
    var cur = '';
    try { cur = taskStatusEl ? (taskStatusEl.textContent || '') : ''; } catch (e0) { cur = ''; }
    var s = String(cur || '').toLowerCase();
    var busy = (s === 'queued' || s === 'planning' || s === 'running' || s === 'waiting_approval');
    if (!busy) return;

    canceling = true;
    updateCancelUI(cur);
    if (cancelStatus) {
      try { cancelStatus.textContent = (cancelForm && cancelForm.getAttribute('data-canceling')) || 'Canceling…'; }
      catch (e1) { cancelStatus.textContent = 'Canceling…'; }
    }

    xhr('POST', '/api/tasks/' + encodeURIComponent(taskId) + '/cancel?token=' + encodeURIComponent(adminToken), null, 12000, function (status, text) {
      canceling = false;
      if (status >= 200 && status < 300) {
        _noteNetOk();
        cancelRequested = true;
        if (cancelStatus) cancelStatus.textContent = '';
        try { if (typeof pollTask === 'function') pollTask(); } catch (e2) {}
        updateCancelUI(taskStatusEl ? (taskStatusEl.textContent || '') : '');
        return;
      }
      _noteNetFail(status, text);
      cancelRequested = false;
      if (cancelStatus) cancelStatus.textContent = (text || '').slice(0, 200) || ('HTTP ' + status);
      updateCancelUI(taskStatusEl ? (taskStatusEl.textContent || '') : '');
    });
  }

  function addTimeline(kind, desc, ts) {
    if (!timeline) return;
    var item = document.createElement('div');
    item.className = 'timeline-item';
    var left = document.createElement('div');
    var right = document.createElement('div');
    right.className = 'timeline-right';

    var copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'copy-btn copy-inline';
    copyBtn.textContent = '⧉';
    try {
      copyBtn.title = (window.__owbCopy && window.__owbCopy.label) ? window.__owbCopy.label : 'Copy';
      copyBtn.setAttribute('aria-label', copyBtn.title);
    } catch (e0) {}
    copyBtn._owbCopyText = (String(kind || '') + ': ' + String(desc || '')).replace(/^\s+|\s+$/g, '');

    var tsEl = document.createElement('div');
    tsEl.className = 'ts';
    tsEl.textContent = ts ? fmtTs(ts) : '';

    right.appendChild(copyBtn);
    right.appendChild(tsEl);

    var k = document.createElement('div');
    k.className = 'kind';
    k.textContent = kind;
    var d = document.createElement('div');
    d.className = 'desc';
    d.textContent = desc || '';
    left.appendChild(k);
    left.appendChild(d);
    item.appendChild(left);
    item.appendChild(right);
    timeline.appendChild(item);
    timeline.scrollTop = timeline.scrollHeight;
  }

  // Step list (shows only step titles by default; details expand on demand).
  var stepCards = {};
  var stepMeta = {};
  var stepsEmpty = null;

  function _ensureStepsPlaceholder() {
    if (!timeline) return;
    if (Object.keys(stepCards || {}).length > 0) return;
    if (stepsEmpty) return;
    var div = document.createElement('div');
    div.className = 'muted';
    div.style.padding = '6px 2px';
    div.textContent = (document.documentElement.lang.indexOf('zh') === 0 ? '等待运行进展…' : 'Waiting for progress…');
    stepsEmpty = div;
    timeline.appendChild(div);
  }

  function _updateStepsPlaceholderText(status) {
    if (!stepsEmpty) return;
    var s = String(status || '').toLowerCase();
    var zh = (document.documentElement.lang.indexOf('zh') === 0);
    if (s === 'running' || s === 'planning' || s === 'queued' || s === 'waiting_approval') {
      stepsEmpty.textContent = zh ? '任务运行中，等待进展…' : 'Task is running. Waiting for progress…';
      return;
    }
    if (s === 'succeeded') {
      stepsEmpty.textContent = zh ? '任务已完成。' : 'Task completed.';
      return;
    }
    if (s === 'failed') {
      stepsEmpty.textContent = zh ? '任务失败（可查看错误/重试）。' : 'Task failed.';
      return;
    }
    if (s === 'canceled' || s === 'cancelled') {
      stepsEmpty.textContent = zh ? '任务已中止。' : 'Task cancelled.';
      return;
    }
    stepsEmpty.textContent = zh ? '等待运行进展…' : 'Waiting for progress…';
  }

  function _clearStepsPlaceholder() {
    if (!stepsEmpty) return;
    try { if (stepsEmpty.remove) stepsEmpty.remove(); } catch (e) {}
    stepsEmpty = null;
  }

  function _stepKey(id) {
    return String(id || '').replace(/^\\s+|\\s+$/g, '');
  }

  var _unknownStepOrder = {};
  var _unknownStepSeq = 0;

  function _stepTitle(id) {
    var k = _stepKey(id);
    var m = stepMeta[k] || {};
    var name = String(m.name || '').replace(/^\\s+|\\s+$/g, '');
    var idx = m.idx;
    var hasIdx = (typeof idx === 'number' && idx >= 0);
    var prefix = hasIdx ? (String(idx + 1) + '. ') : '';
    if (name) return prefix + name;

    // Never show raw step IDs in the UI; fall back to a stable, user-friendly label.
    var zh = false;
    try { zh = document.documentElement.lang.indexOf('zh') === 0; } catch (e0) { zh = false; }
    if (hasIdx) return prefix + (zh ? '步骤' : 'Step');
    try {
      if (!Object.prototype.hasOwnProperty.call(_unknownStepOrder, k)) {
        _unknownStepSeq += 1;
        _unknownStepOrder[k] = _unknownStepSeq;
      }
    } catch (e1) {}
    var n = 0;
    try { n = parseInt(_unknownStepOrder[k] || 0, 10) || 0; } catch (e2) { n = 0; }
    if (n > 0) return (zh ? '步骤 ' : 'Step ') + String(n);
    return (zh ? '步骤' : 'Step');
  }

  function _ensureStepCard(id) {
    if (!timeline) return null;
    var k = _stepKey(id);
    if (!k) return null;
    if (stepCards[k]) return stepCards[k];

    _clearStepsPlaceholder();

    var details = document.createElement('details');
    details.className = 'step-card';

    var summary = document.createElement('summary');
    summary.className = 'step-summary';

    var titleEl = document.createElement('div');
    titleEl.className = 'step-title';
    titleEl.textContent = _stepTitle(k);

    var meta = document.createElement('div');
    meta.className = 'step-meta';
    var statusEl = document.createElement('span');
    statusEl.className = 'status queued';
    statusEl.textContent = 'queued';
    meta.appendChild(statusEl);

    summary.appendChild(titleEl);
    summary.appendChild(meta);

    var body = document.createElement('div');
    body.className = 'step-body';

    details.appendChild(summary);
    details.appendChild(body);
    timeline.appendChild(details);

    stepCards[k] = { el: details, titleEl: titleEl, statusEl: statusEl, bodyEl: body, lastTs: 0 };
    return stepCards[k];
  }

  function _openStepCard(id) {
    var k = _stepKey(id);
    if (!k) return;
    var card = stepCards[k] || _ensureStepCard(k);
    if (!card || !card.el) return;
    try { card.el.open = true; } catch (e) {}
  }

  function _setStepStatus(id, status) {
    var k = _stepKey(id);
    var card = stepCards[k] || _ensureStepCard(k);
    if (!card || !card.statusEl) return;
    var s = String(status || '').toLowerCase().replace(/^\\s+|\\s+$/g, '');
    if (!s) s = 'running';
    try { card.statusEl.textContent = s; } catch (e0) {}
    try { card.statusEl.className = 'status ' + s; } catch (e1) {}
  }

  function _refreshStepTitle(id) {
    var k = _stepKey(id);
    var card = stepCards[k];
    if (!card || !card.titleEl) return;
    try { card.titleEl.textContent = _stepTitle(k); } catch (e0) {}
  }

  function _upsertStep(id, fields, ts) {
    var k = _stepKey(id);
    if (!k) return;
    var f = fields || {};
    var meta = stepMeta[k] || {};
    if (f.name != null) {
      var nm = String(f.name || '').replace(/^\\s+|\\s+$/g, '');
      if (nm) meta.name = nm;
    }
    if (f.title != null) {
      var tl = String(f.title || '').replace(/^\\s+|\\s+$/g, '');
      if (tl) meta.name = tl;
    }
    if (f.idx != null) {
      var idx = Number(f.idx);
      if (!isNaN(idx) && idx >= 0) meta.idx = idx;
    }
    stepMeta[k] = meta;
    _ensureStepCard(k);
    _refreshStepTitle(k);
    if (f.status) _setStepStatus(k, f.status);
    if (ts && stepCards[k]) stepCards[k].lastTs = Math.max(stepCards[k].lastTs || 0, ts || 0);
  }

  function _appendStepDetail(id, line, ts) {
    var k = _stepKey(id);
    if (!k) return;
    var card = stepCards[k] || _ensureStepCard(k);
    if (!card || !card.bodyEl) return;
    var t = Number(ts || 0);
    if (t && card.lastTs && t < card.lastTs) return;
    if (t) card.lastTs = t;
    var div = document.createElement('div');
    div.className = 'step-event';
    div.textContent = String(line || '');
    card.bodyEl.appendChild(div);
    try {
      var max = 24;
      while (card.bodyEl.children && card.bodyEl.children.length > max) {
        card.bodyEl.removeChild(card.bodyEl.firstChild);
      }
    } catch (e0) {}
  }

  function _mapUakStatus(t) {
    var s = String(t || '').toLowerCase();
    if (s === 'step.failed') return 'failed';
    if (s === 'step.completed') return 'succeeded';
    if (s === 'step.started') return 'running';
    if (s === 'step.scheduled') return 'queued';
    return '';
  }

  function _handleEvent(evType, payload, ts) {
    var p = payload || {};
    var typ = String(evType || '');

    if (typ === 'step_update') {
      var sid = _stepKey(p.step_id || p.stepId || p.id || '');
      var fs = p.fields || {};
      if (sid) _upsertStep(sid, { status: fs.status }, ts);
      return;
    }

    if (typ === 'approval_requested') {
      var sid2 = _stepKey(p.step_id || '');
      var tool = String(p.tool || '').replace(/^\\s+|\\s+$/g, '');
      var scope = String(p.scope || '').replace(/^\\s+|\\s+$/g, '');
      if (sid2) {
        _setPendingApproval(sid2, tool, scope);
      }
      if (sid2) _appendStepDetail(sid2, 'approval requested: ' + (tool || '') + (scope ? (' (' + scope + ')') : ''), ts);
      return;
    }

    if (typ === 'approval_decided') {
      var sid3 = _stepKey(p.step_id || '');
      var tool2 = String(p.tool || '').replace(/^\\s+|\\s+$/g, '');
      var scope2 = String(p.scope || '').replace(/^\\s+|\\s+$/g, '');
      var decision = String(p.decision || '').replace(/^\\s+|\\s+$/g, '');
      try {
        if (pendingApproval && pendingApproval.stepId && sid3 && pendingApproval.stepId === sid3) _clearPendingApproval();
      } catch (eC) {}
      if (sid3) _appendStepDetail(sid3, 'approval ' + (decision || '') + ': ' + (tool2 || '') + (scope2 ? (' (' + scope2 + ')') : ''), ts);
      return;
    }

    if (typ === 'uak_event') {
      var ev = p.event || {};
      var t2 = String(ev.type || '');
      var sid4 = _stepKey(ev.step_id || '');
      var src = ev.source || {};
      var srcName = String(src.name || '').replace(/^\\s+|\\s+$/g, '');
      var srcComp = String(src.component || '').replace(/^\\s+|\\s+$/g, '');
      var pl = ev.payload || {};
      var zh2 = _isZh();

      if (t2.indexOf('step.') === 0) {
        var node = String((pl && pl.node) || '').replace(/^\\s+|\\s+$/g, '');
        var title = node || srcName;
        var st = _mapUakStatus(t2);
        if (sid4) _upsertStep(sid4, { title: title, status: st }, ts);
        if (sid4 && st === 'running') _openStepCard(sid4);
        return;
      }
      // Some UAK events (e.g., run.*) are not step-scoped; attach to a synthetic "run" card.
      if (!sid4 && t2.indexOf('run.') === 0) sid4 = '__run__';
      if (!sid4) return;
      if (sid4 === '__run__') {
        _upsertStep(sid4, { title: (zh2 ? '运行' : 'Run') }, ts);
      }

      function _toolNameFromEvent() {
        var tn = '';
        try { tn = String((pl && pl.tool) || '').replace(/^\\s+|\\s+$/g, ''); } catch (e0) { tn = ''; }
        if (tn) return tn;
        try { tn = String(srcName || '').replace(/^\\s+|\\s+$/g, ''); } catch (e1) { tn = ''; }
        return tn;
      }

      function _append(line) {
        var s = String(line || '').replace(/^\\s+|\\s+$/g, '');
        if (!s) return;
        _appendStepDetail(sid4, s, ts);
        _openStepCard(sid4);
      }

      // Record user-facing details (concise + readable).
      if (t2.indexOf('run.') === 0) {
        if (t2 === 'run.created') {
          var g = String((pl && pl.goal) || '').replace(/^\\s+|\\s+$/g, '');
          _append(zh2 ? ('已创建运行' + (g ? ('：' + g) : '')) : ('Run created' + (g ? (': ' + g) : '')));
        } else if (t2 === 'run.started') {
          _append(zh2 ? '运行开始' : 'Run started');
        } else if (t2 === 'run.completed') {
          _append(zh2 ? '运行完成' : 'Run completed');
        } else if (t2 === 'run.failed') {
          _append(zh2 ? '运行失败' : 'Run failed');
        }
        return;
      }

      if (t2.indexOf('tool.') === 0) {
        // Skip overly-technical policy noise by default.
        if (t2 === 'tool.authorized') return;
        var toolName = _toolNameFromEvent();
        var eff = String((pl && pl.side_effects) || '').replace(/^\\s+|\\s+$/g, '');
        var effSuffix = eff ? (zh2 ? ('（' + eff + '）') : (' (' + eff + ')')) : '';
        if (t2 === 'tool.requested') _append((zh2 ? '请求工具：' : 'Tool requested: ') + (toolName || '') + effSuffix);
        else if (t2 === 'tool.started') _append((zh2 ? '运行工具：' : 'Tool started: ') + (toolName || ''));
        else if (t2 === 'tool.completed') {
          var att = 0;
          try { att = parseInt((pl && pl.attempts) || 0, 10) || 0; } catch (eA) { att = 0; }
          var extra = (att && att > 1) ? (zh2 ? ('（重试' + String(att) + '次）') : (' (retries:' + String(att) + ')')) : '';
          _append((zh2 ? '工具完成：' : 'Tool completed: ') + (toolName || '') + extra);
        } else if (t2 === 'tool.failed') {
          _append((zh2 ? '工具失败：' : 'Tool failed: ') + (toolName || ''));
        } else if (t2 === 'tool.denied') {
          _append((zh2 ? '工具被拒绝：' : 'Tool denied: ') + (toolName || ''));
        } else {
          _append((zh2 ? '工具事件：' : 'Tool: ') + (toolName || '') + ' (' + t2 + ')');
        }
        return;
      }

      if (t2.indexOf('llm.') === 0) {
        var model = String((pl && pl.model) || '').replace(/^\\s+|\\s+$/g, '');
        var tc = '';
        try {
          var nTools = (pl && pl.tool_count != null) ? parseInt(pl.tool_count, 10) : NaN;
          if (!isNaN(nTools) && nTools > 0) tc = (zh2 ? ('（可用工具:' + String(nTools) + '）') : (' (tools:' + String(nTools) + ')'));
        } catch (eT) { tc = ''; }
        if (t2 === 'llm.requested') _append((zh2 ? '调用模型：' : 'LLM requested: ') + (model || '') + tc);
        else if (t2 === 'llm.completed') _append((zh2 ? '模型完成：' : 'LLM completed: ') + (model || ''));
        else if (t2 === 'llm.failed') _append((zh2 ? '模型失败：' : 'LLM failed: ') + (model || ''));
        else _append((zh2 ? '模型事件：' : 'LLM: ') + (model || '') + ' (' + t2 + ')');
        return;
      }

      if (t2.indexOf('guardrail.') === 0) {
        var name = String(srcName || '').replace(/^\\s+|\\s+$/g, '');
        var phase = String((pl && pl.phase) || '').replace(/^\\s+|\\s+$/g, '');
        var phaseSuffix = phase ? (zh2 ? ('（' + phase + '）') : (' (' + phase + ')')) : '';
        if (t2 === 'guardrail.started') _append((zh2 ? '校验中：' : 'Guardrail started: ') + (name || '') + phaseSuffix);
        else if (t2 === 'guardrail.passed') _append((zh2 ? '校验通过：' : 'Guardrail passed: ') + (name || '') + phaseSuffix);
        else if (t2 === 'guardrail.failed') {
          var reason = String((pl && pl.reason) || '').replace(/^\\s+|\\s+$/g, '');
          _append((zh2 ? '校验失败：' : 'Guardrail failed: ') + (name || '') + phaseSuffix + (reason ? (zh2 ? (' - ' + reason) : (' - ' + reason)) : ''));
        } else {
          _append((zh2 ? '校验事件：' : 'Guardrail: ') + (name || '') + ' (' + t2 + ')');
        }
        return;
      }

      if (t2.indexOf('approval.') === 0 || t2.indexOf('interrupt.') === 0) {
        _append((zh2 ? '需要确认：' : 'Approval needed: ') + (t2 + (srcName ? (' ' + srcName) : '')).replace(/^\\s+|\\s+$/g, ''));
        return;
      }

      return;
    }
  }

  function _clearChatPlaceholder() {
    if (!chat) return;
    var el = chat.querySelector('.chat-empty');
    if (el) el.remove();
  }

  function ensureChatPlaceholder() {
    if (!chat) return;
    if (chat.children && chat.children.length > 0) return;
    var empty = document.createElement('div');
    empty.className = 'chat-empty';
    empty.textContent = '...';
    try { empty.textContent = chat.getAttribute('data-empty') || ''; } catch (e) {}
    if (!empty.textContent) empty.textContent = 'No messages yet.';
    chat.appendChild(empty);
  }

  function addChat(role, content, ts) {
    if (!chat) return;
    _clearChatPlaceholder();
    var item = document.createElement('div');
    var r = String(role || 'assistant').toLowerCase();
    if (r !== 'user' && r !== 'assistant' && r !== 'system') r = 'assistant';
    var raw = String(content || '');
    try {
      var last = chat.lastElementChild;
      if (last && last._owbRole === r && last._owbText === raw) return;
    } catch (eD) {}
    item.className = 'chat-item ' + r;

    var bubble = document.createElement('div');
    bubble.className = 'chat-bubble copyable';

    var textEl = document.createElement('div');
    var wantMd = (r === 'assistant' || r === 'system');
    textEl.className = 'chat-text' + (wantMd ? ' chat-markdown' : '');
    if (wantMd) {
      try { _renderMarkdownWithCitations(textEl, content || ''); }
      catch (eR) {
        try { _renderTextWithCitations(textEl, content || ''); } catch (eR2) { textEl.textContent = content || ''; }
      }
    } else {
      textEl.textContent = content || '';
    }

    var copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'copy-btn copy-corner';
    copyBtn.textContent = '⧉';
    try {
      copyBtn.title = (window.__owbCopy && window.__owbCopy.label) ? window.__owbCopy.label : 'Copy';
      copyBtn.setAttribute('aria-label', copyBtn.title);
    } catch (e0) {}
    copyBtn._owbCopyText = String(content || '');

    bubble.appendChild(textEl);
    bubble.appendChild(copyBtn);

    item.appendChild(bubble);
    item._owbRole = r;
    item._owbText = raw;
    chat.appendChild(item);
    try {
      var scroller = wbThread || chat;
      scroller.scrollTop = scroller.scrollHeight;
    } catch (eS) {}
  }

  function applyTaskFields(fields) {
    if (!fields) return;
    if (fields.status && taskStatusEl) {
      taskStatusEl.textContent = fields.status;
      taskStatusEl.className = 'status ' + fields.status;
      updateCancelUI(fields.status);
      try { _updateStepsPlaceholderText(fields.status); } catch (eP) {}
    }
    if (fields.updated_at && taskUpdatedEl) {
      taskUpdatedEl.textContent = fields.updated_at;
    }
  }

  function applyStepFields(stepId, fields, ts) {
    try { _upsertStep(stepId, fields || {}, ts || 0); } catch (e) {}
    try {
      var k = _stepKey(stepId);
      var m = stepMeta[k] || {};
      if (!m || !m.name) refreshStepsSnapshot(false);
    } catch (e2) {}
  }

  function describe(evType, payload) {
    var p = payload || {};
    if (evType === 'chat_message') {
      return { kind: 'chat', desc: '' };
    }
    if (evType === 'task_update') {
      var f = p.fields || {};
      if (f.status) return { kind: 'task', desc: 'status -> ' + f.status };
      return { kind: 'task', desc: 'updated' };
    }
    if (evType === 'step_update') {
      var fs = p.fields || {};
      var st = fs.status ? ('status -> ' + fs.status) : 'updated';
      var sid = p.step_id || '';
      return { kind: 'step', desc: (sid + ' ' + st).replace(/^\s+|\s+$/g, '') };
    }
    if (evType === 'approval_requested') {
      return { kind: 'approval', desc: ('requested (' + (p.scope || '') + ') ' + (p.tool || '')).replace(/^\s+|\s+$/g, '') };
    }
    if (evType === 'approval_decided') {
      return { kind: 'approval', desc: ((p.decision || '') + ' (' + (p.scope || '') + ') ' + (p.tool || '')).replace(/^\s+|\s+$/g, '') };
    }
    if (evType === 'uak_event') {
      var ev = p.event || {};
      var t = String(ev.type || '');
      var src = ev.source || {};
      var srcName = String(src.name || '');
      var pl = ev.payload || {};
      if (t.indexOf('tool.') === 0) {
        return { kind: 'tool', desc: (t + ' ' + srcName).replace(/^\s+|\s+$/g, '') };
      }
      if (t.indexOf('llm.') === 0) {
        var model = String((pl && pl.model) || '');
        var tools = (pl && pl.tool_count != null) ? (' tools:' + pl.tool_count) : '';
        return { kind: 'llm', desc: (t + ' ' + model + tools).replace(/^\s+|\s+$/g, '') };
      }
      if (t.indexOf('approval.') === 0) {
        return { kind: 'approval', desc: (t + ' ' + srcName).replace(/^\s+|\s+$/g, '') };
      }
      if (t.indexOf('interrupt.') === 0) {
        return { kind: 'interrupt', desc: (t + ' ' + srcName).replace(/^\s+|\s+$/g, '') };
      }
      if (t.indexOf('run.') === 0) {
        return { kind: 'run', desc: (t + ' ' + srcName).replace(/^\s+|\s+$/g, '') };
      }
      if (t.indexOf('step.') === 0) {
        return { kind: 'step', desc: (t + ' ' + srcName).replace(/^\s+|\s+$/g, '') };
      }
      return { kind: 'uak', desc: (t + ' ' + srcName).replace(/^\s+|\s+$/g, '') };
    }
    return { kind: evType, desc: '' };
  }

  var lastSeq = 0;
  var lastTs = 0;
  var historyLoaded = false;
  var stepsSnapLastMs = 0;
  var goalSeeded = false;

  function _asFloat(v) {
    var n = Number(v);
    return isNaN(n) ? 0 : n;
  }

  function _asInt(v) {
    var n = parseInt(v, 10);
    return isNaN(n) ? 0 : n;
  }

  function _hasChatMessages() {
    try { return !!(chat && chat.querySelector && chat.querySelector('.chat-item')); } catch (e) { return false; }
  }

  function _seedGoalFromTask(task) {
    if (goalSeeded) return;
    if (_hasChatMessages()) { goalSeeded = true; return; }
    var g = '';
    try { g = String((task && task.goal) || '').replace(/^\\s+|\\s+$/g, ''); } catch (e0) { g = ''; }
    if (!g) return;
    try { addChat('user', g, 0); } catch (e1) {}
    goalSeeded = true;
  }

  function _applyEventRow(r) {
    if (!r) return;
    var ts = _asFloat(r.ts || 0);
    var seq = _asInt(r.seq || 0);
    var payload = r.payload || {};
    payload.step_id = r.step_id;
    if (r.type === 'chat_message') {
      addChat(payload.role || 'assistant', payload.content || '', ts);
    } else {
      try { _handleEvent(r.type, payload, ts); } catch (eH) {}
    }
    if (ts && ts > lastTs) lastTs = ts;
    if (seq && seq > lastSeq) lastSeq = seq;
  }

  function refreshStepsSnapshot(force) {
    var now = Date.now ? Date.now() : (new Date().getTime());
    if (!force && (now - stepsSnapLastMs) < 2500) return;
    stepsSnapLastMs = now;
    try { pollTask(false); } catch (e) {}
  }

  function loadHistory() {
    xhr('GET', '/api/tasks/' + encodeURIComponent(taskId) + '/events?tail=1&limit=600', null, 12000, function (status, text) {
      if (!(status >= 200 && status < 300)) { historyLoaded = true; return; }
      var list;
      try { list = JSON.parse(text || '[]'); } catch (e) { historyLoaded = true; return; }
      for (var i2 = 0; i2 < list.length; i2++) {
        try { _applyEventRow(list[i2] || {}); } catch (e2) {}
      }
      historyLoaded = true;
    });
  }

  function pollTask(withEvents) {
    xhr('GET', '/api/tasks/' + encodeURIComponent(taskId), null, 8000, function (status, text) {
      if (!(status >= 200 && status < 300)) { _noteNetFail(status, text); return; }
      _noteNetOk();
      var data;
      try { data = JSON.parse(text || '{}'); } catch (e) { return; }
      var t = data.task || {};
      applyTaskFields(t);
      try { _seedGoalFromTask(t); } catch (eSeed) {}
      try { refreshFiles(false); } catch (e0) {}
      var steps = data.steps || [];
      var waitId = '';
      var waitTool = '';
      for (var i3 = 0; i3 < steps.length; i3++) {
        var s = steps[i3] || {};
        if (s.id) applyStepFields(s.id, { status: s.status, name: s.name, idx: s.idx }, 0);
        try {
          var st = String(s.status || '').toLowerCase();
          if (!waitId && st === 'waiting_approval') {
            waitId = String(s.id || '');
            waitTool = String(s.tool || '');
          }
        } catch (eW) {}
      }
      try {
        var st2 = String((t && t.status) || '').toLowerCase();
        if (st2 === 'waiting_approval' && waitId) {
          if (!pendingApproval || pendingApproval.stepId !== waitId) _setPendingApproval(waitId, waitTool, '');
        } else if (pendingApproval && st2 !== 'waiting_approval') {
          _clearPendingApproval();
        }
      } catch (eW2) {}
    });
    if (withEvents === false || !historyLoaded) return;
    xhr('GET', '/api/tasks/' + encodeURIComponent(taskId) + '/events?after=' + encodeURIComponent(lastSeq) + '&limit=200', null, 8000, function (status, text) {
      if (!(status >= 200 && status < 300)) return;
      var list;
      try { list = JSON.parse(text || '[]'); } catch (e) { return; }
      for (var i4 = 0; i4 < list.length; i4++) {
        var r = list[i4] || {};
        var s = _asInt(r.seq || 0);
        if (s && s <= lastSeq) continue;
        try { _applyEventRow(r); } catch (eH2) {}
      }
    });
  }

  function connectSSE() {
    if (!window.EventSource) return false;
    try {
      var es = new EventSource('/api/events');
      var handle = function (evt) {
        try {
          var msg = JSON.parse(evt.data || '{}');
          var data = msg.data || {};
          var ts = msg.ts || 0;
          if (data.task_id && data.task_id !== taskId) return;
          if (evt.type === 'task_update') {
            applyTaskFields(data.fields || {});
            try {
              refreshFiles(false);
            } catch (e7) {}
          } else if (evt.type === 'step_update') {
            if (data.task_id !== taskId) return;
            applyStepFields(data.step_id, { status: (data.fields || {}).status }, ts);
          } else if (evt.type === 'approval_requested' || evt.type === 'approval_decided') {
            if (data.task_id !== taskId) return;
            try { _handleEvent(evt.type, data, ts); } catch (eA) {}
          } else if (evt.type === 'event_log') {
            if (data.task_id !== taskId) return;
            var seq = 0;
            try { seq = _asInt(data.seq || 0); } catch (eS0) { seq = 0; }
            var innerType = data.type || '';
            var innerPayload = data.payload || {};
            if (innerType === 'chat_message') {
              addChat(innerPayload.role || 'assistant', innerPayload.content || '', ts);
              if (seq && seq > lastSeq) lastSeq = seq;
              if (ts && ts > lastTs) lastTs = ts;
              return;
            }
            if (innerType === 'uak_event') {
              try { _handleEvent('uak_event', innerPayload, ts); } catch (eU) {}
              if (seq && seq > lastSeq) lastSeq = seq;
            }
          }
          if (ts && ts > lastTs) lastTs = ts;
        } catch (e) {}
      };
      es.addEventListener('task_update', handle);
      es.addEventListener('step_update', handle);
      es.addEventListener('approval_requested', handle);
      es.addEventListener('approval_decided', handle);
      es.addEventListener('event_log', handle);
      return true;
    } catch (e) {
      return false;
    }
  }

  function sendChat() {
    if (!chatInput) return;
    var msg = (chatInput.value || '').replace(/^\\s+|\\s+$/g, '');
    if (!msg) return;

    var curStatus = '';
    try { curStatus = String(taskStatusEl ? (taskStatusEl.textContent || '') : '').replace(/^\\s+|\\s+$/g, '').toLowerCase(); } catch (eS0) { curStatus = ''; }
    var zh = _isZh();

    if (chatSend) chatSend.disabled = true;
    if (chatStatus) {
      try { chatStatus.textContent = (chatForm && chatForm.getAttribute('data-sending')) || 'Sending…'; } catch (e) { chatStatus.textContent = 'Sending…'; }
    }

    function _parseApprovalDecision(s) {
      var raw = String(s || '').replace(/^\\s+|\\s+$/g, '');
      if (!raw) return '';
      // Reject first: "不同意" contains "同意".
      if (raw.indexOf('拒绝') >= 0 || raw.indexOf('不同意') >= 0 || raw.indexOf('不允许') >= 0) return 'reject';
      var low = raw.toLowerCase();
      if (low === 'no' || low === 'n' || low === 'reject' || low === 'deny' || low === 'refuse') return 'reject';
      if (raw.indexOf('同意') >= 0 || raw.indexOf('允许') >= 0) return 'approve';
      if (low === 'yes' || low === 'y' || low === 'ok' || low === 'approve' || low === 'allow') return 'approve';
      return '';
    }

    // If task is waiting for approval, interpret the chat reply as an approval decision.
    if (curStatus === 'waiting_approval') {
      var decision = _parseApprovalDecision(msg);
      if (!decision) {
        if (chatSend) chatSend.disabled = false;
        if (chatStatus) chatStatus.textContent = zh ? '当前需要你确认：回复“同意”或“拒绝”。' : 'Approval required: reply “approve” or “reject”.';
        return;
      }
      var sid = '';
      try { sid = String((pendingApproval && pendingApproval.stepId) || '').replace(/^\\s+|\\s+$/g, ''); } catch (eSid) { sid = ''; }
      if (!sid) {
        if (chatSend) chatSend.disabled = false;
        if (chatStatus) chatStatus.textContent = zh ? '未找到待确认的步骤，请稍后重试。' : 'No pending approval step found. Please retry.';
        try { pollTask(false); } catch (ePT) {}
        return;
      }

      try { addChat('user', msg, 0); } catch (eEcho) {}
      var url2 = '/api/tasks/' + encodeURIComponent(taskId) + '/approve/' + encodeURIComponent(sid) + '?token=' + encodeURIComponent(adminToken);
      xhr('POST', url2, { decision: decision, reason: '' }, 12000, function (status, text) {
        if (chatSend) chatSend.disabled = false;
        if (status >= 200 && status < 300) {
          _noteNetOk();
          chatInput.value = '';
          if (chatStatus) chatStatus.textContent = '';
          try { _clearPendingApproval(); } catch (eC) {}
          try { pollTask(false); } catch (eP2) {}
          return;
        }
        _noteNetFail(status, text);
        if (chatStatus) chatStatus.textContent = (text || '').slice(0, 200) || ('HTTP ' + status);
      });
      return;
    }

    try { addChat('user', msg, 0); } catch (eEcho2) {}
    xhr('POST', '/api/tasks/' + encodeURIComponent(taskId) + '/continue?token=' + encodeURIComponent(adminToken), { message: msg }, 12000, function (status, text) {
      if (chatSend) chatSend.disabled = false;
      if (status >= 200 && status < 300) {
        _noteNetOk();
        chatInput.value = '';
        if (chatStatus) chatStatus.textContent = '';
      } else {
        _noteNetFail(status, text);
        if (chatStatus) chatStatus.textContent = (text || '').slice(0, 200) || ('HTTP ' + status);
      }
    });
  }

  if (chatForm && window.XMLHttpRequest) {
    try { ensureChatPlaceholder(); } catch (e) {}
    chatForm.onsubmit = function (e) {
      if (e && e.preventDefault) e.preventDefault();
      try { sendChat(); } catch (err) { return true; }
      return false;
    };
  }

  if (cancelForm && window.XMLHttpRequest) {
    cancelForm.onsubmit = function (e) {
      if (e && e.preventDefault) e.preventDefault();
      try { cancelTask(); } catch (err) { return true; }
      return false;
    };
  } else if (cancelBtn) {
    cancelBtn.addEventListener('click', function (e) {
      try { if (e && e.preventDefault) e.preventDefault(); } catch (e0) {}
      try { cancelTask(); } catch (e1) {}
    });
  }

  try { ensureChatPlaceholder(); } catch (e) {}
  try {
    var g0 = '';
    try { g0 = String(root.getAttribute('data-goal') || '').replace(/^\\s+|\\s+$/g, ''); } catch (e0) { g0 = ''; }
    if (g0) _seedGoalFromTask({ goal: g0 });
  } catch (eG) {}
  try { updateCancelUI(taskStatusEl ? (taskStatusEl.textContent || '') : ''); } catch (e0) {}
  try { _ensureStepsPlaceholder(); } catch (eP) {}
  loadHistory();
  try { pollTask(false); } catch (eSnap) {}
  try { refreshFiles(true); } catch (eInit) {}
  try {
    setInterval(function () {
      try { if (_shouldPollFiles()) refreshFiles(false); } catch (e) {}
    }, 3500);
  } catch (e2) {}
  try { connectSSE(); } catch (eSse) {}
  // Polling stays enabled even when SSE is available; some embedded WebViews have flaky EventSource delivery.
  try { setInterval(pollTask, 2000); } catch (ePoll) {}
})();

(function () {
  function getBodyAttr(name) {
    try {
      return (document.body && document.body.getAttribute(name)) || '';
    } catch (e) {
      return '';
    }
  }

  // Left sidebar collapse (persisted)
  (function () {
    var btn = null;
    try { btn = document.getElementById('sidebar-toggle'); } catch (e0) { btn = null; }
    var KEY = 'owb.sidebarCollapsed';
    function _get() {
      try { return (window.localStorage && window.localStorage.getItem(KEY) === '1'); } catch (e) { return false; }
    }
    function _apply(v) {
      try { if (document.body && document.body.classList) document.body.classList.toggle('sidebar-collapsed', !!v); } catch (e) {}
    }
    function _set(v) {
      try { if (window.localStorage) window.localStorage.setItem(KEY, v ? '1' : '0'); } catch (e) {}
      _apply(v);
    }
    _apply(_get());
    if (btn) {
      btn.addEventListener('click', function () {
        _set(!_get());
      });
    }
  })();

  // Sidebar splitter (persisted width)
  (function () {
    var split = null;
    var sidebar = null;
    try { split = document.getElementById('sidebar-splitter'); } catch (e0) { split = null; }
    try { sidebar = document.querySelector('.sidebar'); } catch (e1) { sidebar = null; }
    if (!split || !sidebar) return;

    var KEY = 'owb.sidebarWidth';
    function _get() {
      try {
        var n = parseInt((window.localStorage && window.localStorage.getItem(KEY)) || '', 10);
        return isNaN(n) ? 0 : n;
      } catch (e) {
        return 0;
      }
    }
    function _apply(px) {
      try { document.documentElement.style.setProperty('--owb-sidebar-w', String(px) + 'px'); } catch (e) {}
    }
    var saved = _get();
    if (saved) _apply(saved);

    var dragging = false;
    var startX = 0;
    var startW = 0;
    var lastW = 0;
    var downEvt = (window.PointerEvent ? 'pointerdown' : 'mousedown');
    var moveEvt = (window.PointerEvent ? 'pointermove' : 'mousemove');
    var upEvt = (window.PointerEvent ? 'pointerup' : 'mouseup');
    var cancelEvt = (window.PointerEvent ? 'pointercancel' : 'mouseleave');

    function clamp(w) {
      var min = 180;
      var max = 520;
      // Don't fight the collapsed mode; that's handled by the toggle.
      try { if (document.body && document.body.classList && document.body.classList.contains('sidebar-collapsed')) return w; } catch (e0) {}
      return Math.max(min, Math.min(max, w));
    }

    function onMove(e) {
      if (!dragging) return;
      var dx = 0;
      try { dx = Number((e && e.clientX) || 0) - startX; } catch (e0) { dx = 0; }
      var w = clamp(startW + dx);
      lastW = w;
      _apply(w);
    }

    function stop() {
      if (!dragging) return;
      dragging = false;
      try { split.classList.remove('dragging'); } catch (e0) {}
      try {
        if (window.localStorage && lastW) window.localStorage.setItem(KEY, String(parseInt(lastW, 10) || 0));
      } catch (e1) {}
      try { window.removeEventListener(moveEvt, onMove); } catch (e2) {}
      try { window.removeEventListener(upEvt, stop); } catch (e3) {}
      try { window.removeEventListener(cancelEvt, stop); } catch (e4) {}
    }

    split.addEventListener(downEvt, function (e) {
      try { if (document.body && document.body.classList && document.body.classList.contains('sidebar-collapsed')) return; } catch (e0) {}
      try {
        if (e && e.button != null && e.button !== 0) return;
        if (e && e.preventDefault) e.preventDefault();
      } catch (e1) {}
      dragging = true;
      try { split.classList.add('dragging'); } catch (e2) {}
      try { startX = Number((e && e.clientX) || 0); } catch (e3) { startX = 0; }
      try { startW = sidebar.getBoundingClientRect().width || 0; } catch (e4) { startW = 0; }
      lastW = startW;
      try { window.addEventListener(moveEvt, onMove); } catch (e5) {}
      try { window.addEventListener(upEvt, stop); } catch (e6) {}
      try { window.addEventListener(cancelEvt, stop); } catch (e7) {}
    });
  })();

  function xhr(method, url, cb) {
    try {
      var r = new XMLHttpRequest();
      r.open(method, url, true);
      r.timeout = 30000;
      r.onreadystatechange = function () {
        if (r.readyState !== 4) return;
        cb(r.status, r.responseText || '');
      };
      r.ontimeout = function () { cb(0, 'timeout'); };
      r.onerror = function () { cb(0, 'error'); };
      r.send(null);
    } catch (e) {
      cb(0, 'exception');
    }
  }

  function closestTaskItem(el) {
    var cur = el;
    while (cur && cur !== document.body) {
      try {
        if (cur.classList && cur.classList.contains('task-item')) return cur;
      } catch (e) {}
      cur = cur.parentNode;
    }
    return null;
  }

  function _toast(msg, ok) {
    var text = String(msg || '').replace(/^\\s+|\\s+$/g, '');
    if (!text) return;
    try {
      var el = document.createElement('div');
      el.className = 'owb-toast ' + (ok ? 'ok' : 'fail');
      el.textContent = text;
      document.body.appendChild(el);
      setTimeout(function () {
        try { if (el && el.remove) el.remove(); else if (el && el.parentNode) el.parentNode.removeChild(el); } catch (e) {}
      }, 2600);
    } catch (e2) {}
  }

  function _confirmModal(message) {
    return new Promise(function (resolve) {
      var zh = false;
      try { zh = document.documentElement.lang.indexOf('zh') === 0; } catch (e0) { zh = false; }
      var title = zh ? '确认删除' : 'Confirm delete';
      var cancelLabel = zh ? '取消' : 'Cancel';
      var okLabel = zh ? '删除' : 'Delete';

      var back = document.createElement('div');
      back.className = 'owb-modal-backdrop';
      var modal = document.createElement('div');
      modal.className = 'owb-modal';
      var t = document.createElement('div');
      t.className = 'owb-modal-title';
      t.textContent = title;
      var b = document.createElement('div');
      b.className = 'owb-modal-body';
      b.textContent = String(message || '');
      var actions = document.createElement('div');
      actions.className = 'owb-modal-actions';
      var cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.textContent = cancelLabel;
      cancelBtn.setAttribute('data-no-copy', '1');
      var okBtn = document.createElement('button');
      okBtn.type = 'button';
      okBtn.className = 'danger-btn';
      okBtn.textContent = okLabel;
      okBtn.setAttribute('data-no-copy', '1');
      actions.appendChild(cancelBtn);
      actions.appendChild(okBtn);
      modal.appendChild(t);
      modal.appendChild(b);
      modal.appendChild(actions);
      back.appendChild(modal);

      function done(v) {
        try { if (back && back.remove) back.remove(); else if (back && back.parentNode) back.parentNode.removeChild(back); } catch (e) {}
        resolve(!!v);
      }

      back.addEventListener('click', function (e) {
        try { if (e && e.target === back) done(false); } catch (e0) { done(false); }
      });
      cancelBtn.addEventListener('click', function () { done(false); });
      okBtn.addEventListener('click', function () { done(true); });

      try { document.body.appendChild(back); } catch (e1) { resolve(false); return; }
      try { okBtn.focus(); } catch (e2) {}
    });
  }

  function _handleDeleteClick(btn) {
    if (!btn) return;
    var id = btn.getAttribute('data-task-id') || '';
    if (!id) return;
    var confirmMsg = getBodyAttr('data-delete-confirm') || 'Delete this task?';
    _confirmModal(confirmMsg).then(function (yes) {
      if (!yes) return;
      var zh = false;
      try { zh = document.documentElement.lang.indexOf('zh') === 0; } catch (e0) { zh = false; }
      var deleting = zh ? '删除中…' : 'Deleting…';

      var oldText = '';
      try { oldText = btn.textContent || ''; } catch (e1) { oldText = ''; }
      try { btn.disabled = true; } catch (e2) {}
      try { btn.textContent = '…'; } catch (e3) {}
      _toast(deleting, true);

      var token = getBodyAttr('data-admin-token') || '';
      var url = '/api/tasks/' + encodeURIComponent(id);
      if (token) url += '?token=' + encodeURIComponent(token);

      function doDelete(attempt) {
        xhr('DELETE', url, function (status, text) {
          // SQLite may be temporarily busy while another run is streaming. Retry a few times automatically.
          if ((status === 0 || status === 503) && attempt < 4) {
            var wait = 200 * (attempt + 1) * (attempt + 1);
            setTimeout(function () { doDelete(attempt + 1); }, wait);
            return;
          }
          try { btn.disabled = false; } catch (e4) {}
          try { btn.textContent = oldText || '🗑'; } catch (e5) {}
          if (status >= 200 && status < 300) {
            var item = closestTaskItem(btn);
            if (item && item.remove) item.remove();
            _toast(zh ? '已删除' : 'Deleted', true);
            try {
              if (location && location.pathname && location.pathname.indexOf('/tasks/' + id) === 0) {
                location.href = '/';
              }
            } catch (e6) {}
            return;
          }
          var failMsg = getBodyAttr('data-delete-failed') || (zh ? '删除失败。' : 'Delete failed.');
          var msg = String((text || '')).replace(/^\\s+|\\s+$/g, '');
          _toast(msg.slice(0, 220) || failMsg, false);
        });
      }

      doDelete(0);
    });
  }

  // Use capture-phase delegation so nested <a> wrappers don't swallow the click.
  document.addEventListener('click', function (e) {
    var t = e && e.target ? e.target : null;
    if (!t) return;
    var btn = null;
    try {
      btn = (t.closest && t.closest('button.task-del[data-task-id]')) ? t.closest('button.task-del[data-task-id]') : null;
    } catch (e0) {
      btn = null;
    }
    if (!btn) return;
    try { if (e && e.preventDefault) e.preventDefault(); } catch (e1) {}
    try { if (e && e.stopPropagation) e.stopPropagation(); } catch (e2) {}
    try { if (e && e.stopImmediatePropagation) e.stopImmediatePropagation(); } catch (e3) {}
    _handleDeleteClick(btn);
  }, true);
})();
