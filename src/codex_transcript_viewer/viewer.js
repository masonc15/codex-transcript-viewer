// Sidebar filtering and navigation for Codex session transcript viewer.

const allNodes = document.querySelectorAll('.tree-node');
let activeFilter = 'default';

function setFilter(filter, btn) {
  activeFilter = filter;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  applyFilters();
}

function filterTree(search) {
  applyFilters(search);
}

function applyFilters(search) {
  search = (search || document.getElementById('tree-search').value).toLowerCase();
  allNodes.forEach(node => {
    const text = node.textContent.toLowerCase();
    const classes = node.className;
    let visible = true;

    if (activeFilter === 'no-tools') {
      visible = !classes.includes('tree-role-tool') && !classes.includes('tree-role-system');
    } else if (activeFilter === 'user-only') {
      visible = classes.includes('tree-role-user');
    } else if (activeFilter === 'answers') {
      visible = node.dataset.kind === 'user' || node.dataset.kind === 'final-answer';
    } else if (activeFilter === 'default') {
      visible = !classes.includes('tree-role-system') && !classes.includes('tree-role-thinking');
    }

    if (visible && search) {
      visible = text.includes(search);
    }

    node.style.display = visible ? '' : 'none';
  });
}

const narrowScreen = window.matchMedia('(max-width: 900px)');

function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-overlay').classList.remove('open');
}

// Smooth scroll to target on sidebar click
document.querySelectorAll('.tree-node').forEach(node => {
  node.addEventListener('click', function(e) {
    e.preventDefault();
    const id = this.getAttribute('href')?.slice(1);
    if (id) {
      const el = document.getElementById(id);
      if (el) {
        // On a phone the drawer covers the transcript, so get it out of the way first.
        if (narrowScreen.matches) closeSidebar();
        const folded = el.tagName === 'DETAILS' ? el : el.closest('details');
        if (folded) folded.open = true;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.style.outline = '2px solid var(--accent)';
        setTimeout(() => el.style.outline = '', 2000);
      }
    }
    document.querySelectorAll('.tree-node').forEach(n => n.classList.remove('active'));
    this.classList.add('active');
  });
});

function setExpanded(output, expanded) {
  output.classList.toggle('expanded', expanded);
  const button = output.querySelector('.output-toggle');
  button.setAttribute('aria-expanded', String(expanded));
  button.textContent = expanded ? button.dataset.less : button.dataset.more;
}

function toggleImage(img) {
  img.setAttribute('aria-pressed', String(img.classList.toggle('full')));
}

document.getElementById('messages').addEventListener('click', e => {
  const button = e.target.closest('.output-toggle');
  if (button) {
    const output = button.closest('.tool-output');
    setExpanded(output, !output.classList.contains('expanded'));
    return;
  }
  // Clicking a long output's preview expands it, unless the click ended a text selection.
  const preview = e.target.closest('.output-preview');
  if (preview && !String(window.getSelection())) {
    setExpanded(preview.closest('.tool-output'), true);
    return;
  }
  const img = e.target.closest('.attachment-image img');
  if (img) toggleImage(img);
});

document.getElementById('messages').addEventListener('keydown', e => {
  const img = e.target.closest?.('.attachment-image img');
  if (img && (e.key === 'Enter' || e.key === ' ')) {
    e.preventDefault();
    toggleImage(img);
  }
});

// Apply default filter on load
applyFilters();
