/* =========================================================
   素材管家 · 前端应用
   ========================================================= */

(function () {
  'use strict';

  // ------------------ 全局状态 ------------------
  const state = {
    mediaType: '',
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
    view: 'grid',               // 'grid' | 'playlist'
    activePlaylistId: null,
    allMedia: [],
    currentIndex: -1,
    currentMedia: null,
    allTags: [],
    playlists: [],
    scanTaskId: null,
    scanPollTimer: null,
    backfillTaskId: null,
    backfillPollTimer: null,
    audio: null,
  };

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
    const total = Math.floor(s);
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

  async function loadMedia() {
    const params = new URLSearchParams();
    if (state.mediaType) params.set('media_type', state.mediaType);
    if (state.q) params.set('q', state.q);
    if (state.selectedTags.length) params.set('tags', state.selectedTags.join(','));
    if (state.minBpm != null) params.set('min_bpm', state.minBpm);
    if (state.maxBpm != null) params.set('max_bpm', state.maxBpm);
    if (state.minEnergy != null) params.set('min_energy', state.minEnergy);
    if (state.maxEnergy != null) params.set('max_energy', state.maxEnergy);
    if (state.minRating != null) params.set('min_rating', state.minRating);
    params.set('sort', state.sort);
    params.set('order', state.order);
    params.set('page', state.page);
    params.set('page_size', state.pageSize);

    try {
      const data = await api('GET', '/api/media?' + params.toString());
      state.allMedia = data.items;
      renderContent();
    } catch (e) {
      toast('加载素材失败：' + e.message, 'error');
    }
  }

  // ------------------ 渲染：侧边栏 ------------------
  function renderTagCloud() {
    const el = $('tagCloud');
    if (!state.allTags.length) {
      el.innerHTML = '<div style="font-size:11.5px;color:var(--text-dim);padding:4px 8px;">暂无标签</div>';
      return;
    }
    el.innerHTML = state.allTags.map(t => {
      const active = state.selectedTags.includes(t.name) ? 'active' : '';
      return `<span class="tag-chip ${active}" data-tag="${escapeHtml(t.name)}">${escapeHtml(t.name)}<span class="count">${t.count}</span></span>`;
    }).join('');
  }

  function renderPlaylists() {
    const el = $('playlistList');
    if (!state.playlists.length) {
      el.innerHTML = '<div style="font-size:11.5px;color:var(--text-dim);padding:4px 8px;">暂无歌单</div>';
      return;
    }
    el.innerHTML = state.playlists.map(p => {
      const active = (state.view === 'playlist' && state.activePlaylistId === p.id) ? 'active' : '';
      return `<div class="playlist-item ${active}" data-pid="${p.id}">
        <span>${escapeHtml(p.name)}</span>
        <span class="count">${p.item_count}</span>
      </div>`;
    }).join('');
  }

  function updateNavActive() {
    document.querySelectorAll('.nav-item').forEach(a => {
      a.classList.toggle('active', a.dataset.type === state.mediaType && state.view === 'grid');
    });
  }

  // ------------------ 渲染：内容区 ------------------
  function renderContent() {
    updateNavActive();
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

    // grid 视图
    header.classList.remove('visible');
    playlistView.style.display = 'none';
    grid.style.display = 'grid';

    if (!state.allMedia.length) {
      grid.innerHTML = '';
      empty.style.display = 'flex';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = state.allMedia.map((m, i) => renderCard(m, i)).join('');
  }

  function renderCard(m, index) {
    const thumbUrl = `/api/media/${m.id}/thumbnail`;
    const displayTitle = m.title || m.file_name;
    const starsHtml = [1, 2, 3, 4, 5].map(n =>
      `<span class="${n <= m.rating ? '' : 'empty'}">★</span>`
    ).join('');
    const favClass = m.is_favorite ? 'active' : '';
    const favChar = m.is_favorite ? '♥' : '♡';

    // 元信息（一行）
    const metaParts = [];
    if (m.media_type === 'bgm') {
      if (m.bpm != null) metaParts.push(`BPM ${Math.round(m.bpm)}`);
      if (m.musical_key) metaParts.push(m.musical_key);
      if (m.duration != null) metaParts.push(fmtDuration(m.duration));
    } else if (m.media_type === 'sfx') {
      if (m.sfx_category) metaParts.push(m.sfx_category);
      if (m.duration != null) metaParts.push(fmtDuration(m.duration));
    } else if (m.media_type === 'image') {
      if (m.width && m.height) metaParts.push(`${m.width}×${m.height}`);
    } else if (m.media_type === 'video') {
      if (m.width && m.height) metaParts.push(`${m.width}×${m.height}`);
      if (m.duration != null) metaParts.push(fmtDuration(m.duration));
    }

    const tagsHtml = (m.tags || []).slice(0, 3).map(t =>
      `<span class="card-tag">${escapeHtml(t)}</span>`
    ).join('');

    return `
      <div class="media-card" data-index="${index}" data-id="${m.id}">
        <div class="card-thumb" data-thumb="1">
          <img src="${thumbUrl}" alt="" loading="lazy" onerror="this.style.display='none'">
          <div class="card-play-hint"><div class="play-icon">▶</div></div>
        </div>
        <div class="card-body">
          <div class="card-title" title="${escapeHtml(displayTitle)}">${escapeHtml(displayTitle)}</div>
          <div class="card-meta">${escapeHtml(metaParts.join(' · '))}</div>
          <div class="card-tags">${tagsHtml}</div>
          <div class="card-bottom">
            <span class="card-stars">${starsHtml}</span>
            <span class="card-fav ${favClass}" data-fav-id="${m.id}">${favChar}</span>
          </div>
        </div>
      </div>
    `;
  }

  function renderPlaylistView() {
    const container = $('playlistView');
    const playlist = state.playlists.find(p => p.id === state.activePlaylistId);
    if (!playlist) {
      container.innerHTML = '<div class="empty-state"><div class="empty-icon">🎧</div><div class="empty-text">歌单已删除或加载失败</div></div>';
      return;
    }
    $('viewTitle').textContent = playlist.name;
    $('viewSub').textContent = (playlist.description || '') +
      (playlist.item_count ? ` · ${playlist.item_count} 个条目` : ' · 空');
    $('viewActions').innerHTML = '';

    if (!playlist.items || !playlist.items.length) {
      container.innerHTML = '<div class="empty-state"><div class="empty-icon">🎵</div><div class="empty-text">该歌单暂无素材，从卡片详情里加入</div></div>';
      return;
    }

    container.innerHTML = playlist.items.map((it, idx) => {
      const title = it.title || it.file_name;
      const dur = it.duration != null ? fmtDuration(it.duration) : '';
      return `
        <div class="playlist-row" data-media-id="${it.media_id}" data-index="${idx}">
          <div class="row-index">${idx + 1}</div>
          <div class="row-thumb" data-play="${it.media_id}">
            <img src="/api/media/${it.media_id}/thumbnail" alt="" onerror="this.style.display='none'">
          </div>
          <div class="row-title" data-play="${it.media_id}">${escapeHtml(title)}</div>
          <div class="row-duration">${dur}</div>
          <div class="row-actions">
            <button data-move="up" ${idx === 0 ? 'disabled' : ''}>↑</button>
            <button data-move="down" ${idx === playlist.items.length - 1 ? 'disabled' : ''}>↓</button>
            <button class="danger" data-remove="1">删</button>
          </div>
        </div>
      `;
    }).join('');
  }

  // ------------------ 播放器 ------------------
  function isPlayable(m) {
    return m && (m.media_type === 'bgm' || m.media_type === 'sfx' || m.media_type === 'video');
  }

  function playByIndex(index) {
    const m = state.allMedia[index];
    if (!m) return;
    if (m.media_type === 'image' || m.media_type === 'video') {
      openViewer(m);
      return;
    }
    if (!isPlayable(m)) {
      toast('该素材不支持播放', 'info');
      return;
    }
    playMedia(m, index);
  }

  // ------------------ 查看器（视频播放 / 图片原图） ------------------
  function openViewer(m) {
    if (!m) return;
    const body = $('viewerBody');
    const streamUrl = `/api/media/${m.id}/stream`;
    if (m.media_type === 'video') {
      body.innerHTML = `<video src="${streamUrl}" controls autoplay playsinline></video>`;
    } else if (m.media_type === 'image') {
      body.innerHTML = `<img src="${streamUrl}" alt="">`;
    } else {
      return;
    }
    $('viewerCaption').textContent = m.title || m.file_name;
    $('viewerModal').style.display = 'flex';
  }

  function closeViewer() {
    $('viewerModal').style.display = 'none';
    $('viewerBody').innerHTML = '';
  }

  function playMedia(m, index) {
    // 视频一律走查看器（带画面的播放器），不进底部音频播放器
    if (m && m.media_type === 'video') {
      openViewer(m);
      return;
    }
    state.currentIndex = index != null ? index : -1;
    state.currentMedia = m;
    const audio = state.audio;
    audio.src = `/api/media/${m.id}/stream`;
    audio.play().then(() => {
      $('playBtn').textContent = '⏸';
    }).catch(err => {
      toast('播放失败：' + err.message, 'error');
    });

    $('player').style.display = 'grid';
    $('playerTitle').textContent = m.title || m.file_name;
    const subParts = [];
    if (m.media_type === 'bgm') {
      if (m.bpm != null) subParts.push(`BPM ${Math.round(m.bpm)}`);
      if (m.musical_key) subParts.push(m.musical_key);
    } else if (m.media_type === 'sfx') {
      if (m.sfx_category) subParts.push(m.sfx_category);
    }
    if (m.duration != null) subParts.push(fmtDuration(m.duration));
    $('playerSub').textContent = subParts.join(' · ');
    $('playerThumb').innerHTML = `<img src="/api/media/${m.id}/thumbnail" alt="" onerror="this.style.display='none'">`;
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
  }

  function playNext() {
    if (state.currentIndex < 0) return;
    const i = findPlayable(state.currentIndex, 1);
    if (i >= 0) playByIndex(i);
  }

  function playPrev() {
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
      playNext();
    });

    audio.addEventListener('pause', () => { $('playBtn').textContent = '▶'; });
    audio.addEventListener('play', () => { $('playBtn').textContent = '⏸'; });

    $('playBtn').addEventListener('click', togglePlay);
    $('nextBtn').addEventListener('click', playNext);
    $('prevBtn').addEventListener('click', playPrev);

    $('progressBar').addEventListener('input', (e) => {
      const dur = audio.duration || 0;
      if (dur > 0) {
        audio.currentTime = (Number(e.target.value) / 1000) * dur;
      }
    });

    $('volumeBar').addEventListener('input', (e) => {
      audio.volume = Math.max(0, Math.min(1, Number(e.target.value) / 100));
    });
    audio.volume = 0.8;
  }

  // ------------------ 详情弹窗 ------------------
  async function openDetail(mediaId) {
    try {
      const m = await api('GET', `/api/media/${mediaId}`);
      showDetailModal(m);
    } catch (e) {
      toast('加载详情失败：' + e.message, 'error');
    }
  }

  function showDetailModal(m) {
    $('detailModal').style.display = 'flex';
    const title = m.title || m.file_name;
    $('detailThumb').innerHTML = `<img src="/api/media/${m.id}/thumbnail" alt="" onerror="this.style.display='none'">`;

    const metaRows = buildMetaRows(m);

    const starsHtml = [1, 2, 3, 4, 5].map(n =>
      `<span data-star="${n}" class="${n <= m.rating ? 'active' : ''}">★</span>`
    ).join('');

    const tagsHtml = (m.tags || []).map(t =>
      `<span class="detail-tag">${escapeHtml(t)}<span class="detail-tag-remove" data-remove-tag="${escapeHtml(t)}">×</span></span>`
    ).join('') || '<span style="color:var(--text-dim);font-size:12px;">暂无标签</span>';

    const favClass = m.is_favorite ? 'active' : '';
    const favChar = m.is_favorite ? '♥' : '♡';

    $('detailInfo').innerHTML = `
      <div class="detail-title">${escapeHtml(title)}</div>
      <div class="detail-filename">${escapeHtml(m.file_name)}</div>

      <div class="detail-meta-grid">
        ${metaRows}
      </div>

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
        ${m.media_type === 'image' ? '<button class="btn btn-primary" id="detailViewBtn">查看原图</button>' : ''}
        <button class="btn" id="detailAddPlaylistBtn">加入歌单</button>
      </div>
    `;

    // 事件：评分
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

    // 事件：收藏
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

    // 事件：加标签
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
      } catch (e) {
        toast('添加标签失败：' + e.message, 'error');
      }
    }
    $('detailTagAdd').addEventListener('click', addTag);
    $('detailTagInput').addEventListener('keydown', e => {
      if (e.key === 'Enter') addTag();
    });

    // 事件：删标签
    function renderDetailTags() {
      const container = $('detailTags');
      if (!m.tags || !m.tags.length) {
        container.innerHTML = '<span style="color:var(--text-dim);font-size:12px;">暂无标签</span>';
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
          } catch (e) {
            toast('删除标签失败：' + e.message, 'error');
          }
        });
      });
    }
    renderDetailTags();

    // 事件：播放
    const playBtn = $('detailPlayBtn');
    if (playBtn) {
      playBtn.addEventListener('click', () => {
        playMedia(m, state.allMedia.findIndex(x => x.id === m.id));
        closeDetail();
      });
    }

    // 事件：查看原图（图片）
    const viewBtn = $('detailViewBtn');
    if (viewBtn) {
      viewBtn.addEventListener('click', () => {
        openViewer(m);
      });
    }

    // 事件：加入歌单
    $('detailAddPlaylistBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      showAddToPlaylist(e.currentTarget, m.id);
    });
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
      // 保留现有 tags（如果 updated 未带）
      const prev = state.allMedia[idx];
      state.allMedia[idx] = { ...prev, ...updated };
    }
    if (state.currentMedia && state.currentMedia.id === updated.id) {
      state.currentMedia = { ...state.currentMedia, ...updated };
    }
    // 局部刷新卡片 DOM，避免整页重绘
    const cardEl = document.querySelector(`.media-card[data-id="${updated.id}"]`);
    if (cardEl) {
      const starsEl = cardEl.querySelector('.card-stars');
      if (starsEl) {
        starsEl.innerHTML = [1, 2, 3, 4, 5].map(n =>
          `<span class="${n <= updated.rating ? '' : 'empty'}">★</span>`
        ).join('');
      }
      const favEl = cardEl.querySelector('.card-fav');
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
    }
  }

  function closeDetail() {
    $('detailModal').style.display = 'none';
  }

  // ------------------ 加入歌单下拉 ------------------
  function showAddToPlaylist(anchorEl, mediaId) {
    const dd = $('addToPlaylistDropdown');
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
    const rect = anchorEl.getBoundingClientRect();
    dd.style.display = 'block';
    dd.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 220)) + 'px';
    dd.style.top = Math.min(rect.bottom + 6, window.innerHeight - 340) + 'px';

    dd.querySelectorAll('.dropdown-item').forEach(el => {
      el.addEventListener('click', async () => {
        const pid = Number(el.dataset.pid);
        try {
          await api('POST', `/api/playlists/${pid}/items`, { media_id: mediaId });
          toast('已加入歌单', 'success', 1500);
          dd.style.display = 'none';
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

  // 点击外部关闭下拉
  document.addEventListener('click', (e) => {
    const dd = $('addToPlaylistDropdown');
    if (dd.style.display === 'block' && !dd.contains(e.target) && !e.target.closest('#detailAddPlaylistBtn')) {
      dd.style.display = 'none';
    }
  });

  // ------------------ 歌单视图 ------------------
  async function openPlaylist(pid) {
    try {
      const detail = await api('GET', `/api/playlists/${pid}`);
      // 用 detail 更新本地列表里的信息
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

  // ------------------ 扫描 ------------------
  async function startScan() {
    const path = $('scanPath').value.trim();
    const type = $('scanType').value;
    const jsonPath = $('scanJson').value.trim();

    if (!path) {
      toast('请填写目录路径', 'error');
      return;
    }

    const body = {
      entries: [{ path, media_type: type }],
    };
    if (jsonPath) body.feature_index = jsonPath;

    $('scanProgressWrap').style.display = 'block';
    $('scanProgressFill').style.width = '0%';
    $('scanProgressText').textContent = '正在启动…';
    $('scanStart').disabled = true;

    try {
      const res = await api('POST', '/api/scan/start', body);
      state.scanTaskId = res.task_id;
      startScanPolling();
    } catch (e) {
      $('scanStart').disabled = false;
      $('scanProgressText').textContent = '启动失败：' + e.message;
      toast('扫描启动失败：' + e.message, 'error');
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
          $('scanStart').disabled = false;
          $('scanProgressText').textContent += ' · 完成';
          toast('扫描完成', 'success');
          await Promise.all([loadMedia(), loadTags()]);
        } else if (t.status === 'error') {
          clearInterval(state.scanPollTimer);
          state.scanPollTimer = null;
          state.scanTaskId = null;
          $('scanStart').disabled = false;
          $('scanProgressText').textContent = '失败：' + (t.error_message || '未知错误');
          toast('扫描失败：' + (t.error_message || '未知错误'), 'error');
        }
      } catch (e) {
        // 忽略轮询瞬时错误
      }
    }, 1000);
  }

  // ------------------ 特征补全 ------------------
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
      } catch (e) {
        // ignore
      }
    }, 2000);
  }

  // ------------------ 新建歌单 ------------------
  async function createPlaylist() {
    const name = $('playlistName').value.trim();
    const description = $('playlistDesc').value.trim() || null;
    if (!name) {
      toast('歌单名不能为空', 'error');
      return;
    }
    try {
      await api('POST', '/api/playlists', { name, description });
      $('playlistModal').style.display = 'none';
      $('playlistName').value = '';
      $('playlistDesc').value = '';
      await loadPlaylists();
      toast('歌单已创建', 'success');
    } catch (e) {
      toast('创建失败：' + e.message, 'error');
    }
  }

  // ------------------ 事件绑定 ------------------
  function bindEvents() {
    // 侧边栏：类型切换
    document.querySelectorAll('.nav-item').forEach(a => {
      a.addEventListener('click', () => {
        state.mediaType = a.dataset.type || '';
        state.view = 'grid';
        state.activePlaylistId = null;
        loadMedia();
        renderPlaylists();
      });
    });

    // 侧边栏：标签云
    $('tagCloud').addEventListener('click', (e) => {
      const chip = e.target.closest('.tag-chip');
      if (!chip) return;
      const name = chip.dataset.tag;
      const idx = state.selectedTags.indexOf(name);
      if (idx >= 0) state.selectedTags.splice(idx, 1);
      else state.selectedTags.push(name);
      renderTagCloud();
      loadMedia();
    });

    // 侧边栏：歌单列表
    $('playlistList').addEventListener('click', (e) => {
      const item = e.target.closest('.playlist-item');
      if (!item) return;
      openPlaylist(Number(item.dataset.pid));
    });

    // 侧边栏：新建歌单
    $('newPlaylistBtn').addEventListener('click', () => {
      $('playlistModal').style.display = 'flex';
      setTimeout(() => $('playlistName').focus(), 50);
    });

    // 搜索
    let searchTimer = null;
    $('searchInput').addEventListener('input', (e) => {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.q = e.target.value.trim();
        loadMedia();
      }, 280);
    });

    // 筛选条
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
      const [sortField, order] = $('sortSelect').value.split(':');
      state.sort = sortField;
      state.order = order;
    }
    ['minBpm', 'maxBpm', 'minEnergy', 'maxEnergy', 'minRating', 'sortSelect'].forEach(id => {
      $(id).addEventListener('change', () => { readFilters(); loadMedia(); });
    });

    $('resetFilterBtn').addEventListener('click', () => {
      ['minBpm', 'maxBpm', 'minEnergy', 'maxEnergy'].forEach(id => $(id).value = '');
      $('minRating').value = '';
      $('sortSelect').value = 'created_at:desc';
      state.selectedTags = [];
      state.q = '';
      $('searchInput').value = '';
      readFilters();
      renderTagCloud();
      loadMedia();
    });

    // 卡片区事件代理
    $('content').addEventListener('click', async (e) => {
      // 卡片
      const card = e.target.closest('.media-card');
      if (card) {
        const idx = Number(card.dataset.index);
        const mediaId = Number(card.dataset.id);

        // 缩略图区域（含 play-icon）→ 播放
        if (e.target.closest('[data-thumb]') || e.target.classList.contains('play-icon')) {
          playByIndex(idx);
          return;
        }

        // 收藏心形
        const favEl = e.target.closest('[data-fav-id]');
        if (favEl) {
          e.stopPropagation();
          const id = Number(favEl.dataset.favId);
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

        // 其它区域 → 打开详情
        openDetail(mediaId);
        return;
      }

      // 歌单视图行
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
          // 在歌单视图里播放：找到对应 media 数据
          const p = state.playlists.find(x => x.id === state.activePlaylistId);
          const it = p && p.items.find(x => x.media_id === mediaId);
          if (it) playMedia(it, -1);
          return;
        }
      }
    });

    // 详情弹窗关闭
    $('detailClose').addEventListener('click', closeDetail);
    $('detailModal').addEventListener('click', (e) => {
      if (e.target === $('detailModal')) closeDetail();
    });

    // 查看器弹窗关闭
    $('viewerClose').addEventListener('click', closeViewer);
    $('viewerModal').addEventListener('click', (e) => {
      if (e.target === $('viewerModal')) closeViewer();
    });

    // 扫描弹窗
    $('scanBtn').addEventListener('click', () => {
      $('scanModal').style.display = 'flex';
      $('scanProgressWrap').style.display = 'none';
      $('scanProgressFill').style.width = '0%';
      $('scanProgressText').textContent = '';
    });
    $('scanClose').addEventListener('click', () => $('scanModal').style.display = 'none');
    $('scanCancel').addEventListener('click', () => $('scanModal').style.display = 'none');
    $('scanStart').addEventListener('click', startScan);
    $('scanModal').addEventListener('click', (e) => {
      if (e.target === $('scanModal')) $('scanModal').style.display = 'none';
    });

    // 补全
    $('backfillBtn').addEventListener('click', startBackfill);

    // 新建歌单弹窗
    $('playlistClose').addEventListener('click', () => $('playlistModal').style.display = 'none');
    $('playlistCancel').addEventListener('click', () => $('playlistModal').style.display = 'none');
    $('playlistCreate').addEventListener('click', createPlaylist);
    $('playlistModal').addEventListener('click', (e) => {
      if (e.target === $('playlistModal')) $('playlistModal').style.display = 'none';
    });
    $('playlistName').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') createPlaylist();
    });

    // Esc 关闭弹窗
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        $('detailModal').style.display = 'none';
        $('scanModal').style.display = 'none';
        $('playlistModal').style.display = 'none';
        $('addToPlaylistDropdown').style.display = 'none';
      }
    });
  }

  // ------------------ 启动 ------------------
  async function init() {
    bindPlayer();
    bindEvents();
    await Promise.all([loadTags(), loadPlaylists()]);
    await loadMedia();
  }

  document.addEventListener('DOMContentLoaded', init);
})();