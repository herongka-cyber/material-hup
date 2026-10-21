/* =========================================================
   素材管家 · 前端应用 v2.48
   ========================================================= */

(function () {
  'use strict';

  // ------------------ 状态 ------------------
  const state = {
    mediaType: '',
    favoriteOnly: false,
    q: '',
    selectedTags: [],
    minBpm: null,
    maxBpm: null,
    minEnergy: null,
    maxEnergy: null,
    minRating: null,
    sort: 'created_at',
    order: 'desc',
    page: 1,
    pageSize: 100,
    totalMedia: 0,
    loadingMore: false,
    view: 'grid',
    selectMode: false,
    selectedIds: new Set(),
    activePlaylistId: null,
    allMedia: [],
    currentIndex: -1,
    currentMedia: null,
    detailMediaId: null,
    allTags: [],
    playlists: [],
    scanTaskId: null,
    scanPollTimer: null,
    backfillTaskId: null,
    backfillPollTimer: null,
    audio: null,
    playlistQueue: null,
    playQueue: [],        // 播放队列：[{id, title}]，下一首优先播完
    queueResume: null,    // 队列播完后恢复的列表位置 {currentIndex}
    _fromQueue: false,    // 内部标记：本次 playMedia 是否来自队列
    pendingDeletePlaylistId: null,
    playlistEditingId: null,
    sidebarOpen: false,
    exportingPlaylistId: null,
    exportInProgress: false,
    exportSucceeded: false,
    playMode: 'sequence',
    lastShuffledId: null,
    playbackRate: 1,
  };

  const MOBILE_BREAKPOINT = 640;
  const PLAY_MODE_STORAGE_KEY = 'mh.playMode';
  const PLAY_MODES = ['sequence', 'repeat-list', 'repeat-one', 'shuffle'];
  const PLAY_MODE_META = {
    'sequence':    { icon: '⇉',  title: '顺序播放' },
    'repeat-list': { icon: '🔁', title: '列表循环' },
    'repeat-one':  { icon: '🔂', title: '单曲循环' },
    'shuffle':     { icon: '🔀', title: '随机播放' },
  };
  const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2];
  const PLAYBACK_RATE_STORAGE_KEY = 'mh.playbackRate';

  // ------------------ 工具 ------------------
  function $(id) { return document.getElementById(id); }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function fmtDuration(s) {
    if (s == null || isNaN(s)) return '';
    const n = Number(s);
    if (n < 1) {
      return `${n.toFixed(1)}s`;
    }
    const total = Math.floor(n);
    const m = Math.floor(total / 60);
    const sec = total % 60;
    return `${m}:${String(sec).padStart(2, '0')}`;
  }

  function fmtSize(b) {
    if (b == null) return '';
    if (b < 1024) return b + ' B';
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
    if (b < 1024 * 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB';
    return (b / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }

  function toast(msg, type, duration) {
    type = type || 'info';
    duration = duration || 3000;
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    $('toastContainer').appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 320);
    }, duration);
  }

  function prettyTitle(m) {
    if (m.title && m.title.trim()) return m.title.trim();
    let name = m.file_name || '';
    name = name.replace(/\.[^.]+$/, '');
    name = name.replace(/^\d{4,}[\s._-]+/, '');
    name = name.replace(/[_-]+/g, ' ');
    name = name.replace(/\s+/g, ' ').trim();
    name = name.replace(/\b([A-Z]{3,})\b/g, (s) =>
      s.charAt(0) + s.slice(1).toLowerCase()
    );
    return name || (m.file_name || '未命名');
  }

  function hashString(s) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
  }

  const PALETTE = [
    ['#3a4a6b', '#1f2633'],
    ['#6b4a3a', '#2e1f18'],
    ['#4a6b3a', '#1f2e1a'],
    ['#5a3a6b', '#2a1d33'],
    ['#6b5a3a', '#2e2818'],
    ['#3a6b6b', '#1a2e2e'],
    ['#6b3a4a', '#2e181f'],
    ['#3a3a6b', '#1a1a2e'],
    ['#6b4a6b', '#2e1f2e'],
    ['#4a6b6b', '#1f2e2e'],
    ['#6b6b3a', '#2e2e18'],
    ['#3a5a6b', '#18262e'],
  ];

  function paletteFor(key) {
    const h = hashString(String(key));
    return PALETTE[h % PALETTE.length];
  }

  function audioCoverBg(key) {
    const [c1, c2] = paletteFor(key);
    return `linear-gradient(135deg, ${c1} 0%, ${c2} 100%)`;
  }

  function waveformEnvelope(key, count) {
    const h = hashString(String(key));
    const kind = h % 6;
    const out = new Array(count);

    for (let i = 0; i < count; i++) {
      const t = i / (count - 1);
      let v;
      if (kind === 0) {
        v = Math.sin(t * Math.PI);
      } else if (kind === 1) {
        v = Math.pow(1 - t, 0.85) * 0.9 + 0.1;
      } else if (kind === 2) {
        v = Math.pow(t, 0.85) * 0.9 + 0.1;
      } else if (kind === 3) {
        v = 1 - 0.75 * Math.sin(t * Math.PI);
      } else if (kind === 4) {
        if (t < 0.6) v = 0.85 + 0.1 * Math.sin(t * 8);
        else v = 0.85 - (t - 0.6) / 0.4 * 0.6;
      } else {
        v = 0.55 + 0.4 * Math.abs(Math.sin(t * Math.PI * 2.2));
      }
      out[i] = 0.25 + Math.max(0, Math.min(1, v)) * 0.75;
    }
    return out;
  }

  function waveformBars(key, count) {
    const h = hashString(String(key));
    const env = waveformEnvelope(key, count);
    const out = new Array(count);
    for (let i = 0; i < count; i++) {
      const bits = (h >>> ((i * 5) % 32)) & 0xff;
      const base = 0.25 + (bits / 255) * 0.75;
      const v = env[i] * base;
      out[i] = Math.round(8 + v * 84);
    }
    return out;
  }

  function buildWavePath(bars, viewWidth, xPad, barRatio) {
    const total = bars.length;
    const step = viewWidth / total;
    const barW = step * barRatio;
    let d = '';
    for (let i = 0; i < total; i++) {
      const h = bars[i];
      const x = (i * step + step * xPad).toFixed(2);
      const y = ((100 - h) / 2).toFixed(2);
      const w = barW.toFixed(2);
      const hh = h.toFixed(2);
      d += `M${x} ${y}h${w}v${hh}h-${w}Z`;
    }
    return d;
  }

  function audioWaveformSvg(key) {
    const bars = waveformBars(key, 48);
    const d = buildWavePath(bars, 100, 0.19, 0.62);
    return `<svg class="thumb-wave" viewBox="0 0 100 100" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg"><path d="${d}" fill="rgba(255,255,255,0.26)"/></svg>`;
  }

  function detailWaveformSvg(key) {
    const id = String(key).replace(/[^a-zA-Z0-9_-]/g, '');
    const gradId = `wfade-${id}`;
    const bars = waveformBars(key, 72);
    const d = buildWavePath(bars, 100, 0.19, 0.62);
    return `
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"  stop-color="rgba(255,255,255,0.30)"/>
            <stop offset="45%" stop-color="rgba(255,255,255,0.30)"/>
            <stop offset="85%" stop-color="rgba(255,255,255,0.10)"/>
            <stop offset="100%" stop-color="rgba(255,255,255,0)"/>
          </linearGradient>
        </defs>
        <path d="${d}" fill="url(#${gradId})"/>
      </svg>
    `;
  }

  function detailNoteSvg() {
    return `
      <svg class="dc-note" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <g fill="none" stroke="rgba(255,255,255,0.85)" stroke-width="3.2"
           stroke-linecap="round" stroke-linejoin="round">
          <ellipse cx="32" cy="72" rx="11" ry="8" transform="rotate(-18 32 72)"/>
          <line x1="42" y1="70" x2="42" y2="24"/>
          <path d="M42 24 Q 58 30 62 44"/>
        </g>
      </svg>
    `;
  }

  function rowWaveformSvg(key) {
    const bars = waveformBars(key, 24);
    const d = buildWavePath(bars, 100, 0.19, 0.62);
    return `<svg class="row-wave" viewBox="0 0 100 100" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg"><path d="${d}" fill="rgba(255,255,255,0.40)"/></svg>`;
  }

  function detectPlatformShortcut() {
    let platform = '';
    try {
      if (navigator.userAgentData && navigator.userAgentData.platform) {
        platform = navigator.userAgentData.platform;
      } else if (navigator.platform) {
        platform = navigator.platform;
      } else if (navigator.userAgent) {
        platform = navigator.userAgent;
      }
    } catch (_) {
      platform = '';
    }
    const isMac = /Mac|iPhone|iPad|iPod/i.test(platform);
    const hintEl = $('searchHint');
    if (hintEl) {
      hintEl.textContent = isMac ? '⌘K' : 'Ctrl K';
    }
  }

  // ------------------ 播放模式（持久化） ------------------
  function loadPlayMode() {
    try {
      const raw = localStorage.getItem(PLAY_MODE_STORAGE_KEY);
      if (raw && PLAY_MODES.indexOf(raw) >= 0) {
        return raw;
      }
    } catch (_) { /* ignore */ }
    return 'sequence';
  }

  function savePlayMode(mode) {
    try {
      localStorage.setItem(PLAY_MODE_STORAGE_KEY, mode);
    } catch (_) { /* ignore */ }
  }

  function updatePlayModeButton() {
    const btn = $('playModeBtn');
    if (!btn) return;
    const meta = PLAY_MODE_META[state.playMode] || PLAY_MODE_META['sequence'];
    btn.textContent = meta.icon;
    btn.title = meta.title;
  }

  function cyclePlayMode() {
    const idx = PLAY_MODES.indexOf(state.playMode);
    const nextIdx = (idx + 1) % PLAY_MODES.length;
    state.playMode = PLAY_MODES[nextIdx];
    savePlayMode(state.playMode);
    updatePlayModeButton();
    const meta = PLAY_MODE_META[state.playMode];
    toast('播放模式：' + meta.title, 'info', 1500);
  }

  // ------------------ 播放倍速（持久化） ------------------
  function fmtRate(r) {
    // 始终保留 1 位小数；超过 1 位小数的保留原样
    const n = Number(r);
    if (!isFinite(n)) return '1.0x';
    const str = String(n);
    if (str.indexOf('.') < 0) {
      return str + '.0x';
    }
    if (str.split('.')[1].length === 1) {
      return str + 'x';
    }
    return str + 'x';
  }

  function loadPlaybackRate() {
    try {
      const raw = localStorage.getItem(PLAYBACK_RATE_STORAGE_KEY);
      if (raw != null) {
        const n = Number(raw);
        if (PLAYBACK_RATES.indexOf(n) >= 0) {
          return n;
        }
      }
    } catch (_) { /* ignore */ }
    return 1;
  }

  function savePlaybackRate(rate) {
    try {
      localStorage.setItem(PLAYBACK_RATE_STORAGE_KEY, String(rate));
    } catch (_) { /* ignore */ }
  }

  function applyPlaybackRate() {
    const audio = state.audio;
    if (audio) {
      try { audio.playbackRate = state.playbackRate; } catch (_) { /* ignore */ }
    }
    const btn = $('playRateBtn');
    if (btn) {
      const label = fmtRate(state.playbackRate);
      btn.textContent = label;
      btn.title = '播放速度 ' + label;
    }
  }

  function cyclePlaybackRate() {
    const idx = PLAYBACK_RATES.indexOf(state.playbackRate);
    const nextIdx = idx < 0 ? 2 : (idx + 1) % PLAYBACK_RATES.length;
    state.playbackRate = PLAYBACK_RATES[nextIdx];
    savePlaybackRate(state.playbackRate);
    applyPlaybackRate();
    toast('播放速度：' + fmtRate(state.playbackRate), 'info', 1500);
  }

  // ------------------ 播放列表/队列工具 ------------------
  function getPlaybackList() {
    if (state.playlistQueue && state.playlistQueue.mediaIds && state.playlistQueue.mediaIds.length) {
      return {
        kind: 'playlist',
        list: state.playlistQueue.mediaIds.slice(),
        currentId: state.playlistQueue.mediaIds[state.playlistQueue.index],
      };
    }
    return {
      kind: 'view',
      list: state.allMedia.map(x => x.id),
      currentId: state.currentMedia ? state.currentMedia.id : null,
    };
  }

  function pickRandomFromList(list, excludeId) {
    if (!list || !list.length) return null;
    if (list.length === 1) return list[0];
    const candidates = list.filter(id => id !== excludeId);
    if (!candidates.length) {
      return list[Math.floor(Math.random() * list.length)];
    }
    return candidates[Math.floor(Math.random() * candidates.length)];
  }

  function playById(mediaId) {
    if (mediaId == null) return false;

    if (state.playlistQueue && state.playlistQueue.mediaIds && state.playlistQueue.mediaIds.indexOf(mediaId) >= 0) {
      state.playlistQueue.index = state.playlistQueue.mediaIds.indexOf(mediaId);
      return playMediaByMediaId(mediaId);
    }

    const idx = state.allMedia.findIndex(x => x.id === mediaId);
    if (idx >= 0) {
      playByIndex(idx);
      return true;
    }
    return playMediaByMediaId(mediaId);
  }

  function playRandomNext() {
    const { list } = getPlaybackList();
    const excludeId = state.lastShuffledId;
    const picked = pickRandomFromList(list, excludeId);
    if (picked == null) {
      toast('没有可播放的素材', 'info', 2000);
      return;
    }
    state.lastShuffledId = picked;
    playById(picked);
  }

  // ---- 定时关闭 ----
  const SLEEP_STEPS = [
    { label: '关闭', value: 0 },
    { label: '30 分钟', value: 30 },
    { label: '20 分钟', value: 20 },
    { label: '10 分钟', value: 10 },
    { label: '播完这首', value: -1 },
  ];
  let sleepTimerId = null;

  function updateSleepBtn() {
    const btn = $('sleepBtn');
    if (!btn) return;
    const s = SLEEP_STEPS[state.sleepStepIdx || 0];
    btn.textContent = s.value === 0 ? '⏱' : (s.value === -1 ? '⏱尾' : `⏱${s.value}`);
    btn.title = '定时关闭：' + s.label;
    btn.classList.toggle('active', s.value !== 0);
  }

  function cycleSleep() {
    state.sleepStepIdx = ((state.sleepStepIdx || 0) + 1) % SLEEP_STEPS.length;
    const s = SLEEP_STEPS[state.sleepStepIdx];
    if (sleepTimerId) { clearTimeout(sleepTimerId); sleepTimerId = null; }
    state.sleepAfterCurrent = false;
    if (s.value > 0) {
      sleepTimerId = setTimeout(() => {
        sleepTimerId = null;
        state.sleepStepIdx = 0;
        updateSleepBtn();
        const audio = state.audio;
        if (audio && !audio.paused) audio.pause();
        $('playBtn').textContent = '▶';
        syncPlayingState();
        toast(`定时到了（${s.label}），已暂停`, 'info', 2500);
      }, s.value * 60 * 1000);
      toast(`定时关闭：${s.label}后暂停`, 'success', 1500);
    } else if (s.value === -1) {
      state.sleepAfterCurrent = true;
      toast('定时关闭：播完这首就停', 'success', 1500);
    } else {
      toast('定时关闭已取消', 'info', 1200);
    }
    updateSleepBtn();
  }

  // ---- 播放队列（下一首播放） ----
  function queueNext(m) {
    if (!m || !isPlayable(m)) return;
    if (state.queueResume == null && state.currentIndex >= 0) {
      state.queueResume = { currentIndex: state.currentIndex };
    }
    state.playQueue = state.playQueue.filter(q => q.id !== m.id);
    state.playQueue.unshift({ id: m.id, title: prettyTitle(m) });
    updateQueueBadge();
    renderQueuePopup();
    toast(`下一首播放：${prettyTitle(m)}`, 'success', 1500);
  }

  function dequeueById(id) {
    state.playQueue = state.playQueue.filter(q => q.id !== id);
    updateQueueBadge();
    renderQueuePopup();
  }

  function clearPlayQueue() {
    state.playQueue = [];
    updateQueueBadge();
    renderQueuePopup();
  }

  function updateQueueBadge() {
    const badge = $('queueBadge');
    if (!badge) return;
    const n = state.playQueue.length;
    badge.style.display = n ? '' : 'none';
    badge.textContent = n > 99 ? '99+' : String(n);
  }

  function renderQueuePopup() {
    const list = $('queueList');
    if (!list) return;
    if (!state.playQueue.length) {
      list.innerHTML = '<div class="cleanup-empty">队列是空的 — 在卡片或详情页点「下一首播放」把素材加进来。</div>';
      return;
    }
    list.innerHTML = state.playQueue.map((q, i) =>
      `<div class="queue-item"><span class="queue-idx">${i + 1}</span>` +
      `<span class="queue-title">${escapeHtml(q.title)}</span>` +
      `<button class="queue-del" data-queue-del="${q.id}" title="移出队列">×</button></div>`
    ).join('');
  }

  function toggleQueuePopup(show) {
    const pop = $('queuePopup');
    if (!pop) return;
    const willShow = show == null ? pop.style.display !== 'flex' : show;
    if (willShow) renderQueuePopup();
    pop.style.display = willShow ? 'flex' : 'none';
  }

  function playNextAuto() {
    if (state.sleepAfterCurrent) {
      state.sleepAfterCurrent = false;
      state.sleepStepIdx = 0;
      updateSleepBtn();
      toast('定时关闭：这首播完了，已停', 'info', 2000);
      return;
    }
    // 播放队列优先：播完后恢复之前的列表位置
    while (state.playQueue.length) {
      const q = state.playQueue.shift();
      updateQueueBadge();
      state._fromQueue = true;
      if (playMediaByMediaId(q.id)) return;
    }
    if (state.queueResume) {
      state.currentIndex = state.queueResume.currentIndex;
      state.queueResume = null;
    }
    if (state.playMode === 'repeat-one') {
      const audio = state.audio;
      if (audio && audio.src) {
        audio.currentTime = 0;
        audio.play().then(() => {
          $('playBtn').textContent = '⏸';
          syncPlayingState();
        }).catch(() => {});
      }
      return;
    }
    if (state.playMode === 'shuffle') {
      playRandomNext();
      return;
    }
    if (state.playMode === 'repeat-list') {
      playNextRepeatList();
      return;
    }
    playNextSequence();
  }

  function playNextSequence() {
    if (state.playlistQueue) {
      const q = state.playlistQueue;
      const next = q.index + 1;
      if (next < q.mediaIds.length) {
        q.index = next;
        playMediaByMediaId(q.mediaIds[next]);
        return;
      }
      state.playlistQueue = null;
      toast('播放完毕', 'info', 2000);
      return;
    }

    if (state.currentIndex < 0) return;
    const i = findPlayable(state.currentIndex, 1);
    if (i > state.currentIndex) {
      playByIndex(i);
      return;
    }
    toast('播放完毕', 'info', 2000);
  }

  function playNextRepeatList() {
    if (state.playlistQueue) {
      const q = state.playlistQueue;
      const next = (q.index + 1) % q.mediaIds.length;
      q.index = next;
      playMediaByMediaId(q.mediaIds[next]);
      return;
    }
    if (state.currentIndex < 0) return;
    const i = findPlayable(state.currentIndex, 1);
    if (i >= 0) playByIndex(i);
  }

  function playNextManual() {
    if (state.playMode === 'shuffle') {
      playRandomNext();
      return;
    }
    if (state.playMode === 'repeat-one') {
      playNextSequence();
      return;
    }
    if (state.playMode === 'repeat-list') {
      playNextRepeatList();
      return;
    }
    playNextSequence();
  }

  // ------------------ 焦点管理（弹窗专用） ------------------
  const _modalFocusState = new WeakMap();

  function getFocusableInModal(modalEl) {
    const sel = 'input, select, textarea, button, [href], [tabindex]:not([tabindex="-1"])';
    const all = Array.from(modalEl.querySelectorAll(sel));
    return all.filter(el => {
      if (el.disabled) return false;
      if (el.getAttribute('tabindex') === '-1') return false;
      const rects = el.getClientRects();
      return rects.length > 0 && (el.offsetWidth > 0 || el.offsetHeight > 0);
    });
  }

  function openModalFocus(modalEl) {
    if (!modalEl) return;
    if (_modalFocusState.has(modalEl)) {
      closeModalFocus(modalEl);
    }
    const prevFocus = document.activeElement;

    const handler = (e) => {
      if (e.key !== 'Tab') return;
      const list = getFocusableInModal(modalEl);
      if (!list.length) {
        e.preventDefault();
        if (!modalEl.hasAttribute('tabindex')) {
          modalEl.setAttribute('tabindex', '-1');
        }
        modalEl.focus();
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      const active = document.activeElement;

      if (!modalEl.contains(active)) {
        e.preventDefault();
        first.focus();
        return;
      }

      if (e.shiftKey) {
        if (active === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', handler, true);

    _modalFocusState.set(modalEl, { prevFocus, handler });

    const target = getFocusableInModal(modalEl)[0];
    if (target) {
      requestAnimationFrame(() => {
        try { target.focus(); } catch (_) { /* ignore */ }
      });
    } else {
      if (!modalEl.hasAttribute('tabindex')) {
        modalEl.setAttribute('tabindex', '-1');
      }
      modalEl.focus();
    }
  }

  function closeModalFocus(modalEl) {
    if (!modalEl) return;
    const st = _modalFocusState.get(modalEl);
    if (!st) return;
    document.removeEventListener('keydown', st.handler, true);
    _modalFocusState.delete(modalEl);
    const prev = st.prevFocus;
    if (prev && document.contains(prev)) {
      try { prev.focus(); } catch (_) { /* ignore */ }
    }
  }

  // ------------------ 侧栏抽屉（小屏） ------------------
  function isMobileViewport() {
    return window.innerWidth <= MOBILE_BREAKPOINT;
  }

  function openSidebar() {
    state.sidebarOpen = true;
    const sb = $('sidebar');
    const bd = $('sidebarBackdrop');
    if (sb) sb.classList.add('open');
    if (bd) bd.classList.add('open');
  }

  function closeSidebar() {
    state.sidebarOpen = false;
    const sb = $('sidebar');
    const bd = $('sidebarBackdrop');
    if (sb) sb.classList.remove('open');
    if (bd) bd.classList.remove('open');
  }

  function toggleSidebar() {
    if (state.sidebarOpen) closeSidebar();
    else openSidebar();
  }

  function closeSidebarIfMobile() {
    if (isMobileViewport()) closeSidebar();
  }

  // ------------------ API ------------------
  async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        if (j && j.detail !== undefined) {
          if (typeof j.detail === 'string') detail = j.detail;
          else if (Array.isArray(j.detail)) {
            detail = j.detail.map(d => d.msg || JSON.stringify(d)).join('；');
          } else detail = JSON.stringify(j.detail);
        }
      } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) return res.json();
    return res.text();
  }

  // ------------------ 数据加载 ------------------
  async function loadTags() {
    try {
      state.allTags = await api('GET', '/api/tags');
      renderTagCloud();
    } catch (e) {
      toast('加载标签失败：' + e.message, 'error');
    }
  }

  async function loadPlaylists() {
    try {
      state.playlists = await api('GET', '/api/playlists');
      renderPlaylists();
    } catch (e) {
      toast('加载歌单失败：' + e.message, 'error');
    }
  }

  function buildMediaQueryParams() {
    const params = new URLSearchParams();
    if (state.mediaType) params.set('media_type', state.mediaType);
    if (state.favoriteOnly) params.set('favorite', 'true');
    if (state.q) params.set('q', state.q);
    if (state.selectedTags.length) params.set('tags', state.selectedTags.join(','));
    if (state.minBpm != null) params.set('min_bpm', state.minBpm);
    if (state.maxBpm != null) params.set('max_bpm', state.maxBpm);
    if (state.minEnergy != null) params.set('min_energy', state.minEnergy);
    if (state.maxEnergy != null) params.set('max_energy', state.maxEnergy);
    if (state.minRating != null) params.set('min_rating', state.minRating);
    if (state.untagged) params.set('untagged', 'true');
    if (state.unrated) params.set('unrated', 'true');
    params.set('sort', state.sort);
    params.set('order', state.order);
    if (state.sort === 'random') params.set('seed', state.randomSeed || 1);
    params.set('page_size', state.pageSize);
    return params;
  }

  async function loadMedia() {
    const params = buildMediaQueryParams();
    params.set('page', state.page);

    try {
      const data = await api('GET', '/api/media?' + params.toString());
      state.allMedia = data.items;
      state.totalMedia = Number(data.total) || 0;
      renderContent();
      syncPlayingState();
      updateMediaCount();
      updateLoadMoreUI();
    } catch (e) {
      toast('加载素材失败：' + e.message, 'error');
    }
  }

  async function loadMoreMedia() {
    if (state.loadingMore) return;
    if (state.allMedia.length >= state.totalMedia) return;
    state.loadingMore = true;
    updateLoadMoreUI();
    const nextPage = state.page + 1;
    const params = buildMediaQueryParams();
    params.set('page', nextPage);

    try {
      const data = await api('GET', '/api/media?' + params.toString());
      state.page = nextPage;
      state.totalMedia = Number(data.total) || 0;
      const existIds = new Set(state.allMedia.map(x => x.id));
      for (const it of data.items) {
        if (!existIds.has(it.id)) {
          state.allMedia.push(it);
          existIds.add(it.id);
        }
      }
      renderContent();
      syncPlayingState();
      updateMediaCount();
      updateLoadMoreUI();
    } catch (e) {
      toast('加载更多失败：' + e.message, 'error');
    } finally {
      state.loadingMore = false;
      updateLoadMoreUI();
    }
  }

  async function loadAllRemaining() {
    if (state.loadingMore) return;
    if (state.allMedia.length >= state.totalMedia) return;
    state.loadingMore = true;
    updateLoadMoreUI();
    const totalPages = Math.max(1, Math.ceil(state.totalMedia / state.pageSize));
    const params = buildMediaQueryParams();
    params.set('page', 1);
    params.set('page_size', Math.min(1000, state.totalMedia));

    try {
      const data = await api('GET', '/api/media?' + params.toString());
      state.page = 1;
      state.totalMedia = Number(data.total) || 0;
      state.allMedia = data.items;
      renderContent();
      syncPlayingState();
      updateMediaCount();
      updateLoadMoreUI();
      if (state.allMedia.length < state.totalMedia) {
        state.page = totalPages > 1 ? Math.ceil(state.allMedia.length / state.pageSize) : 1;
        toast('已加载前 ' + state.allMedia.length + ' 条，如需更多请继续点"加载更多"', 'info', 3000);
      }
    } catch (e) {
      toast('加载全部失败：' + e.message, 'error');
    } finally {
      state.loadingMore = false;
      updateLoadMoreUI();
    }
  }

  function resetAndLoadMedia() {
    state.page = 1;
    state.allMedia = [];
    state.totalMedia = 0;
    state.selectedIds.clear();
    updateBatchBar();
    return loadMedia();
  }

  function updateMediaCount() {
    const el = $('mediaCount');
    if (!el) return;
    if (state.view === 'playlist') {
      el.textContent = '';
      return;
    }
    if (!state.totalMedia && !state.allMedia.length) {
      el.textContent = '';
      return;
    }
    el.textContent = `已显示 ${state.allMedia.length} / 共 ${state.totalMedia} 条`;
  }

  function updateLoadMoreUI() {
    const wrap = $('loadMoreWrap');
    const moreBtn = $('loadMoreBtn');
    const allBtn = $('loadAllBtn');
    if (!wrap || !moreBtn || !allBtn) return;

    if (state.view !== 'grid' || state.allMedia.length >= state.totalMedia) {
      wrap.style.display = 'none';
      return;
    }
    wrap.style.display = 'flex';

    const remain = state.totalMedia - state.allMedia.length;

    if (state.loadingMore) {
      moreBtn.disabled = true;
      allBtn.disabled = true;
      moreBtn.textContent = '加载中…';
      allBtn.style.display = 'none';
    } else {
      moreBtn.disabled = false;
      allBtn.disabled = false;
      moreBtn.textContent = `加载更多（还有 ${remain} 条）`;
      allBtn.style.display = remain > state.pageSize ? '' : 'none';
    }
  }

  // ------------------ 侧边栏渲染 ------------------
  function renderTagCloud() {
    const el = $('tagCloud');
    if (!state.allTags.length) {
      el.innerHTML = '<div style="font-size:11.5px;color:var(--text-3);padding:4px 8px;">暂无标签</div>';
      return;
    }
    el.innerHTML = state.allTags.map(t => {
      const active = state.selectedTags.includes(t.name) ? 'active' : '';
      const [c1] = paletteFor(t.name.toLowerCase());
      return `<span class="tag-chip ${active}" data-tag="${escapeHtml(t.name)}" tabindex="0" role="button">
        <span class="dot" style="--c:${c1}"></span>
        ${escapeHtml(t.name)}<span class="count">${t.count}</span>
      </span>`;
    }).join('');
  }

  function renderPlaylists() {
    const el = $('playlistList');
    if (!state.playlists.length) {
      el.innerHTML = '<div style="font-size:11.5px;color:var(--text-3);padding:4px 8px;">暂无歌单</div>';
      return;
    }
    el.innerHTML = state.playlists.map(p => {
      const active = (state.view === 'playlist' && state.activePlaylistId === p.id) ? 'active' : '';
      return `<div class="playlist-item ${active}" data-pid="${p.id}" tabindex="0" role="button">
        <span class="pl-ico">♪</span>
        <span class="pl-name">${escapeHtml(p.name)}</span>
        <span class="pl-count">${p.item_count}</span>
        <button class="pl-edit" data-edit-pid="${p.id}" tabindex="-1" title="编辑歌单">✎</button>
        <button class="pl-delete" data-delete-pid="${p.id}" tabindex="-1" title="删除歌单">×</button>
      </div>`;
    }).join('');
  }

  function updateNavActive() {
    document.querySelectorAll('.nav-item').forEach(a => {
      const isFav = a.dataset.favorite === '1';
      if (state.view !== 'grid') {
        a.classList.remove('active');
        return;
      }
      if (state.favoriteOnly) {
        a.classList.toggle('active', isFav);
      } else {
        if (isFav) {
          a.classList.remove('active');
        } else {
          a.classList.toggle('active', (a.dataset.type || '') === state.mediaType);
        }
      }
    });
  }

  function renderFilterChips() {
    const chips = [];
    if (state.q) chips.push({ key: 'q', label: `搜索：${state.q}` });
    state.selectedTags.forEach(tagName => {
      chips.push({ key: 'tag:' + tagName, label: '#' + tagName, kind: 'tag' });
    });
    if (state.minBpm != null) chips.push({ key: 'minBpm', label: `BPM ≥ ${state.minBpm}` });
    if (state.maxBpm != null) chips.push({ key: 'maxBpm', label: `BPM ≤ ${state.maxBpm}` });
    if (state.minEnergy != null) chips.push({ key: 'minEnergy', label: `能量 ≥ ${state.minEnergy}` });
    if (state.maxEnergy != null) chips.push({ key: 'maxEnergy', label: `能量 ≤ ${state.maxEnergy}` });
    if (state.minRating != null) chips.push({ key: 'minRating', label: `评分 ≥ ${state.minRating}` });

    const el = $('filterChips');
    el.innerHTML = chips.map(c =>
      `<span class="filter-chip" data-key="${escapeHtml(c.key)}">${escapeHtml(c.label)}<button class="x" data-rm="${escapeHtml(c.key)}">×</button></span>`
    ).join('');

    const badge = $('filterBadge');
    if (chips.length) {
      badge.style.display = '';
      badge.textContent = String(chips.length);
    } else {
      badge.style.display = 'none';
    }
  }

  function hasActiveFilters() {
    return !!(
      state.q ||
      state.selectedTags.length ||
      state.minBpm != null ||
      state.maxBpm != null ||
      state.minEnergy != null ||
      state.maxEnergy != null ||
      state.minRating != null ||
      state.mediaType
    );
  }

  function updateEmptyState() {
    const titleEl = $('emptyTitle');
    const subEl = $('emptySub');
    const resetBtn = $('emptyResetBtn');
    if (!titleEl) return;

    if (state.favoriteOnly && !hasActiveFilters()) {
      titleEl.textContent = '还没有收藏的素材';
      subEl.textContent = '在卡片上点爱心即可收藏';
      if (resetBtn) resetBtn.style.display = 'none';
      return;
    }

    if (hasActiveFilters()) {
      titleEl.textContent = '没有找到匹配的素材';
      subEl.textContent = '试试放宽筛选条件，或清除关键词与筛选后重试';
      if (resetBtn) resetBtn.style.display = '';
    } else {
      titleEl.textContent = '还没有素材';
      subEl.textContent = '点击右上角「扫描素材」导入 BGM、音效、图片或视频';
      if (resetBtn) resetBtn.style.display = 'none';
    }
  }

  function renderContent() {
    updateNavActive();
    renderFilterChips();

    const grid = $('mediaGrid');
    const playlistView = $('playlistView');
    const empty = $('emptyState');
    const header = $('viewHeader');

    if (state.view === 'playlist') {
      grid.style.display = 'none';
      empty.style.display = 'none';
      playlistView.style.display = 'flex';
      header.classList.add('visible');
      renderPlaylistView();
      return;
    }

    header.classList.remove('visible');
    playlistView.style.display = 'none';
    grid.style.display = 'grid';

    if (!state.allMedia.length) {
      grid.innerHTML = '';
      updateEmptyState();
      empty.style.display = 'flex';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = state.allMedia.map((m, i) => renderCard(m, i)).join('');
  }

  function renderCard(m, index) {
    const title = prettyTitle(m);
    const rawName = m.file_name || '';

    let thumbInner = '';
    if (m.media_type === 'image' || m.media_type === 'video') {
      thumbInner = `<img class="thumb-layer" src="/api/media/${m.id}/thumbnail" alt="" loading="lazy" onerror="this.style.display='none'">`;
    } else {
      const grad = audioCoverBg(m.id);
      const wave = audioWaveformSvg(m.id);
      thumbInner = `
        <div class="thumb-gradient" style="background:${grad}"></div>
        ${wave}
        <img class="thumb-audio-img" src="/api/media/${m.id}/thumbnail" alt="" loading="lazy" onerror="this.style.display='none'">
      `;
    }

    const typeLabel = { bgm: 'BGM', sfx: 'SFX', image: 'IMG', video: 'VID' }[m.media_type] || '';

    const favClass = m.is_favorite ? 'active' : '';
    const favChar = m.is_favorite ? '♥' : '♡';

    const playable = m.media_type === 'bgm' || m.media_type === 'sfx' || m.media_type === 'video';

    const isCurrent = state.currentMedia && state.currentMedia.id === m.id;
    const audio = state.audio;
    const isActivelyPlaying = isCurrent && audio && !audio.paused;
    const hoverBtnHtml = isActivelyPlaying
      ? `<div class="thumb-play" data-toggle="1">❚❚</div>`
      : (playable ? `<div class="thumb-play">▶</div>` : '');
    const playingBadge = playable
      ? `<div class="thumb-playing" aria-hidden="true"><span></span><span></span><span></span></div>`
      : '';

    const metaPieces = [];
    if (m.media_type === 'bgm') {
      if (m.bpm != null) metaPieces.push(`<span class="meta-tag bpm">${Math.round(m.bpm)} BPM</span>`);
      if (m.musical_key) metaPieces.push(`<span class="meta-tag key">${escapeHtml(m.musical_key)}</span>`);
      if (m.duration != null) metaPieces.push(`<span>${fmtDuration(m.duration)}</span>`);
    } else if (m.media_type === 'sfx') {
      if (m.sfx_category) metaPieces.push(`<span class="meta-tag">${escapeHtml(m.sfx_category)}</span>`);
      if (m.duration != null) metaPieces.push(`<span>${fmtDuration(m.duration)}</span>`);
    } else if (m.media_type === 'image') {
      if (m.width && m.height) metaPieces.push(`<span>${m.width}×${m.height}</span>`);
    } else if (m.media_type === 'video') {
      if (m.duration != null) metaPieces.push(`<span>${fmtDuration(m.duration)}</span>`);
      if (m.width && m.height) metaPieces.push(`<span>${m.width}×${m.height}</span>`);
    }
    const metaHtml = metaPieces.join('<span class="meta-sep">·</span>');

    const tagsHtml = (m.tags || []).slice(0, 3).map(t =>
      `<span class="card-tag">${escapeHtml(t)}</span>`
    ).join('');

    const starsHtml = [1, 2, 3, 4, 5].map(n =>
      `<span class="${n <= m.rating ? '' : 'empty'}">★</span>`
    ).join('');

    const cardClass = (isActivelyPlaying ? 'media-card is-playing' : 'media-card')
      + (state.selectedIds.has(m.id) ? ' selected' : '');

    return `
      <div class="${cardClass}" data-index="${index}" data-id="${m.id}" tabindex="0" role="button" title="${escapeHtml(rawName)}">
        <div class="card-thumb" data-thumb="1">
          ${thumbInner}
          <span class="card-check">✓</span>
          <div class="thumb-ripple"></div>
          <div class="thumb-badge">${typeLabel}</div>
          <button class="thumb-fav ${favClass}" data-fav-id="${m.id}" tabindex="-1" title="收藏">${favChar}</button>
          ${playable ? `<button class="thumb-queue" data-queue-id="${m.id}" tabindex="-1" title="下一首播放">+⏭</button>` : ''}
          ${playingBadge}
          ${hoverBtnHtml}
        </div>
        <div class="card-body">
          <div class="card-title">${escapeHtml(title)}</div>
          <div class="card-meta">${metaHtml}</div>
          <div class="card-tags">${tagsHtml}</div>
          <div class="card-bottom">
            <span class="card-stars">${starsHtml}</span>
          </div>
        </div>
      </div>
    `;
  }

  function renderPlaylistView() {
    const container = $('playlistView');
    const playlist = state.playlists.find(p => p.id === state.activePlaylistId);
    if (!playlist) {
      container.innerHTML = '<div class="empty-state"><div class="empty-ico">♪</div><div class="empty-title">歌单不存在</div></div>';
      return;
    }
    $('viewTitle').textContent = playlist.name;
    const plItems = playlist.items || [];
    const totDur = plItems.reduce((s, it) => s + (it.duration || 0), 0);
    const totSize = plItems.reduce((s, it) => s + (it.file_size || 0), 0);
    $('viewSub').textContent = (playlist.description || '') +
      (playlist.item_count ? ` · ${playlist.item_count} 个条目` : ' · 空') +
      (totDur > 0 ? ` · 总时长 ${fmtTotalDuration(totDur)}` : '') +
      (totSize > 0 ? ` · ${fmtSize(totSize)}` : '');

    const playableCount = (playlist.items || []).filter(it =>
      it.media_type === 'bgm' || it.media_type === 'sfx' || it.media_type === 'video'
    ).length;
    const actions = [];
    if (playableCount > 0) {
      actions.push(`<button class="btn btn-primary" id="playPlaylistBtn">▶ 播放全部（${playableCount}）</button>`);
    }
    actions.push(`<button class="btn" id="playlistExportBtn">⇩ 导出</button>`);
    actions.push(`<a class="btn" href="/api/playlists/${playlist.id}/m3u" download>导出 M3U</a>`);
    $('viewActions').innerHTML = actions.join('');

    if (!playlist.items || !playlist.items.length) {
      container.innerHTML = '<div class="empty-state"><div class="empty-ico">♪</div><div class="empty-title">该歌单暂无素材</div><div class="empty-sub">从素材卡片详情里点击「加入歌单」即可添加</div></div>';
      return;
    }

    container.innerHTML = playlist.items.map((it, idx) => {
      const title = prettyTitle(it);
      const dur = it.duration != null ? fmtDuration(it.duration) : '';
      const metaPieces = [];
      if (it.media_type === 'bgm' && it.bpm != null) metaPieces.push(`${Math.round(it.bpm)} BPM`);
      if (it.musical_key) metaPieces.push(it.musical_key);
      if (it.sfx_category) metaPieces.push(it.sfx_category);
      const metaStr = metaPieces.join(' · ');

      let thumbContent = '';
      if (it.media_type === 'image' || it.media_type === 'video') {
        thumbContent = `<img src="/api/media/${it.media_id}/thumbnail" alt="" onerror="this.style.display='none'">`;
      } else {
        const grad = audioCoverBg(it.media_id);
        const wave = rowWaveformSvg(it.media_id);
        thumbContent = `
          <div class="row-grad" style="background:${grad}"></div>
          ${wave}
        `;
      }

      return `
        <div class="playlist-row" data-media-id="${it.media_id}" data-index="${idx}">
          <div class="row-index">${idx + 1}</div>
          <div class="row-thumb" data-play="${it.media_id}">${thumbContent}</div>
          <div class="row-main">
            <div class="row-title" data-play="${it.media_id}" title="${escapeHtml(it.file_name || '')}">${escapeHtml(title)}</div>
            <div class="row-meta">${escapeHtml(metaStr)}</div>
          </div>
          <div class="row-duration">${dur}</div>
          <div class="row-actions">
            <button data-move="up" ${idx === 0 ? 'disabled' : ''} title="上移">↑</button>
            <button data-move="down" ${idx === playlist.items.length - 1 ? 'disabled' : ''} title="下移">↓</button>
            <button class="danger" data-remove="1" title="移除">删除</button>
          </div>
        </div>
      `;
    }).join('');
  }

  function syncPlayingState() {
    const audio = state.audio;
    const cur = state.currentMedia;
    const active = !!(audio && cur && audio.src && !audio.paused && !audio.ended);

    document.querySelectorAll('.media-card').forEach(cardEl => {
      const id = Number(cardEl.dataset.id);
      const isThis = active && cur && cur.id === id;
      cardEl.classList.toggle('is-playing', isThis);
      const btn = cardEl.querySelector('.thumb-play');
      if (btn) {
        const playable = btn.dataset.toggle === '1' || btn.textContent.trim() === '▶' || btn.textContent.trim() === '❚❚';
        if (isThis && playable) {
          btn.dataset.toggle = '1';
          btn.textContent = '❚❚';
        } else if (playable) {
          delete btn.dataset.toggle;
          btn.textContent = '▶';
        }
      }
    });
  }

  function isPlayable(m) {
    return m && (m.media_type === 'bgm' || m.media_type === 'sfx' || m.media_type === 'video');
  }

  function playByIndex(index) {
    const m = state.allMedia[index];
    if (!m) return;
    if (!isPlayable(m)) {
      toast('该素材不支持播放', 'info');
      return;
    }
    playMedia(m, index);
  }

  function applyDensity() {
    const compact = state.density === 'compact';
    $('content').classList.toggle('density-compact', compact);
    const btn = $('densityBtn');
    if (btn) btn.textContent = compact ? '标准视图' : '紧凑视图';
    try { localStorage.setItem('mh.density', compact ? 'compact' : 'standard'); } catch (e) { /* ignore */ }
  }

  function randomPickPlay() {
    const pool = state.allMedia.filter(isPlayable);
    if (!pool.length) {
      toast('当前列表没有可播放的素材', 'info');
      return;
    }
    let pick = pool[Math.floor(Math.random() * pool.length)];
    if (pool.length > 1 && state.currentMedia && pick.id === state.currentMedia.id) {
      pick = pool[(pool.indexOf(pick) + 1) % pool.length];
    }
    const idx = state.allMedia.findIndex(x => x.id === pick.id);
    playMedia(pick, idx);
    const card = document.querySelector(`.media-card[data-id="${pick.id}"]`);
    if (card) {
      card.scrollIntoView({ block: 'center', behavior: 'smooth' });
      card.classList.add('flash');
      setTimeout(() => card.classList.remove('flash'), 1600);
    }
    toast('随机抽到：' + prettyTitle(pick), 'success', 2000);
  }

  function playMedia(m, index) {
    // 队列播出的不清除恢复点；手动点播则清除（回到正常的列表流）
    if (state._fromQueue) state._fromQueue = false;
    else state.queueResume = null;
    state.currentIndex = index != null ? index : -1;
    state.currentMedia = m;
    recordPlayHistory(m);
    const audio = state.audio;
    audio.src = `/api/media/${m.id}/stream`;
    // 双保险：新 src 后重设一次倍速（某些浏览器边缘行为可能因 src 变更重置）
    try { audio.playbackRate = state.playbackRate; } catch (_) { /* ignore */ }
    audio.play().then(() => {
      $('playBtn').textContent = '⏸';
      syncPlayingState();
    }).catch(err => {
      toast('播放失败：' + err.message, 'error');
      syncPlayingState();
    });

    $('player').style.display = 'flex';
    $('playerTitle').textContent = prettyTitle(m);

    const subParts = [];
    if (m.media_type === 'bgm') {
      if (m.bpm != null) subParts.push(`${Math.round(m.bpm)} BPM`);
      if (m.musical_key) subParts.push(m.musical_key);
    } else if (m.media_type === 'sfx') {
      if (m.sfx_category) subParts.push(m.sfx_category);
    }
    if (m.duration != null) subParts.push(fmtDuration(m.duration));
    $('playerSub').textContent = subParts.join(' · ');

    if (m.media_type === 'image' || m.media_type === 'video') {
      $('playerThumb').innerHTML = `<img src="/api/media/${m.id}/thumbnail" alt="" onerror="this.style.display='none'">`;
    } else {
      const grad = audioCoverBg(m.id);
      $('playerThumb').innerHTML =
        `<div class="pt-grad" style="background:${grad}"></div>
         <img src="/api/media/${m.id}/thumbnail" alt=""
              style="position:absolute;right:2px;bottom:2px;width:26%;height:auto;z-index:1;mix-blend-mode:soft-light;opacity:.35"
              onerror="this.style.display='none'">`;
    }
  }

  function playMediaByMediaId(mediaId) {
    const p = state.playlists.find(x => x.id === state.activePlaylistId);
    let m = p && p.items ? p.items.find(it => it.media_id === mediaId) : null;
    if (!m) {
      m = state.allMedia.find(x => x.id === mediaId);
    }
    if (!m) return false;
    playMedia(m, -1);
    return true;
  }

  function playPlaylistAll() {
    const p = state.playlists.find(x => x.id === state.activePlaylistId);
    if (!p || !p.items) {
      toast('歌单为空', 'info');
      return;
    }
    const ids = p.items
      .filter(it => it.media_type === 'bgm' || it.media_type === 'sfx' || it.media_type === 'video')
      .map(it => it.media_id);
    if (!ids.length) {
      toast('该歌单没有可播放的素材', 'info');
      return;
    }
    state.playlistQueue = { playlistId: p.id, mediaIds: ids, index: 0 };
    playMediaByMediaId(ids[0]);
  }

  function findPlayable(fromIndex, direction) {
    const n = state.allMedia.length;
    if (!n) return -1;
    for (let step = 1; step <= n; step++) {
      const i = ((fromIndex + direction * step) % n + n) % n;
      if (isPlayable(state.allMedia[i])) return i;
    }
    return -1;
  }

  function togglePlay() {
    const audio = state.audio;
    if (!audio.src) return;
    if (audio.paused) {
      audio.play().then(() => $('playBtn').textContent = '⏸').catch(() => {});
    } else {
      audio.pause();
      $('playBtn').textContent = '▶';
    }
    syncPlayingState();
  }

  function playPrev() {
    if (state.playlistQueue) {
      const q = state.playlistQueue;
      const prev = q.index - 1;
      if (prev >= 0) {
        q.index = prev;
        playMediaByMediaId(q.mediaIds[prev]);
        return;
      } else {
        q.index = 0;
        playMediaByMediaId(q.mediaIds[0]);
        return;
      }
    }
    if (state.currentIndex < 0) return;
    const i = findPlayable(state.currentIndex, -1);
    if (i >= 0) playByIndex(i);
  }

  function bindPlayer() {
    state.audio = $('audio');
    const audio = state.audio;

    audio.addEventListener('timeupdate', () => {
      const dur = audio.duration || 0;
      const cur = audio.currentTime || 0;
      $('currentTime').textContent = fmtDuration(cur);
      $('totalTime').textContent = fmtDuration(dur);
      if (dur > 0) {
        $('progressBar').value = String(Math.round(cur / dur * 1000));
      }
    });

    audio.addEventListener('ended', () => {
      $('playBtn').textContent = '▶';
      syncPlayingState();
      playNextAuto();
    });

    audio.addEventListener('pause', () => {
      $('playBtn').textContent = '▶';
      syncPlayingState();
    });
    audio.addEventListener('play', () => {
      $('playBtn').textContent = '⏸';
      syncPlayingState();
    });

    $('playBtn').addEventListener('click', togglePlay);
    $('nextBtn').addEventListener('click', playNextManual);
    $('prevBtn').addEventListener('click', playPrev);

    $('progressBar').addEventListener('input', (e) => {
      const dur = audio.duration || 0;
      if (dur > 0) {
        audio.currentTime = (Number(e.target.value) / 1000) * dur;
      }
    });

    function applyVolume(v) {
      const vol = Math.max(0, Math.min(1, Number(v) || 0));
      audio.volume = vol;
      $('volumeBar').value = Math.round(vol * 100);
      const ico = document.querySelector('.vol-ico');
      if (ico) ico.textContent = vol === 0 ? '🔇' : '🔊';
      try { localStorage.setItem('mh.volume', String(vol)); } catch (e) { /* ignore */ }
    }
    $('volumeBar').addEventListener('input', (e) => applyVolume(Number(e.target.value) / 100));
    const volIco = document.querySelector('.vol-ico');
    if (volIco) {
      volIco.style.cursor = 'pointer';
      volIco.title = '静音 / 恢复';
      volIco.addEventListener('click', () => {
        if (audio.volume > 0) { state._prevVolume = audio.volume; applyVolume(0); }
        else applyVolume(state._prevVolume || 0.8);
      });
    }
    let savedVol = 0.8;
    try {
      const raw = localStorage.getItem('mh.volume');
      if (raw != null && !isNaN(parseFloat(raw))) savedVol = parseFloat(raw);
    } catch (e) { /* ignore */ }
    applyVolume(savedVol);
    $('sleepBtn').addEventListener('click', cycleSleep);
    updateSleepBtn();
    // 播放队列弹窗
    $('queueBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      toggleQueuePopup();
    });
    $('queueClearBtn').addEventListener('click', () => {
      clearPlayQueue();
      toast('播放队列已清空', 'info', 1200);
    });
    $('queueList').addEventListener('click', (e) => {
      const del = e.target.closest('[data-queue-del]');
      if (del) {
        e.stopPropagation();
        dequeueById(Number(del.dataset.queueDel));
      }
    });
    document.addEventListener('click', (e) => {
      const pop = $('queuePopup');
      if (pop && pop.style.display === 'flex'
          && !e.target.closest('#queuePopup') && !e.target.closest('#queueBtn')) {
        pop.style.display = 'none';
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const pop = $('queuePopup');
        if (pop && pop.style.display === 'flex') pop.style.display = 'none';
      }
    });
    updateQueueBadge();
  }

  function openViewer(media) {
    closeAddToPlaylist();

    const modal = $('viewerModal');
    const body = $('viewerBody');
    const caption = $('viewerCaption');

    if (media.media_type === 'video') {
      body.innerHTML = `<video src="/api/media/${media.id}/stream" controls autoplay></video>`;
    } else if (media.media_type === 'image') {
      body.innerHTML = `<img src="/api/media/${media.id}/stream" alt="">`;
    } else {
      body.innerHTML = '';
      return;
    }
    caption.textContent = `${prettyTitle(media)} · ${media.file_name || ''}`;
    modal.style.display = 'flex';
    openModalFocus(modal);
  }

  function closeViewer() {
    const modal = $('viewerModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
    $('viewerBody').innerHTML = '';
  }

  async function openDetail(mediaId) {
    try {
      const m = await api('GET', `/api/media/${mediaId}`);
      showDetailModal(m);
    } catch (e) {
      toast('加载详情失败：' + e.message, 'error');
    }
  }

  async function loadSimilar(mediaId) {
    const box = $('detailSimilar');
    if (!box) return;
    try {
      const d = await api('GET', `/api/media/${mediaId}/similar`);
      if (state.detailMediaId !== mediaId) return; // 详情已切换，丢弃过期结果
      if (!d.items.length) {
        box.innerHTML = '<div class="cleanup-empty">暂时没有同类的其他素材。</div>';
        return;
      }
      box.innerHTML = d.items.map(it => {
        const meta = [];
        if (it.bpm != null) meta.push(Math.round(it.bpm) + ' BPM');
        if (it.musical_key) meta.push(it.musical_key);
        if (it.sfx_category) meta.push(it.sfx_category);
        if (it.duration != null) meta.push(fmtDuration(it.duration));
        return `<div class="similar-item" data-similar-id="${it.id}">
          <span class="sim-name">${escapeHtml(prettyTitle(it))}</span>
          <span class="sim-meta">${meta.join(' · ')}</span>
        </div>`;
      }).join('');
    } catch (e) {
      box.innerHTML = '<div class="cleanup-empty">相似素材加载失败。</div>';
    }
  }

  function showDetailModal(m) {
    closeAddToPlaylist();
    state.detailMediaId = m.id;

    const modal = $('detailModal');
    modal.style.display = 'flex';
    const title = prettyTitle(m);

    if (m.media_type === 'image' || m.media_type === 'video') {
      $('detailThumb').innerHTML =
        `<img src="/api/media/${m.id}/thumbnail" alt="" onerror="this.style.display='none'">`;
    } else {
      const grad = audioCoverBg(m.id);
      const wave = detailWaveformSvg(m.id);
      $('detailThumb').innerHTML = `
        <div class="dc-grad" style="background:${grad}"></div>
        <div class="dc-wave-band">${wave}</div>
        <div class="dc-scrim"></div>
        ${detailNoteSvg()}
      `;
    }

    const metaRows = buildMetaRows(m);

    const starsHtml = [1, 2, 3, 4, 5].map(n =>
      `<span data-star="${n}" class="${n <= m.rating ? 'active' : ''}">★</span>`
    ).join('');

    const tagsHtml = (m.tags || []).map(t =>
      `<span class="detail-tag">${escapeHtml(t)}<span class="detail-tag-remove" data-remove-tag="${escapeHtml(t)}">×</span></span>`
    ).join('') || '<span style="color:var(--text-3);font-size:12px;">暂无标签</span>';

    const favClass = m.is_favorite ? 'active' : '';
    const favChar = m.is_favorite ? '♥' : '♡';

    const viewBtnLabel = m.media_type === 'image'
      ? '在原图中查看'
      : (m.media_type === 'video' ? '在查看器中观看' : '');

    $('detailInfo').innerHTML = `
      <div class="detail-title-row">
        <div class="detail-title" id="detailTitleEl">${escapeHtml(title)}</div>
        <button class="title-edit-btn" id="detailTitleEditBtn" title="编辑标题">✎</button>
      </div>
      <div class="detail-filename">${escapeHtml(m.file_name || '')}</div>

      <div class="detail-meta-grid">${metaRows}</div>

      <div class="detail-section">
        <div class="detail-section-label">评分</div>
        <div class="detail-stars" id="detailStars">${starsHtml}</div>
      </div>

      <div class="detail-section">
        <div class="detail-section-label">收藏</div>
        <button class="detail-fav-btn ${favClass}" id="detailFavBtn">${favChar} ${m.is_favorite ? '已收藏' : '加入收藏'}</button>
      </div>

      <div class="detail-section">
        <div class="detail-section-label">标签</div>
        <div class="detail-tags" id="detailTags">${tagsHtml}</div>
        <div class="tag-input-wrap">
          <input type="text" id="detailTagInput" placeholder="输入标签名，回车添加" />
          <button id="detailTagAdd">添加</button>
        </div>
      </div>

      <div class="detail-actions">
        ${isPlayable(m) ? '<button class="btn btn-primary" id="detailPlayBtn">▶ 播放</button>' : ''}
        ${isPlayable(m) ? '<button class="btn" id="detailQueueBtn">下一首播放</button>' : ''}
        ${viewBtnLabel ? `<button class="btn" id="detailViewBtn">${viewBtnLabel}</button>` : ''}
        <button class="btn" id="detailAddPlaylistBtn">加入歌单</button>
        <button class="btn" id="detailCopyPathBtn">复制路径</button>
        <button class="btn" id="detailRenameBtn">重命名文件</button>
      </div>

      <div class="detail-section">
        <div class="detail-section-label">相似素材</div>
        <div class="similar-list" id="detailSimilar"><div class="cleanup-empty">正在找…</div></div>
      </div>
    `;
    loadSimilar(m.id);

    // 标题编辑（查看/编辑两态）：自定义 title 原样展示，留空保存恢复文件名派生名
    const renderDetailTitleRow = () => {
      const row = document.querySelector('.detail-title-row');
      if (!row) return;
      row.innerHTML = `
        <div class="detail-title" id="detailTitleEl">${escapeHtml(prettyTitle(m))}</div>
        <button class="title-edit-btn" id="detailTitleEditBtn" title="编辑标题">✎</button>
      `;
      $('detailTitleEditBtn').addEventListener('click', () => {
        const row2 = document.querySelector('.detail-title-row');
        if (!row2) return;
        row2.innerHTML = `
          <div class="title-edit-form">
            <input type="text" id="detailTitleInput" value="${escapeHtml(prettyTitle(m))}" placeholder="留空则恢复文件名" />
            <button class="primary" id="detailTitleSave">保存</button>
            <button class="ghost" id="detailTitleCancel">取消</button>
          </div>
        `;
        const input = $('detailTitleInput');
        input.focus();
        input.select();
        let submitting = false;
        const doCancel = () => renderDetailTitleRow();
        const doSubmit = async () => {
          if (submitting) return;
          submitting = true;
          const saveBtn = $('detailTitleSave');
          if (saveBtn) saveBtn.disabled = true;
          try {
            const updated = await api('PATCH', `/api/media/${m.id}`, { title: input.value.trim() });
            m.title = updated.title;
            updateMediaInPlace(updated);
            renderDetailTitleRow();
            toast('已保存', 'success', 1500);
          } catch (e) {
            toast('保存失败：' + e.message, 'error');
            submitting = false;
            const sb = $('detailTitleSave');
            if (sb) sb.disabled = false;
          }
        };
        $('detailTitleSave').addEventListener('click', doSubmit);
        $('detailTitleCancel').addEventListener('click', doCancel);
        input.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter') { ev.preventDefault(); doSubmit(); }
          if (ev.key === 'Escape') { ev.stopPropagation(); doCancel(); }
        });
      });
    };
    renderDetailTitleRow();

    const infoEl = $('detailInfo');
    infoEl.scrollTop = 0;
    updateDetailScrollHint();
    setTimeout(updateDetailScrollHint, 30);

    $('detailStars').querySelectorAll('span').forEach(sp => {
      sp.addEventListener('click', async () => {
        const rating = Number(sp.dataset.star);
        try {
          const updated = await api('PATCH', `/api/media/${m.id}`, { rating });
          m.rating = updated.rating;
          refreshStars(updated.rating);
          updateMediaInPlace(updated);
          toast('评分已更新', 'success', 1500);
        } catch (e) {
          toast('评分更新失败：' + e.message, 'error');
        }
      });
      sp.addEventListener('mouseenter', () => {
        const hoverVal = Number(sp.dataset.star);
        $('detailStars').querySelectorAll('span').forEach(s => {
          s.classList.toggle('hover', Number(s.dataset.star) <= hoverVal);
        });
      });
    });
    $('detailStars').addEventListener('mouseleave', () => {
      $('detailStars').querySelectorAll('span').forEach(s => s.classList.remove('hover'));
    });

    function refreshStars(r) {
      $('detailStars').querySelectorAll('span').forEach(s => {
        s.classList.toggle('active', Number(s.dataset.star) <= r);
      });
    }

    $('detailFavBtn').addEventListener('click', async () => {
      const next = !m.is_favorite;
      try {
        const updated = await api('PATCH', `/api/media/${m.id}`, { is_favorite: next });
        m.is_favorite = updated.is_favorite;
        const btn = $('detailFavBtn');
        btn.classList.toggle('active', !!m.is_favorite);
        btn.innerHTML = m.is_favorite ? '♥ 已收藏' : '♡ 加入收藏';
        updateMediaInPlace(updated);
        toast(m.is_favorite ? '已收藏' : '已取消收藏', 'success', 1500);
      } catch (e) {
        toast('收藏更新失败：' + e.message, 'error');
      }
    });

    async function addTag() {
      const input = $('detailTagInput');
      const name = input.value.trim();
      if (!name) return;
      try {
        const res = await api('POST', `/api/media/${m.id}/tags`, { tags: [name] });
        m.tags = res.tags;
        input.value = '';
        renderDetailTags();
        loadTags();
        updateMediaInPlace({ ...m, tags: res.tags });
        toast('标签已添加', 'success', 1500);
        setTimeout(updateDetailScrollHint, 20);
      } catch (e) {
        toast('添加标签失败：' + e.message, 'error');
      }
    }
    $('detailTagAdd').addEventListener('click', addTag);
    $('detailTagInput').addEventListener('keydown', e => {
      if (e.key === 'Enter') addTag();
    });

    function renderDetailTags() {
      const container = $('detailTags');
      if (!m.tags || !m.tags.length) {
        container.innerHTML = '<span style="color:var(--text-3);font-size:12px;">暂无标签</span>';
        return;
      }
      container.innerHTML = m.tags.map(t =>
        `<span class="detail-tag">${escapeHtml(t)}<span class="detail-tag-remove" data-remove-tag="${escapeHtml(t)}">×</span></span>`
      ).join('');
      container.querySelectorAll('[data-remove-tag]').forEach(el => {
        el.addEventListener('click', async () => {
          const tagName = el.dataset.removeTag;
          try {
            const res = await api('DELETE', `/api/media/${m.id}/tags`, { tags: [tagName] });
            m.tags = res.tags;
            renderDetailTags();
            loadTags();
            updateMediaInPlace({ ...m, tags: res.tags });
            toast('标签已删除', 'success', 1500);
            setTimeout(updateDetailScrollHint, 20);
          } catch (e) {
            toast('删除标签失败：' + e.message, 'error');
          }
        });
      });
    }
    renderDetailTags();

    const playBtn = $('detailPlayBtn');
    if (playBtn) {
      playBtn.addEventListener('click', () => {
        playMedia(m, state.allMedia.findIndex(x => x.id === m.id));
        closeDetail();
      });
    }

    const queueBtn2 = $('detailQueueBtn');
    if (queueBtn2) {
      queueBtn2.addEventListener('click', () => {
        queueNext(m);
      });
    }

    const viewBtn = $('detailViewBtn');
    if (viewBtn) {
      viewBtn.addEventListener('click', () => {
        closeDetail();
        openViewer(m);
      });
    }

    $('detailAddPlaylistBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      showAddToPlaylist(e.currentTarget, m.id);
    });

    $('detailCopyPathBtn').addEventListener('click', async (e) => {
      e.stopPropagation();
      const p = m.file_path || '';
      if (!p) { toast('这条素材没有文件路径', 'error'); return; }
      let ok = false;
      try {
        await navigator.clipboard.writeText(p);
        ok = true;
      } catch (_) {
        const ta = document.createElement('textarea');
        ta.value = p;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
        ta.remove();
      }
      if (ok) toast('已复制文件路径', 'success', 1500);
      else toast('复制失败，路径是：' + p, 'error');
    });

    $('detailRenameBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      openRenameModal(m);
    });

    updateDetailNav();
    openModalFocus(modal);
  }

  // 详情内切换上一条/下一条（范围 = 当前网格已加载列表）
  function updateDetailNav() {
    const idx = state.allMedia.findIndex(x => x.id === state.detailMediaId);
    const prev = $('detailPrevBtn');
    const next = $('detailNextBtn');
    if (prev) prev.disabled = idx <= 0;
    if (next) next.disabled = idx < 0 || idx >= state.allMedia.length - 1;
  }

  function stepDetail(dir) {
    const idx = state.allMedia.findIndex(x => x.id === state.detailMediaId);
    if (idx < 0) return;
    const target = state.allMedia[idx + dir];
    if (target) openDetail(target.id);
  }

  function updateDetailScrollHint() {
    const infoEl = $('detailInfo');
    const wrapEl = $('detailInfoScrollWrap');
    if (!infoEl || !wrapEl) return;
    const canScroll = infoEl.scrollHeight > infoEl.clientHeight + 2;
    const atBottom = infoEl.scrollTop + infoEl.clientHeight >= infoEl.scrollHeight - 2;
    wrapEl.classList.toggle('no-overflow', !canScroll);
    wrapEl.classList.toggle('at-bottom', canScroll && atBottom);
  }

  function buildMetaRows(m) {
    const rows = [];
    const push = (label, value) => {
      if (value == null || value === '') return;
      rows.push(`<div class="detail-meta-item"><div class="detail-meta-label">${label}</div><div class="detail-meta-value">${escapeHtml(String(value))}</div></div>`);
    };
    push('类型', { bgm: 'BGM 音乐', sfx: '音效', image: '图片', video: '视频' }[m.media_type]);
    if (m.sfx_category) push('音效分类', m.sfx_category);
    if (m.bpm != null) push('BPM', Math.round(m.bpm));
    if (m.musical_key) push('调性', m.musical_key);
    if (m.energy != null) push('能量', Number(m.energy).toFixed(2));
    if (m.duration != null) push('时长', fmtDuration(m.duration));
    if (m.width && m.height) push('尺寸', `${m.width}×${m.height}`);
    if (m.bitrate) push('码率', Math.round(m.bitrate / 1000) + ' kbps');
    if (m.sample_rate) push('采样率', m.sample_rate + ' Hz');
    push('文件大小', fmtSize(m.file_size));
    return rows.join('');
  }

  function updateMediaInPlace(updated) {
    const idx = state.allMedia.findIndex(x => x.id === updated.id);
    if (idx >= 0) {
      const prev = state.allMedia[idx];
      state.allMedia[idx] = { ...prev, ...updated };
    }
    if (state.currentMedia && state.currentMedia.id === updated.id) {
      state.currentMedia = { ...state.currentMedia, ...updated };
    }
    const cardEl = document.querySelector(`.media-card[data-id="${updated.id}"]`);
    if (cardEl) {
      const starsEl = cardEl.querySelector('.card-stars');
      if (starsEl) {
        starsEl.innerHTML = [1, 2, 3, 4, 5].map(n =>
          `<span class="${n <= updated.rating ? '' : 'empty'}">★</span>`
        ).join('');
      }
      const favEl = cardEl.querySelector('.thumb-fav');
      if (favEl) {
        favEl.classList.toggle('active', !!updated.is_favorite);
        favEl.textContent = updated.is_favorite ? '♥' : '♡';
      }
      const tagsEl = cardEl.querySelector('.card-tags');
      if (tagsEl && updated.tags) {
        tagsEl.innerHTML = updated.tags.slice(0, 3).map(t =>
          `<span class="card-tag">${escapeHtml(t)}</span>`
        ).join('');
      }
      const cardTitleEl = cardEl.querySelector('.card-title');
      if (cardTitleEl) {
        cardTitleEl.textContent = prettyTitle(updated);
      }
    }
    // 正在播放的正是这条时，播放条标题同步
    if (state.currentMedia && state.currentMedia.id === updated.id) {
      const pt = $('playerTitle');
      if (pt) pt.textContent = prettyTitle(updated);
    }

    // 收藏视图下取消收藏 → 从视图移除，避免幽灵卡
    // is_favorite 是 0/1 整数，必须用真值判断
    if (state.favoriteOnly && !updated.is_favorite) {
      const removeIdx = state.allMedia.findIndex(x => x.id === updated.id);
      if (removeIdx >= 0) {
        state.allMedia.splice(removeIdx, 1);
        state.totalMedia = Math.max(0, state.totalMedia - 1);
        if (state.currentMedia && state.currentMedia.id === updated.id) {
          state.currentIndex = -1;
        }
        const el = document.querySelector(`.media-card[data-id="${updated.id}"]`);
        if (el) el.remove();
        updateMediaCount();
        updateLoadMoreUI();
        if (!state.allMedia.length) {
          const grid = $('mediaGrid');
          const empty = $('emptyState');
          if (grid) grid.innerHTML = '';
          updateEmptyState();
          if (empty) empty.style.display = 'flex';
        }
      }
    }
  }

  function closeDetail() {
    const modal = $('detailModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
    closeAddToPlaylist();
  }

  let addToPlaylistMediaId = null;

  function closeAddToPlaylist() {
    const dd = document.getElementById('addToPlaylistDropdown');
    if (dd) dd.style.display = 'none';
    addToPlaylistMediaId = null;
  }

  function showAddToPlaylist(anchorEl, mediaId) {
    addToPlaylistMediaId = mediaId;

    let dd = document.getElementById('addToPlaylistDropdown');
    if (!dd || dd.parentNode !== document.body) {
      if (dd && dd.parentNode) dd.parentNode.removeChild(dd);
      dd = document.createElement('div');
      dd.id = 'addToPlaylistDropdown';
      dd.className = 'dropdown';
      document.body.appendChild(dd);
    }

    if (!state.playlists.length) {
      dd.innerHTML = '<div class="dropdown-empty">暂无歌单，请先在左侧新建</div>';
    } else {
      dd.innerHTML = state.playlists.map(p =>
        `<div class="dropdown-item" data-pid="${p.id}">
          <span>${escapeHtml(p.name)}</span>
          <span class="count">${p.item_count}</span>
        </div>`
      ).join('');
    }

    dd.style.display = 'block';

    const rect = anchorEl.getBoundingClientRect();
    const ddRect = dd.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const GAP = 6;
    const EDGE = 8;

    let left = rect.left;
    if (left + ddRect.width + EDGE > vw) {
      left = vw - ddRect.width - EDGE;
    }
    if (left < EDGE) left = EDGE;

    let top = rect.bottom + GAP;
    if (top + ddRect.height + EDGE > vh) {
      top = rect.top - ddRect.height - GAP;
      if (top < EDGE) {
        top = Math.max(EDGE, vh - ddRect.height - EDGE);
      }
    }

    dd.style.left = left + 'px';
    dd.style.top = top + 'px';

    dd.querySelectorAll('.dropdown-item').forEach(el => {
      el.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const pid = Number(el.dataset.pid);
        const mid = addToPlaylistMediaId;
        if (!mid) return;
        try {
          await api('POST', `/api/playlists/${pid}/items`, { media_id: mid });
          toast('已加入歌单', 'success', 1500);
          closeAddToPlaylist();
          await loadPlaylists();
          if (state.view === 'playlist' && state.activePlaylistId === pid) {
            await openPlaylist(pid);
          }
        } catch (e) {
          toast('加入失败：' + e.message, 'error');
        }
      });
    });
  }

  document.addEventListener('click', (e) => {
    const dd = document.getElementById('addToPlaylistDropdown');
    if (!dd || dd.style.display !== 'block') return;
    if (dd.contains(e.target)) return;
    if (e.target.closest && e.target.closest('#detailAddPlaylistBtn')) return;
    closeAddToPlaylist();
  });

  async function openPlaylist(pid) {
    try {
      const detail = await api('GET', `/api/playlists/${pid}`);
      const idx = state.playlists.findIndex(p => p.id === pid);
      if (idx >= 0) {
        state.playlists[idx] = {
          ...state.playlists[idx],
          item_count: detail.item_count,
          items: detail.items,
        };
      } else {
        state.playlists.push(detail);
      }
      state.view = 'playlist';
      state.activePlaylistId = pid;
      renderPlaylists();
      renderContent();
      syncPlayingState();
      updateMediaCount();
      updateLoadMoreUI();
    } catch (e) {
      toast('打开歌单失败：' + e.message, 'error');
    }
  }

  async function moveItem(pid, mediaId, direction) {
    const p = state.playlists.find(x => x.id === pid);
    if (!p || !p.items) return;
    const items = p.items;
    const idx = items.findIndex(it => it.media_id === mediaId);
    if (idx < 0) return;
    const target = direction === 'up' ? idx - 1 : idx + 1;
    if (target < 0 || target >= items.length) return;
    const arr = items.slice();
    [arr[idx], arr[target]] = [arr[target], arr[idx]];
    const order = arr.map(it => it.media_id);
    try {
      await api('PUT', `/api/playlists/${pid}/items/order`, { order });
      await openPlaylist(pid);
      toast('已保存顺序', 'success', 1200);
    } catch (e) {
      toast('重排失败：' + e.message, 'error');
    }
  }

  async function removeItem(pid, mediaId) {
    try {
      await api('DELETE', `/api/playlists/${pid}/items/${mediaId}`);
      toast('已移除', 'success', 1200);
      await openPlaylist(pid);
    } catch (e) {
      toast('移除失败：' + e.message, 'error');
    }
  }

  function setScanFooter(scanState) {
    const idle = $('scanFooterIdle');
    const done = $('scanFooterDone');
    const err = $('scanFooterError');
    if (!idle || !done || !err) return;
    idle.hidden = true;
    done.hidden = true;
    err.hidden = true;
    if (scanState === 'done') {
      done.hidden = false;
    } else if (scanState === 'error') {
      err.hidden = false;
    } else {
      idle.hidden = false;
    }
  }

  function resetScanModalToIdle() {
    $('scanProgressWrap').style.display = 'none';
    $('scanProgressFill').style.width = '0%';
    $('scanProgressText').textContent = '';
    $('scanStart').disabled = false;
    setScanFooter('idle');
  }

  async function startScan() {
    const path = $('scanPath').value.trim();
    const type = $('scanType').value;
    const jsonPath = $('scanJson').value.trim();

    if (!path) {
      toast('请填写目录路径', 'error');
      return;
    }

    const body = { entries: [{ path, media_type: type }] };
    if (jsonPath) body.feature_index = jsonPath;

    $('scanProgressWrap').style.display = 'block';
    $('scanProgressFill').style.width = '0%';
    $('scanProgressText').textContent = '正在启动…';
    $('scanStart').disabled = true;
    setScanFooter('running');

    try {
      const res = await api('POST', '/api/scan/start', body);
      state.scanTaskId = res.task_id;
      startScanPolling();
    } catch (e) {
      $('scanStart').disabled = false;
      $('scanProgressText').textContent = '启动失败：' + e.message;
      toast('扫描启动失败：' + e.message, 'error');
      setScanFooter('error');
    }
  }

  function startScanPolling() {
    if (state.scanPollTimer) clearInterval(state.scanPollTimer);
    state.scanPollTimer = setInterval(async () => {
      if (!state.scanTaskId) return;
      try {
        const t = await api('GET', `/api/scan/status/${state.scanTaskId}`);
        const pct = t.total > 0 ? Math.round(t.processed / t.total * 100) : 0;
        $('scanProgressFill').style.width = pct + '%';
        const parts = [`${t.processed}/${t.total}（${pct}%）`];
        if (t.inserted) parts.push(`新增 ${t.inserted}`);
        if (t.updated) parts.push(`更新 ${t.updated}`);
        if (t.skipped) parts.push(`跳过 ${t.skipped}`);
        if (t.errors && t.errors.length) parts.push(`错误 ${t.errors.length}`);
        $('scanProgressText').textContent = parts.join(' · ');
        if (t.status === 'done') {
          clearInterval(state.scanPollTimer);
          state.scanPollTimer = null;
          state.scanTaskId = null;
          $('scanProgressText').textContent += ' · 完成';
          toast('扫描完成', 'success');
          setScanFooter('done');
          await Promise.all([loadMedia(), loadTags()]);
        } else if (t.status === 'error') {
          clearInterval(state.scanPollTimer);
          state.scanPollTimer = null;
          state.scanTaskId = null;
          $('scanProgressText').textContent = '失败：' + (t.error_message || '未知错误');
          toast('扫描失败：' + (t.error_message || '未知错误'), 'error');
          setScanFooter('error');
        }
      } catch (e) { /* ignore */ }
    }, 1000);
  }

  async function startBackfill() {
    try {
      const res = await api('POST', '/api/features/backfill');
      state.backfillTaskId = res.task_id;
      toast('特征补全任务已启动', 'info');
      startBackfillPolling();
    } catch (e) {
      toast('补全启动失败：' + e.message, 'error');
    }
  }

  function startBackfillPolling() {
    if (state.backfillPollTimer) return;
    let lastShownPct = -1;
    state.backfillPollTimer = setInterval(async () => {
      if (!state.backfillTaskId) {
        clearInterval(state.backfillPollTimer);
        state.backfillPollTimer = null;
        return;
      }
      try {
        const t = await api('GET', `/api/features/backfill/${state.backfillTaskId}`);
        const pct = t.total > 0 ? Math.round(t.processed / t.total * 100) : 0;
        if (pct !== lastShownPct && pct % 10 === 0) {
          toast(`BGM 特征补全 ${t.processed}/${t.total}`, 'info', 1500);
          lastShownPct = pct;
        }
        if (t.status === 'done') {
          clearInterval(state.backfillPollTimer);
          state.backfillPollTimer = null;
          state.backfillTaskId = null;
          toast(`补全完成：分析 ${t.analyzed}，跳过 ${t.skipped}，失败 ${t.failed}`, 'success', 4000);
          await loadMedia();
        } else if (t.status === 'error') {
          clearInterval(state.backfillPollTimer);
          state.backfillPollTimer = null;
          state.backfillTaskId = null;
          toast('补全失败：' + (t.error_message || '未知错误'), 'error');
        }
      } catch (e) { /* ignore */ }
    }, 2000);
  }

  function updatePlaylistSubmitState() {
    const nameInput = $('playlistName');
    const submitBtn = $('playlistCreate');
    if (!nameInput || !submitBtn) return;
    const isEmpty = !nameInput.value.trim();
    submitBtn.disabled = isEmpty;
  }

  function openPlaylistModal(playlist) {
    const modal = $('playlistModal');
    const titleEl = $('playlistModalTitle');
    const submitBtn = $('playlistCreate');
    const nameInput = $('playlistName');
    const descInput = $('playlistDesc');

    if (playlist) {
      state.playlistEditingId = playlist.id;
      if (titleEl) titleEl.textContent = '编辑歌单';
      if (submitBtn) submitBtn.textContent = '保存';
      if (nameInput) nameInput.value = playlist.name || '';
      if (descInput) descInput.value = playlist.description || '';
    } else {
      state.playlistEditingId = null;
      if (titleEl) titleEl.textContent = '新建歌单';
      if (submitBtn) submitBtn.textContent = '创建';
      if (nameInput) nameInput.value = '';
      if (descInput) descInput.value = '';
    }

    updatePlaylistSubmitState();

    modal.style.display = 'flex';
    openModalFocus(modal);
    if (nameInput) {
      requestAnimationFrame(() => {
        try { nameInput.focus(); nameInput.select(); } catch (_) { /* ignore */ }
      });
    }
  }

  function closePlaylistModal() {
    const modal = $('playlistModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
    state.playlistEditingId = null;
  }

  async function submitPlaylistForm() {
    const name = $('playlistName').value.trim();
    const description = $('playlistDesc').value.trim() || null;
    if (!name) {
      toast('歌单名不能为空', 'error');
      return;
    }

    const editingId = state.playlistEditingId;

    try {
      if (editingId == null) {
        await api('POST', '/api/playlists', { name, description });
        closePlaylistModal();
        await loadPlaylists();
        toast('歌单已创建', 'success');
      } else {
        await api('PATCH', `/api/playlists/${editingId}`, { name, description });
        const wasActive = state.activePlaylistId === editingId;
        closePlaylistModal();
        await loadPlaylists();
        if (wasActive && state.view === 'playlist') {
          await openPlaylist(editingId);
        }
        toast('歌单已保存', 'success');
      }
    } catch (e) {
      toast((editingId == null ? '创建失败：' : '保存失败：') + e.message, 'error');
    }
  }

  function toggleFilterPanel() {
    const panel = $('filterPanel');
    const btn = $('filterToggleBtn');
    const isOpen = panel.style.display !== 'none';
    panel.style.display = isOpen ? 'none' : 'flex';
    btn.classList.toggle('open', !isOpen);
  }

  function resetAllFilters() {
    ['minBpm', 'maxBpm', 'minEnergy', 'maxEnergy'].forEach(id => $(id).value = '');
    $('minRating').value = '';
    $('sortSelect').value = 'created_at:desc';
    state.selectedTags = [];
    state.q = '';
    $('searchInput').value = '';
    state.mediaType = '';
    state.view = 'grid';
    state.activePlaylistId = null;
    state.minBpm = null;
    state.maxBpm = null;
    state.minEnergy = null;
    state.maxEnergy = null;
    state.minRating = null;
    state.untagged = false;
    state.unrated = false;
    $('untaggedCheck').checked = false;
    $('unratedCheck').checked = false;
    state.sort = 'created_at';
    state.order = 'desc';

    renderTagCloud();
    renderPlaylists();
    resetAndLoadMedia();
    closeSidebarIfMobile();
  }

  function closeScanModal() {
    const modal = $('scanModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  function openDeletePlaylistModal(playlist) {
    if (!playlist) return;
    state.pendingDeletePlaylistId = playlist.id;

    const nameEl = $('playlistDeleteName');
    const metaEl = $('playlistDeleteMeta');
    if (nameEl) nameEl.textContent = playlist.name || '未命名歌单';
    if (metaEl) {
      const count = Number(playlist.item_count) || 0;
      metaEl.textContent = count === 0
        ? '该歌单目前没有素材'
        : `包含 ${count} 条素材`;
    }

    const modal = $('playlistDeleteModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
    const cancelBtn = $('playlistDeleteCancel');
    if (cancelBtn) {
      requestAnimationFrame(() => {
        try { cancelBtn.focus(); } catch (_) { /* ignore */ }
      });
    }
  }

  function closeDeletePlaylistModal() {
    const modal = $('playlistDeleteModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
    state.pendingDeletePlaylistId = null;
  }

  async function confirmDeletePlaylist() {
    const pid = state.pendingDeletePlaylistId;
    if (!pid) {
      closeDeletePlaylistModal();
      return;
    }
    try {
      await api('DELETE', `/api/playlists/${pid}`);
      const wasActive = state.activePlaylistId === pid;
      const wasQueued = state.playlistQueue && state.playlistQueue.playlistId === pid;
      closeDeletePlaylistModal();
      await loadPlaylists();

      if (wasQueued) {
        state.playlistQueue = null;
      }

      if (wasActive) {
        state.view = 'grid';
        state.activePlaylistId = null;
        renderPlaylists();
        resetAndLoadMedia();
      }

      toast('歌单已删除', 'success', 2000);
    } catch (e) {
      toast('删除失败：' + e.message, 'error');
    }
  }

  // ------------------ 导出歌单 ------------------
  function _sanitizeForPath(name) {
    const s = String(name || '我的歌单').trim() || '我的歌单';
    return s.replace(/[\\/:*?"<>|]/g, '_');
  }

  // ---- 批量操作（多选） ----
  function updateBatchBar() {
    const n = state.selectedIds.size;
    const info = $('batchInfo');
    if (info) info.textContent = `已选 ${n} 项`;
    ['batchAddPlaylistBtn', 'batchRatingBtn', 'batchExportBtn', 'batchFavBtn', 'batchUnfavBtn'].forEach(id => {
      const b = $(id);
      if (b) b.disabled = n === 0;
    });
  }

  function clearSelection() {
    state.selectedIds.clear();
    document.querySelectorAll('.media-card.selected').forEach(c => c.classList.remove('selected'));
    updateBatchBar();
  }

  function setSelectMode(on) {
    state.selectMode = on;
    document.body.classList.toggle('select-mode', on);
    const bar = $('batchBar');
    if (bar) bar.style.display = on ? 'flex' : 'none';
    const btn = $('selectModeBtn');
    if (btn) btn.classList.toggle('active', on);
    if (!on) clearSelection();
    updateBatchBar();
  }

  function toggleSelect(id, cardEl) {
    if (state.selectedIds.has(id)) state.selectedIds.delete(id);
    else state.selectedIds.add(id);
    if (cardEl) cardEl.classList.toggle('selected', state.selectedIds.has(id));
    updateBatchBar();
  }

  async function batchSetFavorite(fav) {
    const ids = [...state.selectedIds];
    if (!ids.length) return;
    try {
      const r = await api('POST', '/api/media/batch', { ids, is_favorite: fav });
      ids.forEach(id => {
        const m = state.allMedia.find(x => x.id === id);
        if (m) updateMediaInPlace({ ...m, is_favorite: fav ? 1 : 0 });
      });
      // 卡片已被移出当前视图的（如收藏视图里取消收藏），从选择集中剔除
      ids.forEach(id => {
        if (!document.querySelector(`.media-card[data-id="${id}"]`)) state.selectedIds.delete(id);
      });
      updateBatchBar();
      toast(fav ? `已收藏 ${r.updated} 项` : `已取消收藏 ${r.updated} 项`, 'success');
    } catch (e) {
      toast('批量操作失败：' + e.message, 'error');
    }
  }

  function openBatchPlaylistModal() {
    if (!state.selectedIds.size) return;
    $('batchPlaylistSub').textContent = `把已选 ${state.selectedIds.size} 项素材加入：`;
    const list = $('batchPlaylistList');
    if (!state.playlists.length) {
      list.innerHTML = '<div class="cleanup-empty">还没有歌单，先新建一个歌单再来。</div>';
    } else {
      list.innerHTML = state.playlists.map(p =>
        `<button class="batch-playlist-item" data-batch-pid="${p.id}">${escapeHtml(p.name)}<span class="bp-count">${p.item_count || 0} 项</span></button>`
      ).join('');
    }
    const modal = $('batchPlaylistModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
  }

  function closeBatchPlaylistModal() {
    const modal = $('batchPlaylistModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  async function batchAddToPlaylist(pid, btn) {
    const ids = [...state.selectedIds];
    btn.disabled = true;
    let ok = 0, skip = 0;
    for (const mid of ids) {
      try {
        await api('POST', `/api/playlists/${pid}/items`, { media_id: mid });
        ok++;
      } catch (e) { skip++; }
    }
    closeBatchPlaylistModal();
    await loadPlaylists();
    if (ok) toast(`已加入歌单 ${ok} 项` + (skip ? `，跳过 ${skip} 项（已在歌单里）` : ''), 'success');
    else toast('所选素材都已经在该歌单里了', 'error');
    setSelectMode(false);
  }

  // ---- 批量打标签 ----
  function openBatchTagModal() {
    if (!state.selectedIds.size) return;
    $('batchTagSub').textContent = `给已选 ${state.selectedIds.size} 项素材打标签（点已有标签直接打上，或输入新标签）：`;
    const list = $('batchTagList');
    list.innerHTML = state.allTags.length
      ? state.allTags.map(t => `<button class="btn btn-ghost" data-batch-tag="${escapeHtml(t.name)}">${escapeHtml(t.name)} · ${t.count}</button>`).join('')
      : '<div class="cleanup-empty">还没有已有标签，在下面输入第一个。</div>';
    $('batchTagInput').value = '';
    const modal = $('batchTagModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
  }

  function closeBatchTagModal() {
    const modal = $('batchTagModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  async function batchApplyTag(name, btn) {
    const tag = (name || '').trim();
    if (!tag) { toast('标签名不能为空', 'error'); return; }
    const ids = [...state.selectedIds];
    if (!ids.length) { closeBatchTagModal(); return; }
    if (btn) btn.disabled = true;
    let ok = 0, fail = 0;
    for (const mid of ids) {
      try {
        await api('POST', `/api/media/${mid}/tags`, { tags: [tag] });
        ok++;
      } catch (e) { fail++; }
    }
    closeBatchTagModal();
    await loadTags();
    toast(`已给 ${ok} 项打上「${tag}」` + (fail ? `，${fail} 项失败` : ''), fail ? 'error' : 'success');
    setSelectMode(false);
    resetAndLoadMedia();
  }

  // ---- 批量设评分 ----
  function openBatchRatingModal() {
    if (!state.selectedIds.size) return;
    $('batchRatingSub').textContent = `给已选 ${state.selectedIds.size} 项素材统一评分：`;
    const modal = $('batchRatingModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
  }

  function closeBatchRatingModal() {
    const modal = $('batchRatingModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  async function batchApplyRating(n, btn) {
    const ids = [...state.selectedIds];
    if (!ids.length) { closeBatchRatingModal(); return; }
    if (btn) btn.disabled = true;
    try {
      const r = await api('POST', '/api/media/batch', { ids, rating: n });
      ids.forEach(id => {
        const m = state.allMedia.find(x => x.id === id);
        if (m) updateMediaInPlace({ ...m, rating: n });
      });
      updateBatchBar();
      toast(n === 0 ? `已清除 ${r.updated} 项的评分` : `已给 ${r.updated} 项设为 ${n} 星`, 'success');
    } catch (e) {
      toast('批量设评分失败：' + e.message, 'error');
    } finally {
      closeBatchRatingModal();
    }
  }

  // ---- 数据统计 ----
  function fmtTotalDuration(sec) {
    if (sec == null || isNaN(sec)) return '0 分钟';
    const s = Math.round(sec);
    if (s >= 3600) {
      const h = Math.floor(s / 3600);
      const m = Math.round((s % 3600) / 60);
      return m ? `${h} 小时 ${m} 分` : `${h} 小时`;
    }
    if (s >= 60) return `${Math.round(s / 60)} 分钟`;
    return `${s} 秒`;
  }

  function fmtSize(bytes) {
    if (bytes == null || isNaN(bytes)) return '0 MB';
    if (bytes >= 1024 * 1024 * 1024) return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
    if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return bytes + ' B';
  }

  function openStatsModal() {
    const modal = $('statsModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
    const body = $('statsBody');
    body.innerHTML = '<div class="cleanup-empty">正在统计…</div>';
    api('GET', '/api/stats').then(d => {
      const typeNames = { bgm: 'BGM 音乐', sfx: '音效', image: '图片', video: '视频' };
      const typeRows = Object.keys(typeNames).map(k => {
        const t = d.by_type[k] || { count: 0, duration_sec: 0, size_bytes: 0 };
        const extra = (k === 'bgm' || k === 'sfx' || k === 'video')
          ? `${fmtTotalDuration(t.duration_sec)} · ${fmtSize(t.size_bytes)}`
          : fmtSize(t.size_bytes);
        return `<div class="stats-type-row"><span class="st-name">${typeNames[k]}</span><span class="st-num">${t.count} 项</span><span class="st-num">${extra}</span></div>`;
      }).join('');
      const tagsHtml = d.top_tags.length
        ? `<div class="stats-section-title">最常用标签</div><div class="stats-tags">${d.top_tags.map(t => `<span class="detail-tag">${escapeHtml(t.name)} · ${t.count}</span>`).join('')}</div>`
        : '';
      body.innerHTML = `
        <div class="stats-grid">
          <div class="stat-card"><div class="stat-value">${d.total}</div><div class="stat-label">素材总数</div></div>
          <div class="stat-card"><div class="stat-value">${fmtTotalDuration(d.total_duration_sec)}</div><div class="stat-label">音视频总时长</div></div>
          <div class="stat-card"><div class="stat-value">${fmtSize(d.total_size_bytes)}</div><div class="stat-label">占用空间</div></div>
          <div class="stat-card"><div class="stat-value">${d.favorites}</div><div class="stat-label">已收藏</div></div>
          <div class="stat-card"><div class="stat-value">${d.playlists}</div><div class="stat-label">歌单（共 ${d.playlist_items} 项）</div></div>
          <div class="stat-card"><div class="stat-value">${d.avg_rating != null ? d.avg_rating : '—'}</div><div class="stat-label">平均评分</div></div>
        </div>
        <div class="stats-section-title">按类型</div>
        ${typeRows}
        <div class="stats-section-title">评分分布</div>
        ${[5, 4, 3, 2, 1].map(n => `<div class="stats-type-row"><span class="st-name">${'★'.repeat(n)}</span><span class="st-num">${(d.by_rating || {})[n] || 0} 项</span></div>`).join('')}
        <div class="stats-type-row"><span class="st-name">未评分</span><span class="st-num">${(d.by_rating || {})[0] || 0} 项</span></div>
        <div class="stats-section-title">时长分布</div>
        ${[['lt30', '短于 30 秒'], ['b30_120', '30 秒 – 2 分钟'], ['b120_300', '2 – 5 分钟'], ['ge300', '长于 5 分钟']].map(([k, label]) => `<div class="stats-type-row"><span class="st-name">${label}</span><span class="st-num">${(d.by_duration || {})[k] || 0} 项</span></div>`).join('')}
        ${(d.by_duration || {}).nodur ? `<div class="stats-type-row"><span class="st-name">无时长（图片）</span><span class="st-num">${d.by_duration.nodur} 项</span></div>` : ''}
        ${tagsHtml}
      `;
    }).catch(e => {
      body.innerHTML = '<div class="cleanup-empty">统计失败：' + escapeHtml(e.message) + '</div>';
    });
  }

  function closeStatsModal() {
    const modal = $('statsModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  // ---- 标签管理 ----
  function renderTagManageList() {
    const list = $('tagManageList');
    if (!state.allTags.length) {
      list.innerHTML = '<div class="cleanup-empty">还没有标签。在素材详情里添加第一个标签吧。</div>';
      return;
    }
    list.innerHTML = state.allTags.map(t => `
      <div class="tm-row" data-tag-id="${t.id}">
        <span class="tm-name">${escapeHtml(t.name)}</span>
        <span class="tm-count">${t.count} 项</span>
        <button class="btn btn-ghost" data-tm-act="rename">改名</button>
        <button class="btn btn-ghost" data-tm-act="delete">删除</button>
      </div>`).join('');
  }

  function openTagManageModal() {
    renderTagManageList();
    const modal = $('tagManageModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
  }

  function closeTagManageModal() {
    const modal = $('tagManageModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  async function submitTagRename(tagId, oldName, input) {
    const name = input.value.trim();
    if (!name) { toast('标签名不能为空', 'error'); return; }
    if (name === oldName) { renderTagManageList(); return; }
    try {
      await api('PATCH', `/api/tags/${tagId}`, { name });
      const idx = state.selectedTags.indexOf(oldName);
      if (idx >= 0) {
        state.selectedTags[idx] = name;
        renderFilterChips();
        resetAndLoadMedia();
      }
      await loadTags();
      renderTagManageList();
      toast('标签已改名', 'success', 1500);
    } catch (e) {
      toast('改名失败：' + e.message, 'error');
    }
  }

  async function submitTagDelete(tagId, name) {
    try {
      await api('DELETE', `/api/tags/${tagId}`);
      const idx = state.selectedTags.indexOf(name);
      if (idx >= 0) {
        state.selectedTags.splice(idx, 1);
        renderFilterChips();
        resetAndLoadMedia();
      }
      await loadTags();
      renderTagManageList();
      toast('标签已删除', 'success', 1500);
    } catch (e) {
      toast('删除失败：' + e.message, 'error');
    }
  }

  // ---- 失效素材清理 ----
  async function refreshMissingCount() {
    try {
      const data = await api('GET', '/api/media/missing');
      const badge = $('missingCount');
      if (badge) {
        badge.textContent = data.total;
        badge.style.display = data.total > 0 ? '' : 'none';
      }
    } catch (e) { /* 计数失败不打扰使用 */ }
  }

  function openCleanupModal() {
    const modal = $('cleanupModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
    const list = $('cleanupList');
    const purgeBtn = $('cleanupPurge');
    list.innerHTML = '<div class="cleanup-empty">正在检查…</div>';
    purgeBtn.disabled = true;
    purgeBtn.textContent = '删除全部记录';
    api('GET', '/api/media/missing').then(data => {
      if (!data.items.length) {
        list.innerHTML = '<div class="cleanup-empty">没有失效素材，所有记录对应的文件都在。</div>';
        return;
      }
      list.innerHTML = data.items.map(it => `
        <div class="cleanup-item">
          <div class="ci-name">${escapeHtml(it.file_name)}</div>
          <div class="ci-path">${escapeHtml(it.file_path)}</div>
        </div>`).join('');
      purgeBtn.disabled = false;
      purgeBtn.textContent = `删除全部 ${data.total} 条记录`;
    }).catch(e => {
      list.innerHTML = '<div class="cleanup-empty">检查失败：' + escapeHtml(e.message) + '</div>';
    });
  }

  function closeCleanupModal() {
    const modal = $('cleanupModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  async function submitCleanupPurge() {
    const btn = $('cleanupPurge');
    btn.disabled = true;
    try {
      const r = await api('POST', '/api/media/missing/purge');
      closeCleanupModal();
      toast(`已删除 ${r.removed} 条失效记录`, 'success');
      await Promise.all([resetAndLoadMedia(), loadPlaylists(), refreshMissingCount()]);
    } catch (e) {
      toast('清理失败：' + e.message, 'error');
      btn.disabled = false;
    }
  }

  // ---- 重复素材检测 ----
  async function refreshDupCount() {
    try {
      const data = await api('GET', '/api/media/duplicates');
      const badge = $('dupCount');
      if (badge) {
        badge.textContent = data.extra_items;
        badge.style.display = data.extra_items > 0 ? '' : 'none';
      }
    } catch (e) { /* 计数失败不打扰使用 */ }
  }

  function renderDupList(data) {
    const summary = $('dupSummary');
    const list = $('dupList');
    if (!data.groups.length) {
      summary.innerHTML = '';
      list.innerHTML = '<div class="cleanup-empty">没有发现重复素材，库里很干净。</div>';
      return;
    }
    summary.innerHTML = `<div class="dup-summary">共 ${data.total_groups} 组重复、${data.extra_items} 条多余记录，约可省 ${fmtSize(data.wasted_bytes)}</div>`;
    const typeNames = { bgm: 'BGM 音乐', sfx: '音效', image: '图片', video: '视频' };
    list.innerHTML = data.groups.map(g => `
      <div class="dup-group">
        <div class="dup-group-head">${typeNames[g.media_type] || g.media_type} · ${fmtSize(g.file_size)}${g.duration != null ? ' · ' + fmtDuration(g.duration) : ''} · ${g.count} 条</div>
        ${g.items.map(it => `
          <div class="cleanup-item">
            <div class="ci-name">${escapeHtml(it.title || it.file_name)}</div>
            <div class="ci-path">${escapeHtml(it.file_path)}</div>
            <div class="dup-actions">
              <button class="btn btn-ghost" data-dup-act="open" data-dup-id="${it.id}">打开</button>
              <button class="btn btn-ghost" data-dup-act="delete" data-dup-id="${it.id}">删记录</button>
            </div>
          </div>`).join('')}
      </div>`).join('');
  }

  async function reloadDupList() {
    const data = await api('GET', '/api/media/duplicates');
    renderDupList(data);
    const badge = $('dupCount');
    if (badge) {
      badge.textContent = data.extra_items;
      badge.style.display = data.extra_items > 0 ? '' : 'none';
    }
  }

  function openDupModal() {
    const modal = $('dupModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
    $('dupList').innerHTML = '<div class="cleanup-empty">正在检测…</div>';
    $('dupSummary').innerHTML = '';
    api('GET', '/api/media/duplicates').then(renderDupList).catch(e => {
      $('dupList').innerHTML = '<div class="cleanup-empty">检测失败：' + escapeHtml(e.message) + '</div>';
    });
  }

  function closeDupModal() {
    const modal = $('dupModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  async function submitDupDelete(mediaId, btn) {
    try {
      await api('DELETE', `/api/media/${mediaId}`);
      toast('已删除该条记录（源文件未动）', 'success', 1500);
      await reloadDupList();
      await Promise.all([resetAndLoadMedia(), loadPlaylists(), refreshMissingCount()]);
    } catch (e) {
      toast('删除失败：' + e.message, 'error');
      if (btn) { btn.disabled = false; }
    }
  }

  // ---- 最近播放 ----
  const HISTORY_KEY = 'mh.playHistory';

  function loadPlayHistory() {
    try {
      const arr = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }

  function recordPlayHistory(m) {
    if (!m || m.id == null) return;
    const snap = {
      id: m.id,
      file_name: m.file_name,
      title: m.title || null,
      media_type: m.media_type,
      duration: m.duration != null ? m.duration : null,
      bpm: m.bpm != null ? m.bpm : null,
      musical_key: m.musical_key || null,
      sfx_category: m.sfx_category || null,
      played_at: Date.now(),
    };
    const arr = loadPlayHistory().filter(x => x.id !== m.id);
    arr.unshift(snap);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(arr.slice(0, 30))); } catch (e) { /* 存不下就算了 */ }
  }

  function fmtPlayedAt(ts) {
    const d = new Date(ts);
    const now = new Date();
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    if (d.toDateString() === now.toDateString()) return `今天 ${hh}:${mm}`;
    return `${d.getMonth() + 1}月${d.getDate()}日 ${hh}:${mm}`;
  }

  function renderHistoryList() {
    const list = $('historyList');
    const arr = loadPlayHistory();
    if (!arr.length) {
      list.innerHTML = '<div class="cleanup-empty">还没有播放记录，先随便播点什么吧。</div>';
      return;
    }
    const typeNames = { bgm: 'BGM', sfx: '音效', image: '图片', video: '视频' };
    list.innerHTML = arr.map(h => `
      <div class="cleanup-item history-item" data-history-id="${h.id}">
        <div class="ci-name">${escapeHtml(prettyTitle(h))}</div>
        <div class="ci-path">${typeNames[h.media_type] || h.media_type}${h.duration != null ? ' · ' + fmtDuration(h.duration) : ''} · ${fmtPlayedAt(h.played_at)}</div>
      </div>`).join('');
  }

  function openHistoryModal() {
    renderHistoryList();
    const modal = $('historyModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
  }

  function closeHistoryModal() {
    const modal = $('historyModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  // ---- 关于 ----
  function openAboutModal() {
    const modal = $('aboutModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
    const body = $('aboutBody');
    body.innerHTML = '<div class="cleanup-empty">正在读取…</div>';
    Promise.all([api('GET', '/api/about'), api('GET', '/api/stats')]).then(([a, s]) => {
      body.innerHTML = `
        <div class="sc-list">
          <div class="sc-row"><span class="sc-key">版本</span><span class="sc-desc">${escapeHtml(a.version)}</span></div>
          <div class="sc-row"><span class="sc-key">素材总数</span><span class="sc-desc">${s.total} 条 · ${fmtSize(s.total_size_bytes)}</span></div>
          <div class="sc-row"><span class="sc-key">数据库</span><span class="sc-desc" style="word-break:break-all;">${escapeHtml(a.db_path)}</span></div>
          <div class="sc-row"><span class="sc-key">数据目录</span><span class="sc-desc" style="word-break:break-all;">${escapeHtml(a.data_dir)}</span></div>
        </div>`;
    }).catch(e => {
      body.innerHTML = '<div class="cleanup-empty">读取失败：' + escapeHtml(e.message) + '</div>';
    });
  }

  function closeAboutModal() {
    const modal = $('aboutModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  // ---- 重命名文件 ----
  function openRenameModal(m) {
    state.renamingId = m.id;
    $('renameSub').textContent = `当前文件：${m.file_name}。把磁盘上的源文件改名（会真实改动文件）。不写扩展名会自动沿用原来的。`;
    $('renameInput').value = m.file_name || '';
    const modal = $('renameModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
    const input = $('renameInput');
    input.focus();
    input.select();
  }

  function closeRenameModal() {
    const modal = $('renameModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
    state.renamingId = null;
  }

  async function submitRename() {
    const id = state.renamingId;
    if (id == null) return;
    const name = $('renameInput').value.trim();
    if (!name) { toast('文件名不能为空', 'error'); return; }
    const btn = $('renameConfirm');
    btn.disabled = true;
    try {
      const r = await api('PATCH', `/api/media/${id}/rename`, { new_name: name });
      closeRenameModal();
      toast(`已改名为 ${r.file_name}`, 'success');
      resetAndLoadMedia();
      openDetail(id);
    } catch (e) {
      toast('改名失败：' + e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  // ---- 快捷键帮助 ----
  function openShortcutModal() {
    const modal = $('shortcutModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
  }

  function closeShortcutModal() {
    const modal = $('shortcutModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
  }

  function openExportPlaylistModal() {
    const p = state.playlists.find(x => x.id === state.activePlaylistId);
    if (!p) {
      toast('请先打开一个歌单', 'error');
      return;
    }
    state.exportingPlaylistId = p.id;
    state.exportingSelectionIds = null;
    _showExportModal('导出歌单', `把歌单「${p.name}」内素材按顺序复制到目标文件夹，可直接导入剪映。`, `D:\\导出\\${_sanitizeForPath(p.name)}`);
  }

  function openExportSelectionModal() {
    const ids = [...state.selectedIds];
    if (!ids.length) return;
    state.exportingPlaylistId = null;
    state.exportingSelectionIds = ids;
    _showExportModal('导出选中素材', `把已选 ${ids.length} 项素材复制到目标文件夹，可直接导入剪映。`, 'D:\\导出\\选中素材');
  }

  function _showExportModal(title, sub, placeholder) {
    state.exportInProgress = false;
    state.exportSucceeded = false;

    const titleEl = $('exportModalTitle');
    if (titleEl) titleEl.textContent = title;
    const subEl = $('exportModalSub');
    if (subEl) subEl.textContent = sub;

    const destEl = $('exportDestDir');
    if (destEl) {
      destEl.value = '';
      destEl.placeholder = placeholder;
    }

    const resultEl = $('exportResult');
    if (resultEl) {
      resultEl.style.display = 'none';
      resultEl.innerHTML = '';
    }

    const confirmBtn = $('exportPlaylistConfirm');
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = '导出';
    }

    const modal = $('exportPlaylistModal');
    modal.style.display = 'flex';
    openModalFocus(modal);
    if (destEl) {
      requestAnimationFrame(() => {
        try { destEl.focus(); destEl.select(); } catch (_) { /* ignore */ }
      });
    }
  }

  function closeExportPlaylistModal() {
    const modal = $('exportPlaylistModal');
    if (modal) {
      closeModalFocus(modal);
      modal.style.display = 'none';
    }
    state.exportingPlaylistId = null;
    state.exportingSelectionIds = null;
    state.exportInProgress = false;
    state.exportSucceeded = false;
  }

  function _renderExportResult(result) {
    const el = $('exportResult');
    if (!el) return;
    const copied = Number(result.copied) || 0;
    const skipped = Array.isArray(result.skipped) ? result.skipped : [];
    const errors = Array.isArray(result.errors) ? result.errors : [];
    const destDir = result.dest_dir || '';

    const parts = [];
    parts.push(`<div class="er-title">导出完成</div>`);
    parts.push(
      `<div class="er-line">` +
        `<span class="er-count-ok">${copied} 个文件已复制</span>` +
        (skipped.length ? ` · <span class="er-count-skip">${skipped.length} 个跳过</span>` : '') +
        (errors.length ? ` · <span class="er-count-err">${errors.length} 个错误</span>` : '') +
      `</div>`
    );
    if (destDir) {
      parts.push(`<div class="er-line">目标目录：</div>`);
      parts.push(`<div class="er-path">${escapeHtml(destDir)}</div>`);
    }
    if (result.manifest) {
      parts.push(`<div class="er-line">已生成 <span class="er-count-ok">${escapeHtml(result.manifest)}</span></div>`);
    }
    if (skipped.length) {
      const items = skipped.map(s => `<li>${escapeHtml(s)}</li>`).join('');
      parts.push(
        `<details><summary>查看跳过的素材（${skipped.length}）</summary>` +
        `<ul class="export-result-list">${items}</ul></details>`
      );
    }
    if (errors.length) {
      const items = errors.map(s => `<li>${escapeHtml(s)}</li>`).join('');
      parts.push(
        `<details><summary>查看错误信息（${errors.length}）</summary>` +
        `<ul class="export-result-list">${items}</ul></details>`
      );
    }
    el.innerHTML = parts.join('');
    el.style.display = 'block';
  }

  async function submitExportPlaylist() {
    if (state.exportInProgress) return;
    if (state.exportSucceeded) {
      closeExportPlaylistModal();
      return;
    }

    const pid = state.exportingPlaylistId;
    const selIds = state.exportingSelectionIds;
    if (!pid && !(selIds && selIds.length)) {
      toast('未选择歌单', 'error');
      return;
    }

    const destInput = $('exportDestDir');
    const destDir = destInput ? destInput.value.trim() : '';
    if (!destDir) {
      toast('请填写目标文件夹路径', 'error');
      if (destInput) {
        try { destInput.focus(); } catch (_) { /* ignore */ }
      }
      return;
    }

    const confirmBtn = $('exportPlaylistConfirm');
    const cancelBtn = $('exportPlaylistCancel');
    state.exportInProgress = true;
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = '导出中…';
    }
    if (cancelBtn) cancelBtn.disabled = true;

    try {
      const withManifest = !!($('exportManifestCheck') && $('exportManifestCheck').checked);
      const result = selIds && selIds.length
        ? await api('POST', '/api/media/export', { ids: selIds, dest_dir: destDir, with_manifest: withManifest })
        : await api('POST', `/api/playlists/${pid}/export`, { dest_dir: destDir, with_manifest: withManifest });
      state.exportSucceeded = true;
      _renderExportResult(result);
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = '完成';
      }
      if (cancelBtn) cancelBtn.disabled = false;

      const copied = Number(result.copied) || 0;
      const skipped = (result.skipped || []).length;
      const errCount = (result.errors || []).length;
      const parts = [`已导出 ${copied} 条`];
      if (skipped) parts.push(`跳过 ${skipped} 条`);
      if (errCount) parts.push(`错误 ${errCount} 条`);
      toast(parts.join(' · '), errCount ? 'info' : 'success', 2500);
    } catch (e) {
      state.exportInProgress = false;
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = '导出';
      }
      if (cancelBtn) cancelBtn.disabled = false;
      const el = $('exportResult');
      if (el) {
        el.innerHTML =
          `<div class="er-title">导出失败</div>` +
          `<div class="er-line"><span class="er-count-err">${escapeHtml(e.message || '未知错误')}</span></div>`;
        el.style.display = 'block';
      }
      toast('导出失败：' + e.message, 'error');
    } finally {
      state.exportInProgress = false;
    }
  }

  // ------------------ 侧栏键盘激活 ------------------
  function activateNavItem(a) {
    const isFav = a.dataset.favorite === '1';
    if (isFav) {
      state.favoriteOnly = true;
      state.mediaType = '';
    } else {
      state.favoriteOnly = false;
      state.mediaType = a.dataset.type || '';
    }
    state.view = 'grid';
    state.activePlaylistId = null;
    resetAndLoadMedia();
    renderPlaylists();
    updateMediaCount();
    updateLoadMoreUI();
    closeSidebarIfMobile();
  }

  function activateTagChip(chip) {
    const name = chip.dataset.tag;
    if (!name) return;
    const idx = state.selectedTags.indexOf(name);
    if (idx >= 0) state.selectedTags.splice(idx, 1);
    else state.selectedTags.push(name);
    renderTagCloud();
    resetAndLoadMedia();
    closeSidebarIfMobile();
  }

  function activatePlaylistItem(pid) {
    if (!Number.isFinite(pid)) return;
    openPlaylist(pid);
    closeSidebarIfMobile();
  }

  function bindEvents() {
    const hamburger = $('hamburgerBtn');
    if (hamburger) {
      hamburger.addEventListener('click', toggleSidebar);
    }
    const closeBtn = $('sidebarCloseBtn');
    if (closeBtn) {
      closeBtn.addEventListener('click', closeSidebar);
    }
    const backdrop = $('sidebarBackdrop');
    if (backdrop) {
      backdrop.addEventListener('click', closeSidebar);
    }

    window.addEventListener('resize', () => {
      if (!isMobileViewport() && state.sidebarOpen) {
        closeSidebar();
      }
    });

    document.querySelectorAll('.nav-item').forEach(a => {
      a.addEventListener('click', () => activateNavItem(a));
      a.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          activateNavItem(a);
        }
      });
    });

    $('tagCloud').addEventListener('click', (e) => {
      const chip = e.target.closest('.tag-chip');
      if (!chip) return;
      activateTagChip(chip);
    });
    $('tagCloud').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const chip = e.target.closest('.tag-chip');
      if (!chip) return;
      e.preventDefault();
      activateTagChip(chip);
    });

    $('playlistList').addEventListener('click', (e) => {
      const editBtn = e.target.closest('[data-edit-pid]');
      if (editBtn) {
        e.stopPropagation();
        const pid = Number(editBtn.dataset.editPid);
        const p = state.playlists.find(x => x.id === pid);
        if (p) openPlaylistModal(p);
        return;
      }
      const deleteBtn = e.target.closest('[data-delete-pid]');
      if (deleteBtn) {
        e.stopPropagation();
        const pid = Number(deleteBtn.dataset.deletePid);
        const p = state.playlists.find(x => x.id === pid);
        if (p) openDeletePlaylistModal(p);
        return;
      }
      const item = e.target.closest('.playlist-item');
      if (!item) return;
      activatePlaylistItem(Number(item.dataset.pid));
    });
    $('playlistList').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      if (e.target.closest('[data-edit-pid]') || e.target.closest('[data-delete-pid]')) return;
      const item = e.target.closest('.playlist-item');
      if (!item) return;
      e.preventDefault();
      activatePlaylistItem(Number(item.dataset.pid));
    });

    $('newPlaylistBtn').addEventListener('click', () => {
      openPlaylistModal(null);
    });

    let searchTimer = null;
    $('searchInput').addEventListener('input', (e) => {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.q = e.target.value.trim();
        resetAndLoadMedia();
      }, 280);
    });

    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        $('searchInput').focus();
        $('searchInput').select();
      }
    });

    // 空格播放/暂停：在输入框、按钮、弹窗里都不抢，避免误触
    document.addEventListener('keydown', (e) => {
      if (e.key !== ' ' && e.code !== 'Space') return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.tagName === 'BUTTON' || t.isContentEditable)) return;
      const anyModalOpen = [...document.querySelectorAll('.modal-mask')].some(m => m.style.display !== 'none');
      if (anyModalOpen) return;
      if ($('player').style.display === 'none') return;
      e.preventDefault();
      togglePlay();
    });

    $('filterToggleBtn').addEventListener('click', toggleFilterPanel);

    $('filterChips').addEventListener('click', (e) => {
      const btn = e.target.closest('.x');
      if (!btn) return;
      const key = btn.dataset.rm;

      if (key.startsWith('tag:')) {
        const tagName = key.slice(4);
        const idx = state.selectedTags.indexOf(tagName);
        if (idx >= 0) state.selectedTags.splice(idx, 1);
        renderTagCloud();
        resetAndLoadMedia();
        return;
      }

      if (key === 'q') {
        state.q = '';
        $('searchInput').value = '';
      } else if (key === 'minRating') {
        state.minRating = null;
        $('minRating').value = '';
      } else {
        state[key] = null;
        const idMap = { minBpm: 'minBpm', maxBpm: 'maxBpm', minEnergy: 'minEnergy', maxEnergy: 'maxEnergy' };
        if (idMap[key]) $(idMap[key]).value = '';
      }
      resetAndLoadMedia();
    });

    function readFilters() {
      const v = (id) => {
        const val = $(id).value.trim();
        return val === '' ? null : Number(val);
      };
      state.minBpm = v('minBpm');
      state.maxBpm = v('maxBpm');
      state.minEnergy = v('minEnergy');
      state.maxEnergy = v('maxEnergy');
      state.minRating = v('minRating');
      state.untagged = $('untaggedCheck').checked;
      state.unrated = $('unratedCheck').checked;
      const [sortField, order] = $('sortSelect').value.split(':');
      if (sortField === 'random' && state.sort !== 'random') {
        state.randomSeed = Math.floor(Math.random() * 100000) + 1;
      }
      state.sort = sortField;
      state.order = order;
    }
    ['minBpm', 'maxBpm', 'minEnergy', 'maxEnergy', 'minRating', 'sortSelect', 'untaggedCheck', 'unratedCheck'].forEach(id => {
      $(id).addEventListener('change', () => { readFilters(); resetAndLoadMedia(); });
    });

    $('resetFilterBtn').addEventListener('click', resetAllFilters);
    $('emptyResetBtn').addEventListener('click', resetAllFilters);

    $('loadMoreBtn').addEventListener('click', loadMoreMedia);
    $('loadAllBtn').addEventListener('click', loadAllRemaining);

    $('content').addEventListener('click', async (e) => {
      if (e.target.id === 'playPlaylistBtn' || e.target.closest('#playPlaylistBtn')) {
        playPlaylistAll();
        return;
      }

      if (e.target.id === 'playlistExportBtn' || e.target.closest('#playlistExportBtn')) {
        openExportPlaylistModal();
        return;
      }

      const card = e.target.closest('.media-card');
      if (card) {
        const idx = Number(card.dataset.index);
        const mediaId = Number(card.dataset.id);

        if (state.selectMode) {
          toggleSelect(mediaId, card);
          return;
        }

        const queueBtn = e.target.closest('[data-queue-id]');
        if (queueBtn) {
          e.stopPropagation();
          const id = Number(queueBtn.dataset.queueId);
          const m = state.allMedia.find(x => x.id === id);
          if (m) queueNext(m);
          return;
        }

        const favBtn = e.target.closest('[data-fav-id]');
        if (favBtn) {
          e.stopPropagation();
          const id = Number(favBtn.dataset.favId);
          const m = state.allMedia.find(x => x.id === id);
          if (!m) return;
          const next = !m.is_favorite;
          try {
            const updated = await api('PATCH', `/api/media/${id}`, { is_favorite: next });
            updateMediaInPlace(updated);
            toast(updated.is_favorite ? '已收藏' : '已取消收藏', 'success', 1200);
          } catch (err) {
            toast('操作失败：' + err.message, 'error');
          }
          return;
        }

        if (e.target.closest('[data-thumb]')) {
          const m = state.allMedia[idx];
          if (!m) return;
          if (m.media_type === 'image' || m.media_type === 'video') {
            openViewer(m);
          } else {
            const isCurrent = state.currentMedia && state.currentMedia.id === m.id;
            if (isCurrent) {
              togglePlay();
            } else {
              playByIndex(idx);
            }
          }
          return;
        }

        openDetail(mediaId);
        return;
      }

      const row = e.target.closest('.playlist-row');
      if (row) {
        const mediaId = Number(row.dataset.mediaId);
        const moveBtn = e.target.closest('[data-move]');
        if (moveBtn) {
          if (moveBtn.disabled) return;
          moveItem(state.activePlaylistId, mediaId, moveBtn.dataset.move);
          return;
        }
        if (e.target.closest('[data-remove]')) {
          removeItem(state.activePlaylistId, mediaId);
          return;
        }
        if (e.target.closest('[data-play]')) {
          const p = state.playlists.find(x => x.id === state.activePlaylistId);
          const it = p && p.items.find(x => x.media_id === mediaId);
          if (it) {
            if (it.media_type === 'image' || it.media_type === 'video') {
              openViewer(it);
            } else {
              playMedia(it, -1);
            }
          }
          return;
        }
      }
    });

    $('content').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const card = e.target.closest('.media-card');
      if (!card) return;
      if (e.target !== card) return;
      e.preventDefault();
      const mediaId = Number(card.dataset.id);
      if (!mediaId) return;
      if (state.selectMode) {
        toggleSelect(mediaId, card);
        return;
      }
      openDetail(mediaId);
    });

    $('detailClose').addEventListener('click', closeDetail);
    $('detailPrevBtn').addEventListener('click', () => stepDetail(-1));
    $('detailNextBtn').addEventListener('click', () => stepDetail(1));
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      const modal = $('detailModal');
      if (!modal || modal.style.display === 'none') return;
      const tag = (document.activeElement && document.activeElement.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      stepDetail(e.key === 'ArrowLeft' ? -1 : 1);
    });
    $('detailModal').addEventListener('click', (e) => {
      if (e.target === $('detailModal')) closeDetail();
    });

    const infoEl = $('detailInfo');
    if (infoEl) {
      infoEl.addEventListener('scroll', updateDetailScrollHint);
    }

    $('viewerClose').addEventListener('click', closeViewer);
    $('viewerModal').addEventListener('click', (e) => {
      if (e.target === $('viewerModal')) closeViewer();
    });

    $('scanBtn').addEventListener('click', () => {
      const modal = $('scanModal');
      modal.style.display = 'flex';
      resetScanModalToIdle();
      openModalFocus(modal);
    });

    $('scanClose').addEventListener('click', closeScanModal);
    $('scanCancel').addEventListener('click', closeScanModal);
    $('scanStart').addEventListener('click', startScan);
    $('scanDoneBtn').addEventListener('click', closeScanModal);
    $('scanErrorCloseBtn').addEventListener('click', closeScanModal);
    $('scanRetryBtn').addEventListener('click', () => {
      startScan();
    });

    $('scanModal').addEventListener('click', (e) => {
      if (e.target === $('scanModal')) closeScanModal();
    });

    $('backfillBtn').addEventListener('click', startBackfill);

    $('playlistClose').addEventListener('click', closePlaylistModal);
    $('playlistCancel').addEventListener('click', closePlaylistModal);
    $('playlistCreate').addEventListener('click', submitPlaylistForm);
    $('playlistModal').addEventListener('click', (e) => {
      if (e.target === $('playlistModal')) closePlaylistModal();
    });
    $('playlistName').addEventListener('input', updatePlaylistSubmitState);
    $('playlistName').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submitPlaylistForm();
    });

    $('playlistDeleteClose').addEventListener('click', closeDeletePlaylistModal);
    $('playlistDeleteCancel').addEventListener('click', closeDeletePlaylistModal);
    $('playlistDeleteConfirm').addEventListener('click', confirmDeletePlaylist);
    $('playlistDeleteModal').addEventListener('click', (e) => {
      if (e.target === $('playlistDeleteModal')) closeDeletePlaylistModal();
    });

    $('exportPlaylistClose').addEventListener('click', closeExportPlaylistModal);
    $('exportPlaylistCancel').addEventListener('click', closeExportPlaylistModal);
    $('exportPlaylistConfirm').addEventListener('click', submitExportPlaylist);
    $('cleanupMissingBtn').addEventListener('click', openCleanupModal);
    $('cleanupMissingBtn').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCleanupModal(); }
    });
    $('cleanupClose').addEventListener('click', closeCleanupModal);
    $('cleanupCancel').addEventListener('click', closeCleanupModal);
    $('cleanupPurge').addEventListener('click', submitCleanupPurge);
    $('selectModeBtn').addEventListener('click', () => setSelectMode(!state.selectMode));
    $('randomPickBtn').addEventListener('click', randomPickPlay);
    $('densityBtn').addEventListener('click', () => {
      state.density = state.density === 'compact' ? 'standard' : 'compact';
      applyDensity();
    });
    $('batchExitBtn').addEventListener('click', () => setSelectMode(false));
    $('batchSelectAllBtn').addEventListener('click', () => {
      state.allMedia.forEach(m => state.selectedIds.add(m.id));
      document.querySelectorAll('.media-card').forEach(c => {
        if (state.selectedIds.has(Number(c.dataset.id))) c.classList.add('selected');
      });
      updateBatchBar();
    });
    $('batchFavBtn').addEventListener('click', () => batchSetFavorite(true));
    $('batchUnfavBtn').addEventListener('click', () => batchSetFavorite(false));
    $('batchAddPlaylistBtn').addEventListener('click', openBatchPlaylistModal);
    $('batchPlaylistClose').addEventListener('click', closeBatchPlaylistModal);
    $('batchPlaylistCancel').addEventListener('click', closeBatchPlaylistModal);
    $('batchPlaylistList').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-batch-pid]');
      if (btn) batchAddToPlaylist(Number(btn.dataset.batchPid), btn);
    });
    $('batchTagBtn').addEventListener('click', openBatchTagModal);
    $('batchTagClose').addEventListener('click', closeBatchTagModal);
    $('batchTagCancel').addEventListener('click', closeBatchTagModal);
    $('batchRatingBtn').addEventListener('click', openBatchRatingModal);
    $('batchRatingClose').addEventListener('click', closeBatchRatingModal);
    $('batchRatingCancel').addEventListener('click', closeBatchRatingModal);
    $('batchExportBtn').addEventListener('click', openExportSelectionModal);
    document.querySelectorAll('#batchRatingRow .batch-rating-btn').forEach(b => {
      b.addEventListener('click', (e) => batchApplyRating(parseInt(b.dataset.rating, 10), e.currentTarget));
    });
    $('batchTagApply').addEventListener('click', (e) => batchApplyTag($('batchTagInput').value, e.currentTarget));
    $('batchTagInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') batchApplyTag($('batchTagInput').value, $('batchTagApply'));
    });
    $('batchTagList').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-batch-tag]');
      if (btn) batchApplyTag(btn.dataset.batchTag, btn);
    });
    $('manageTagsBtn').addEventListener('click', openTagManageModal);
    $('statsBtn').addEventListener('click', openStatsModal);
    $('statsBtn').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openStatsModal(); }
    });
    $('statsClose').addEventListener('click', closeStatsModal);
    $('statsDone').addEventListener('click', closeStatsModal);
    $('dupBtn').addEventListener('click', openDupModal);
    $('dupBtn').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDupModal(); }
    });
    $('dupClose').addEventListener('click', closeDupModal);
    $('dupDone').addEventListener('click', closeDupModal);
    $('historyBtn').addEventListener('click', openHistoryModal);
    $('historyBtn').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openHistoryModal(); }
    });
    $('historyClose').addEventListener('click', closeHistoryModal);
    $('historyDone').addEventListener('click', closeHistoryModal);
    $('shortcutBtn').addEventListener('click', openShortcutModal);
    $('shortcutBtn').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openShortcutModal(); }
    });
    $('shortcutClose').addEventListener('click', closeShortcutModal);
    $('shortcutDone').addEventListener('click', closeShortcutModal);
    $('aboutBtn').addEventListener('click', openAboutModal);
    $('aboutBtn').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openAboutModal(); }
    });
    $('aboutClose').addEventListener('click', closeAboutModal);
    $('aboutDone').addEventListener('click', closeAboutModal);
    $('renameClose').addEventListener('click', closeRenameModal);
    $('renameCancel').addEventListener('click', closeRenameModal);
    $('renameConfirm').addEventListener('click', submitRename);
    $('renameInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submitRename();
    });
    $('historyClear').addEventListener('click', () => {
      try { localStorage.removeItem(HISTORY_KEY); } catch (e) { /* ignore */ }
      renderHistoryList();
      toast('播放记录已清空', 'success', 1500);
    });
    $('detailInfo').addEventListener('click', (e) => {
      const row = e.target.closest('[data-similar-id]');
      if (row) openDetail(Number(row.dataset.similarId));
    });
    $('historyList').addEventListener('click', (e) => {
      const row = e.target.closest('[data-history-id]');
      if (!row) return;
      const snap = loadPlayHistory().find(x => x.id === Number(row.dataset.historyId));
      if (!snap) return;
      closeHistoryModal();
      playMedia(snap, -1);
    });
    $('dupList').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-dup-act]');
      if (!btn) return;
      const id = Number(btn.dataset.dupId);
      if (btn.dataset.dupAct === 'open') {
        closeDupModal();
        openDetail(id);
      } else if (btn.dataset.dupAct === 'delete') {
        btn.textContent = '确认删记录？';
        btn.classList.remove('btn-ghost');
        btn.classList.add('btn-danger');
        btn.dataset.dupAct = 'confirm-delete';
      } else if (btn.dataset.dupAct === 'confirm-delete') {
        btn.disabled = true;
        submitDupDelete(id, btn);
      }
    });
    $('tagManageClose').addEventListener('click', closeTagManageModal);
    $('tagManageDone').addEventListener('click', closeTagManageModal);
    $('tagManageList').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-tm-act]');
      if (!btn) return;
      const row = btn.closest('.tm-row');
      const tagId = Number(row.dataset.tagId);
      const tag = state.allTags.find(t => t.id === tagId);
      if (!tag) return;
      const act = btn.dataset.tmAct;
      if (act === 'rename') {
        row.innerHTML = `
          <input type="text" class="tm-edit-input" value="${escapeHtml(tag.name)}" />
          <button class="btn btn-primary" data-tm-act="save">保存</button>
          <button class="btn btn-ghost" data-tm-act="cancel">取消</button>
        `;
        const input = row.querySelector('.tm-edit-input');
        input.focus();
        input.select();
        input.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter') submitTagRename(tagId, tag.name, input);
          if (ev.key === 'Escape') { ev.stopPropagation(); renderTagManageList(); }
        });
      } else if (act === 'save') {
        const input = row.querySelector('.tm-edit-input');
        if (input) submitTagRename(tagId, tag.name, input);
      } else if (act === 'cancel') {
        renderTagManageList();
      } else if (act === 'delete') {
        btn.textContent = '确认删除？';
        btn.classList.remove('btn-ghost');
        btn.classList.add('btn-danger');
        btn.dataset.tmAct = 'confirm-delete';
      } else if (act === 'confirm-delete') {
        submitTagDelete(tagId, tag.name);
      }
    });
    $('exportPlaylistModal').addEventListener('click', (e) => {
      if (e.target === $('exportPlaylistModal')) closeExportPlaylistModal();
    });
    $('exportDestDir').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submitExportPlaylist();
    });

    const playModeBtn = $('playModeBtn');
    if (playModeBtn) {
      playModeBtn.addEventListener('click', cyclePlayMode);
    }

    const playRateBtn = $('playRateBtn');
    if (playRateBtn) {
      playRateBtn.addEventListener('click', cyclePlaybackRate);
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeAddToPlaylist();
        closeDetail();
        closeScanModal();
        closePlaylistModal();
        closeViewer();
        closeDeletePlaylistModal();
        closeExportPlaylistModal();
        closeCleanupModal();
        closeBatchPlaylistModal();
        closeBatchTagModal();
        closeTagManageModal();
        closeStatsModal();
        closeDupModal();
        closeHistoryModal();
        closeShortcutModal();
        closeRenameModal();
        closeAboutModal();
        if (state.sidebarOpen) closeSidebar();
      }
    });
  }

  async function init() {
    try { state.density = localStorage.getItem('mh.density') === 'compact' ? 'compact' : 'standard'; }
    catch (e) { state.density = 'standard'; }
    state.playMode = loadPlayMode();
    updatePlayModeButton();
    state.playbackRate = loadPlaybackRate();
    bindPlayer();
    applyPlaybackRate();
    bindEvents();
    applyDensity();
    detectPlatformShortcut();
    await Promise.all([loadTags(), loadPlaylists()]);
    await resetAndLoadMedia();
    refreshMissingCount();
    refreshDupCount();
    syncPlayingState();
    updateMediaCount();
    updateLoadMoreUI();
  }

  document.addEventListener('DOMContentLoaded', init);
})();