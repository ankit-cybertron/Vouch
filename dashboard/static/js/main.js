/**
 * Vouch Dashboard — Client-Side Logic
 *
 * Features:
 *   - GitHub repository URL fetching & dynamic scoring
 *   - Instant sample chip loading
 *   - Live search & table filtering
 *   - Score bar animations
 *   - Proper external links to GitHub & local Vouch PR inspection
 */

(function () {
  'use strict';

  const searchInput = document.getElementById('search-input');
  const fetchBtn = document.getElementById('fetch-repo-btn');
  const filterForm = document.getElementById('filter-form');
  const loadingBanner = document.getElementById('fetch-loading-banner');
  const loadingText = document.getElementById('loading-text');
  const errorBanner = document.getElementById('fetch-error-banner');
  const prTbody = document.getElementById('pr-tbody');
  const cardCount = document.getElementById('card-count');
  const statTotal = document.getElementById('stat-total');
  const statFlagged = document.getElementById('stat-flagged');
  const statRequeued = document.getElementById('stat-requeued');
  const statAvg = document.getElementById('stat-avg');

  // Helper: check if a string looks like a GitHub repo (owner/repo or URL)
  function isRepoQuery(str) {
    const s = str.trim();
    if (!s) return false;
    if (s.includes('github.com')) return true;
    const parts = s.split('/');
    return parts.length === 2 && parts[0].length > 0 && parts[1].length > 0;
  }

  // Format ISO date to "Sep 15, 2026"
  function formatDate(isoStr) {
    if (!isoStr) return '—';
    try {
      const dt = new Date(isoStr);
      return dt.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      });
    } catch (e) {
      return isoStr;
    }
  }

  // Capitalize first letter
  function capitalize(s) {
    if (!s) return '';
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  // Animate score bars
  function animateBars() {
    const fills = document.querySelectorAll('.score-bar-fill, .gauge-bar-fill, .feature-bar-fill');
    fills.forEach(function (el) {
      const targetWidth = el.style.width;
      el.style.width = '0%';
      requestAnimationFrame(function () {
        setTimeout(function () {
          el.style.transition = 'width 0.6s ease';
          el.style.width = targetWidth;
        }, 60);
      });
    });
  }

  // Render PR rows into the table
  function renderPrRows(prs) {
    if (!prTbody) return;

    if (!prs || prs.length === 0) {
      prTbody.innerHTML = `
        <div style="padding: 48px; text-align: center; color: var(--color-fg-muted);">
          <p style="font-size:16px; font-weight:600; margin-bottom:8px;">No pull requests found.</p>
          <a href="/" class="gh-btn">Reset filters</a>
        </div>
      `;
      if (cardCount) cardCount.textContent = '0 pull requests';
      return;
    }

    const rowsHtml = prs.map(function (pr) {
      const isOpen = pr.state === 'open';
      const statusIcon = isOpen
        ? `<div class="gh-pr-icon open" title="Open pull request"><svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z"></path></svg></div>`
        : `<div class="gh-pr-icon merged" title="Merged pull request"><svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M5 3.25a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0Zm0 2.122a2.25 2.25 0 1 0-1.5 0v.878A2.25 2.25 0 0 0 5.75 8.5h4.5A2.25 2.25 0 0 0 12.5 6.25v-.878a2.25 2.25 0 1 0-1.5 0v.878a.75.75 0 0 1-.75.75h-4.5A.75.75 0 0 1 5 6.25v-.878ZM11.75 4a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5ZM7.25 12a.75.75 0 1 1 1.5 0 .75.75 0 0 1-1.5 0Zm1.5 1.372a2.25 2.25 0 1 0-1.5 0v-1.872a.75.75 0 0 1 .75-.75h.01a.75.75 0 0 1 .74.75v1.872Z"></path></svg></div>`;

      let requeuedBadge = pr.re_queued ? '<span class="gh-label gh-label-requeued">re-review-required</span>' : '';
      const reviewer = pr.reviewer || '—';
      const dateText = formatDate(pr.merged_at || pr.created_at);
      const residualPct = pr.residual_pct || Math.round(pr.residual_risk * 100);

      return `
        <div class="gh-pr-row" id="pr-${pr.pr_number}">
          ${statusIcon}
          <div class="gh-pr-main">
            <div class="gh-pr-title-line">
              <a href="/pr/${pr.repo}/${pr.pr_number}" class="gh-pr-title" id="link-pr-${pr.pr_number}" title="Inspect PR #${pr.pr_number} in Vouch">
                ${escapeHtml(pr.title)}
              </a>
              ${requeuedBadge}
              <span class="gh-label gh-label-${pr.risk_tier}">risk: ${pr.risk_tier} (${Number(pr.residual_risk).toFixed(2)})</span>
              <span class="gh-label gh-label-confidence">confidence: ${pr.confidence_pct || Math.round(pr.review_confidence * 100)}%</span>
            </div>
            <div class="gh-pr-meta">
              <a href="${pr.pr_url}" target="_blank" rel="noopener noreferrer" class="gh-pr-meta-num" title="View #${pr.pr_number} on GitHub">
                #${pr.pr_number}
                <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor" style="vertical-align:-1px; opacity:0.7;">
                  <path d="M3.75 2h3.5a.75.75 0 0 1 0 1.5h-3.5a.25.25 0 0 0-.25.25v8.5c0 .138.112.25.25.25h8.5a.25.25 0 0 0 .25-.25v-3.5a.75.75 0 0 1 1.5 0v3.5A1.75 1.75 0 0 1 12.25 14h-8.5A1.75 1.75 0 0 1 2 12.25v-8.5C2 2.784 2.784 2 3.75 2Zm6.854-1h4.146a.25.25 0 0 1 .25.25v4.146a.25.25 0 0 1-.427.177L13.03 4.03 8.28 8.78a.75.75 0 1 1-1.06-1.06l4.75-4.75-1.543-1.543a.25.25 0 0 1 .177-.427Z"></path>
                </svg>
              </a>
              <span>${isOpen ? 'opened ' + dateText : 'was merged on ' + dateText} by <strong>${escapeHtml(pr.author || 'unknown')}</strong></span>
              <span>·</span>
              <span>Reviewer: <strong>${escapeHtml(reviewer)}</strong></span>
              <span>·</span>
              <span>Diff: <strong>${pr.diff_lines || 0} lines</strong> (${pr.changed_files || 1} files)</span>
            </div>
          </div>
          <div class="gh-pr-aside">
            <div class="gh-meter" title="Residual risk = ${Number(pr.residual_risk).toFixed(2)}">
              <span style="font-size:11px; font-weight:600; color:var(--color-fg-muted);">${Number(pr.residual_risk).toFixed(2)}</span>
              <div class="gh-meter-bar">
                <div class="gh-meter-fill ${pr.risk_tier}" style="width: ${residualPct}%;"></div>
              </div>
            </div>
            <a href="${pr.pr_url}" target="_blank" rel="noopener noreferrer" class="gh-comments-count" title="Comments">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                <path d="M1 2.75C1 1.784 1.784 1 2.75 1h10.5c.966 0 1.75.784 1.75 1.75v7.5A1.75 1.75 0 0 1 13.25 12H9.06l-2.573 2.573A1.458 1.458 0 0 1 4 13.543V12H2.75A1.75 1.75 0 0 1 1 10.25Zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h2a.75.75 0 0 1 .75.75v2.19l2.72-2.72a.749.749 0 0 1 .53-.22h4.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25Z"></path>
              </svg>
              <span>${pr.comments ? pr.comments.length : 1}</span>
            </a>
            <a href="/pr/${pr.repo}/${pr.pr_number}" class="gh-btn gh-btn-sm" id="view-pr-${pr.pr_number}">
              Inspect
            </a>
          </div>
        </div>
      `;
    }).join('');

    prTbody.innerHTML = rowsHtml;
    if (cardCount) cardCount.textContent = `${prs.length} pull requests`;
    animateBars();
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // Perform GitHub repository fetch
  async function fetchRepository(repoQuery) {
    if (!repoQuery) return;

    if (loadingBanner) {
      loadingBanner.style.display = 'block';
      if (loadingText) loadingText.textContent = `Fetching & rating ${repoQuery}…`;
      loadingBanner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    if (errorBanner) {
      errorBanner.style.display = 'none';
      errorBanner.innerHTML = '';
    }

    try {
      const resp = await fetch('/api/fetch-repo', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify({ repo: repoQuery }),
      });

      const data = await resp.json();

      if (!resp.ok || data.error) {
        if (errorBanner) {
          errorBanner.style.display = 'flex';
          errorBanner.innerHTML = `
            <div><strong>Error:</strong> ${escapeHtml(data.error || 'Failed to fetch repository.')}</div>
            <button type="button" class="gh-btn gh-btn-sm" onclick="this.parentElement.style.display='none'">Dismiss</button>
          `;
        }
        return;
      }

      // Update KPIs
      if (data.stats) {
        if (statTotal) statTotal.textContent = data.stats.total_scored;
        if (statFlagged) statFlagged.textContent = data.stats.high_risk_flagged;
        if (statRequeued) statRequeued.textContent = data.stats.requeued_today;
        if (statAvg) statAvg.textContent = Number(data.stats.avg_residual_risk).toFixed(2);
      }

      // Render table
      renderPrRows(data.prs);

      // Update URL without page reload
      const url = new URL(window.location);
      url.searchParams.set('repo', repoQuery);
      window.history.pushState({}, '', url);

    } catch (err) {
      if (errorBanner) {
        errorBanner.style.display = 'flex';
        errorBanner.innerHTML = `<div><strong>Network error:</strong> ${escapeHtml(err.message)}</div>`;
      }
    } finally {
      if (loadingBanner) {
        loadingBanner.style.display = 'none';
      }
    }
  }

  // ─── Event listeners ──────────────────────────────────────
  if (fetchBtn && searchInput) {
    fetchBtn.addEventListener('click', function () {
      const q = searchInput.value.trim();
      if (isRepoQuery(q)) {
        fetchRepository(q);
      } else if (q) {
        // Submit form for regular query
        filterForm.submit();
      }
    });
  }

  if (filterForm && searchInput) {
    filterForm.addEventListener('submit', function (e) {
      const q = searchInput.value.trim();
      if (isRepoQuery(q)) {
        e.preventDefault();
        fetchRepository(q);
      }
    });
  }

  // Sample chips
  document.querySelectorAll('.repo-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      const repo = this.getAttribute('data-repo');
      if (repo && searchInput) {
        searchInput.value = repo;
        fetchRepository(repo);
      }
    });
  });

  const missingPrBanner = document.getElementById('missing-pr-banner');
  const missingPrNumLabel = document.getElementById('missing-pr-num-label');
  const fetchMissingPrBtn = document.getElementById('fetch-missing-pr-btn');

  // Client-side text filtering as user types (when not typing a repo)
  if (searchInput) {
    searchInput.addEventListener('input', function () {
      const q = this.value.trim().toLowerCase();
      if (isRepoQuery(q)) return; // Don't filter rows while user is typing a repo

      const isNumericSearch = /^#?\d+$/.test(q);
      const searchNum = q.replace(/^#/, '');

      const rows = document.querySelectorAll('.gh-pr-row');
      let visibleCount = 0;
      rows.forEach(function (row) {
        const text = row.textContent.toLowerCase();
        const prId = row.id.replace('pr-', '');
        const matches = text.includes(q) || (isNumericSearch && prId === searchNum);
        row.style.display = matches ? '' : 'none';
        if (matches) visibleCount++;
      });
      if (cardCount) {
        cardCount.textContent = `${visibleCount} shown`;
      }

      if (missingPrBanner) {
        if (visibleCount === 0 && isNumericSearch) {
          missingPrBanner.style.display = 'flex';
          if (missingPrNumLabel) missingPrNumLabel.textContent = '#' + searchNum;
          if (fetchMissingPrBtn) fetchMissingPrBtn.setAttribute('data-num', searchNum);
        } else {
          missingPrBanner.style.display = 'none';
        }
      }
    });

    searchInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        const q = this.value.trim();
        if (/^#?\d+$/.test(q) && missingPrBanner && missingPrBanner.style.display !== 'none' && fetchMissingPrBtn) {
          e.preventDefault();
          fetchMissingPrBtn.click();
        }
      }
    });
  }

  // Fetch missing PR button
  if (fetchMissingPrBtn) {
    fetchMissingPrBtn.addEventListener('click', async function () {
      const num = this.getAttribute('data-num');
      const owner = this.getAttribute('data-owner');
      const repo = this.getAttribute('data-repo');
      if (!num || !owner || !repo) return;

      const origText = this.innerHTML;
      this.disabled = true;
      this.innerHTML = '<span class="loading-spinner" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:6px;"></span> Fetching & Rating…';

      try {
        const resp = await fetch('/api/fetch-single-pr', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ owner: owner, repo: repo, pr_number: num }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
          alert(data.error || `Failed to fetch PR #${num}`);
          this.disabled = false;
          this.innerHTML = origText;
          return;
        }

        // Successfully fetched and rated -> reload repo board with this PR filtered
        window.location.href = `/repo/${owner}/${repo}?q=${num}`;
      } catch (err) {
        alert('Network error: ' + err.message);
        this.disabled = false;
        this.innerHTML = origText;
      }
    });
  }

  // ─── Repository Catalog & Modal Interactions ─────────────
  const openModalBtn = document.getElementById('open-fetch-modal-btn');
  const closeModalBtn = document.getElementById('close-fetch-modal-btn');
  const cancelModalBtn = document.getElementById('cancel-fetch-modal-btn');
  const submitModalBtn = document.getElementById('submit-fetch-modal-btn');
  const modalBackdrop = document.getElementById('fetch-modal-backdrop');
  const modalRepoInput = document.getElementById('modal-repo-input');
  const modalLoadingBanner = document.getElementById('modal-loading-banner');
  const modalErrorBanner = document.getElementById('modal-error-banner');
  const repoSearchInput = document.getElementById('repo-search-input');
  const reposTotalBadge = document.getElementById('repos-total-badge');

  function openFetchModal(initialValue) {
    if (!modalBackdrop) return;
    modalBackdrop.style.display = 'flex';
    if (modalErrorBanner) {
      modalErrorBanner.style.display = 'none';
      modalErrorBanner.textContent = '';
    }
    if (modalLoadingBanner) {
      modalLoadingBanner.style.display = 'none';
    }
    if (submitModalBtn) {
      submitModalBtn.disabled = false;
    }
    if (modalRepoInput) {
      if (initialValue) modalRepoInput.value = initialValue;
      setTimeout(function () {
        modalRepoInput.focus();
        if (modalRepoInput.value) modalRepoInput.select();
      }, 50);
    }
  }

  function closeFetchModal() {
    if (!modalBackdrop) return;
    modalBackdrop.style.display = 'none';
    if (modalRepoInput) modalRepoInput.value = '';
    if (modalErrorBanner) modalErrorBanner.style.display = 'none';
    if (modalLoadingBanner) modalLoadingBanner.style.display = 'none';
  }

  if (openModalBtn) {
    openModalBtn.addEventListener('click', function () {
      openFetchModal();
    });
  }

  if (closeModalBtn) {
    closeModalBtn.addEventListener('click', closeFetchModal);
  }

  if (cancelModalBtn) {
    cancelModalBtn.addEventListener('click', closeFetchModal);
  }

  if (modalBackdrop) {
    modalBackdrop.addEventListener('click', function (e) {
      if (e.target === modalBackdrop) {
        closeFetchModal();
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && modalBackdrop.style.display !== 'none') {
        closeFetchModal();
      }
    });
  }

  async function handleModalFetch() {
    if (!modalRepoInput) return;
    const val = modalRepoInput.value.trim();
    if (!val) {
      if (modalErrorBanner) {
        modalErrorBanner.style.display = 'block';
        modalErrorBanner.textContent = 'Please enter a GitHub repository URL or owner/repo.';
      }
      modalRepoInput.focus();
      return;
    }

    if (modalErrorBanner) modalErrorBanner.style.display = 'none';
    if (modalLoadingBanner) modalLoadingBanner.style.display = 'block';
    if (submitModalBtn) submitModalBtn.disabled = true;

    try {
      const resp = await fetch('/api/fetch-repo', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify({ repo: val }),
      });

      const data = await resp.json();

      if (!resp.ok || data.error) {
        if (modalErrorBanner) {
          modalErrorBanner.style.display = 'block';
          modalErrorBanner.textContent = data.error || 'Failed to fetch repository.';
        }
        if (submitModalBtn) submitModalBtn.disabled = false;
        if (modalLoadingBanner) modalLoadingBanner.style.display = 'none';
        return;
      }

      // Successful fetch -> redirect directly to the repository PR view
      if (data.redirect_url) {
        window.location.href = data.redirect_url;
      } else {
        window.location.reload();
      }

    } catch (err) {
      if (modalErrorBanner) {
        modalErrorBanner.style.display = 'block';
        modalErrorBanner.textContent = 'Network error: ' + err.message;
      }
      if (submitModalBtn) submitModalBtn.disabled = false;
      if (modalLoadingBanner) modalLoadingBanner.style.display = 'none';
    }
  }

  if (submitModalBtn) {
    submitModalBtn.addEventListener('click', handleModalFetch);
  }

  if (modalRepoInput) {
    modalRepoInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleModalFetch();
      }
    });
  }

  // Quick repository chips on Repos page
  document.querySelectorAll('.quick-repo-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      const repo = this.getAttribute('data-repo');
      if (repo) {
        openFetchModal(repo);
      }
    });
  });

  // Clear all repositories button
  const clearAllBtn = document.getElementById('clear-all-repos-btn');
  if (clearAllBtn) {
    clearAllBtn.addEventListener('click', async function () {
      if (!confirm('Are you sure you want to clear all fetched repositories and pull requests?')) return;
      try {
        await fetch('/api/clear-repos', { method: 'POST' });
        window.location.reload();
      } catch (err) {
        alert('Failed to clear: ' + err.message);
      }
    });
  }

  // Client-side repository search filter on repos.html
  if (repoSearchInput) {
    repoSearchInput.addEventListener('input', function () {
      const q = this.value.trim().toLowerCase();
      const cards = document.querySelectorAll('.gh-repo-card');
      let visibleCount = 0;

      cards.forEach(function (card) {
        const name = (card.getAttribute('data-name') || '').toLowerCase();
        const text = card.textContent.toLowerCase();
        const matches = !q || name.includes(q) || text.includes(q);
        card.style.display = matches ? 'flex' : 'none';
        if (matches) visibleCount++;
      });

      if (reposTotalBadge) {
        reposTotalBadge.textContent = visibleCount;
      }
    });
  }

  // ─── Refetch Repository PRs ──────────────────────────────
  async function handleRefetchRepo(owner, repo, btnElement) {
    if (!owner || !repo) return;
    const origHtml = btnElement ? btnElement.innerHTML : '';
    if (btnElement) {
      btnElement.disabled = true;
      btnElement.innerHTML = '<span class="loading-spinner" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:4px;"></span> Syncing…';
    }

    try {
      const resp = await fetch('/api/refetch-repo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ owner: owner, repo: repo, repo_name: repo }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.success) {
        alert(data.error || 'Failed to refetch repository PRs.');
        if (btnElement) {
          btnElement.disabled = false;
          btnElement.innerHTML = origHtml;
        }
        return;
      }
      window.location.reload();
    } catch (err) {
      alert('Network error: ' + err.message);
      if (btnElement) {
        btnElement.disabled = false;
        btnElement.innerHTML = origHtml;
      }
    }
  }

  const refetchBoardBtn = document.getElementById('refetch-board-btn');
  if (refetchBoardBtn) {
    refetchBoardBtn.addEventListener('click', function () {
      const owner = this.getAttribute('data-owner');
      const repo = this.getAttribute('data-repo');
      handleRefetchRepo(owner, repo, this);
    });
  }

  document.querySelectorAll('.refetch-repo-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      e.preventDefault();
      const owner = this.getAttribute('data-owner');
      const repo = this.getAttribute('data-repo');
      handleRefetchRepo(owner, repo, this);
    });
  });

  // ─── Discover Popular Repositories ───────────────────────
  async function handleDiscoverPopular(btn) {
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="loading-spinner" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:4px;"></span> Discovering…';
    }

    try {
      const resp = await fetch('/api/discover-popular', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: 6 }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.success) {
        alert(data.error || 'Failed to discover popular repositories.');
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = origHtml;
        }
        return;
      }
      window.location.reload();
    } catch (err) {
      alert('Network error: ' + err.message);
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }
    }
  }

  const discoverPopularBtn = document.getElementById('discover-popular-btn');
  if (discoverPopularBtn) {
    discoverPopularBtn.addEventListener('click', function () {
      handleDiscoverPopular(this);
    });
  }

  const emptyDiscoverPopularBtn = document.getElementById('empty-discover-popular-btn');
  if (emptyDiscoverPopularBtn) {
    emptyDiscoverPopularBtn.addEventListener('click', function () {
      handleDiscoverPopular(this);
    });
  }

  // ─── On-Demand Score for Older PRs ───────────────────────
  document.querySelectorAll('.run-model-btn').forEach(function (btn) {
    btn.addEventListener('click', async function () {
      const num = this.getAttribute('data-num');
      const owner = this.getAttribute('data-owner');
      const repoName = this.getAttribute('data-repo-name');
      const fullRepo = this.getAttribute('data-repo');
      if (!num) return;

      const actualOwner = owner || (fullRepo ? fullRepo.split('/')[0] : '');
      const actualRepo = repoName || (fullRepo ? fullRepo.split('/')[1] : '');

      const origText = this.textContent;
      this.disabled = true;
      this.textContent = 'Rating…';

      try {
        const resp = await fetch('/api/score-pr', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ owner: actualOwner, repo: actualRepo, pr_number: num }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
          alert(data.error || `Failed to score PR #${num}`);
          this.disabled = false;
          this.textContent = origText;
          return;
        }
        window.location.reload();
      } catch (err) {
        alert('Network error: ' + err.message);
        this.disabled = false;
        this.textContent = origText;
      }
    });
  });

  // Animate on load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', animateBars);
  } else {
    animateBars();
  }

})();
