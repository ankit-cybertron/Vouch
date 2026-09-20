/**
 * Vouch — Smart Repository Autocomplete Component
 * Provides live typeahead suggestions for repository search inputs.
 */
(function () {
  'use strict';

  function initSmartRepoAutocomplete(inputEl, options = {}) {
    if (!inputEl) return;

    const targetUrlTemplate = options.targetUrlTemplate || '/repo/{owner}/{repo}';
    let activeIndex = -1;
    let currentResults = [];
    let debounceTimer = null;

    // Create dropdown element
    const dropdown = document.createElement('div');
    dropdown.className = 'smart-repo-dropdown';
    dropdown.setAttribute('role', 'listbox');
    dropdown.style.display = 'none';

    // Position container
    const parent = inputEl.parentElement;
    if (parent && getComputedStyle(parent).position === 'static') {
      parent.style.position = 'relative';
    }
    parent.appendChild(dropdown);

    function closeDropdown() {
      dropdown.style.display = 'none';
      dropdown.innerHTML = '';
      activeIndex = -1;
      currentResults = [];
    }

    function renderResults(results, query) {
      currentResults = results;
      activeIndex = -1;
      dropdown.innerHTML = '';

      if (!results || results.length === 0) {
        dropdown.innerHTML = `
          <div class="smart-repo-empty">
            No matching fetched repositories. Press Enter to search on GitHub.
          </div>
        `;
        dropdown.style.display = 'block';
        return;
      }

      results.forEach((r, idx) => {
        const item = document.createElement('div');
        item.className = 'smart-repo-item';
        item.setAttribute('role', 'option');
        item.setAttribute('data-index', idx);

        // Highlight matched part
        const fullName = r.full_name || `${r.owner}/${r.repo}`;
        let displayName = fullName;
        if (query) {
          const qEscaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          const regex = new RegExp(`(${qEscaped})`, 'gi');
          displayName = fullName.replace(regex, '<strong>$1</strong>');
        }

        item.innerHTML = `
          <div class="smart-repo-main">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" class="smart-repo-icon">
              <path d="M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 11.5Zm10.5-1h-8a1 1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8ZM5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 .25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3 0L5.4 15.7a.25.25 0 0 1-.4-.2Z"></path>
            </svg>
            <span class="smart-repo-name">${displayName}</span>
          </div>
          <div class="smart-repo-meta">
            ${r.language ? `<span class="smart-repo-lang"><span class="smart-repo-dot" style="background:${r.language_color || '#586069'}"></span>${r.language}</span>` : ''}
            ${r.stars && r.stars !== '—' ? `<span class="smart-repo-stars">★ ${r.stars}</span>` : ''}
          </div>
        `;

        item.addEventListener('mousedown', (e) => {
          e.preventDefault();
          selectResult(r);
        });

        item.addEventListener('mouseenter', () => {
          setActiveItem(idx);
        });

        dropdown.appendChild(item);
      });

      dropdown.style.display = 'block';
    }

    function setActiveItem(idx) {
      const items = dropdown.querySelectorAll('.smart-repo-item');
      items.forEach((it, i) => {
        if (i === idx) {
          it.classList.add('active');
          it.scrollIntoView({ block: 'nearest' });
        } else {
          it.classList.remove('active');
        }
      });
      activeIndex = idx;
    }

    function selectResult(r) {
      closeDropdown();
      const owner = r.owner || (r.full_name ? r.full_name.split('/')[0] : '');
      const repo = r.repo || (r.full_name ? r.full_name.split('/')[1] : '');
      const fullName = r.full_name || `${owner}/${repo}`;
      sessionStorage.setItem('vouch_repos_view', 'visible');
      sessionStorage.setItem('vouch_active_repo', fullName);
      const url = targetUrlTemplate
        .replace('{owner}', encodeURIComponent(owner))
        .replace('{repo}', encodeURIComponent(repo))
        .replace('{full_name}', encodeURIComponent(fullName));
      window.location.href = url;
    }

    async function fetchSuggestions(query) {
      try {
        const resp = await fetch(`/api/repos/search?q=${encodeURIComponent(query)}`);
        if (!resp.ok) return;
        const data = await resp.json();
        renderResults(data.results || [], query);
      } catch (err) {
        // Silently fail on network disconnect
      }
    }

    // Input listeners
    inputEl.addEventListener('input', function () {
      const val = this.value.trim();
      clearTimeout(debounceTimer);
      if (!val) {
        // Show recent/top repos on empty input
        debounceTimer = setTimeout(() => fetchSuggestions(''), 100);
        return;
      }
      debounceTimer = setTimeout(() => fetchSuggestions(val), 150);
    });

    inputEl.addEventListener('focus', function () {
      const val = this.value.trim();
      fetchSuggestions(val);
    });

    inputEl.addEventListener('keydown', function (e) {
      const items = dropdown.querySelectorAll('.smart-repo-item');
      if (dropdown.style.display === 'block' && items.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          const next = activeIndex + 1 >= items.length ? 0 : activeIndex + 1;
          setActiveItem(next);
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          const prev = activeIndex - 1 < 0 ? items.length - 1 : activeIndex - 1;
          setActiveItem(prev);
          return;
        }
        if (e.key === 'Enter') {
          if (activeIndex >= 0 && activeIndex < currentResults.length) {
            e.preventDefault();
            selectResult(currentResults[activeIndex]);
            return;
          }
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          closeDropdown();
          return;
        }
      }

      // If user presses enter on input and it has "owner/repo" format
      if (e.key === 'Enter') {
        const val = this.value.trim();
        if (val.includes('/')) {
          e.preventDefault();
          const parts = val.split('/');
          const owner = parts[0].trim();
          const repo = parts[1].trim();
          if (owner && repo) {
            selectResult({ owner, repo, full_name: `${owner}/${repo}` });
          }
        }
      }
    });

    // Close when clicking outside
    document.addEventListener('click', function (e) {
      if (!parent.contains(e.target)) {
        closeDropdown();
      }
    });
  }

  window.initSmartRepoAutocomplete = initSmartRepoAutocomplete;

  // Auto-initialize any input with data-smart-repo
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-smart-repo]').forEach((el) => {
      const tmpl = el.getAttribute('data-target-template') || '/repo/{owner}/{repo}';
      initSmartRepoAutocomplete(el, { targetUrlTemplate: tmpl });
    });
  });
})();
