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
      visible = classes.includes('tree-role-user') || (classes.includes('tree-role-assistant') && text.includes('\u2705'));
    } else if (activeFilter === 'default') {
      visible = !classes.includes('tree-role-system') && !classes.includes('tree-role-thinking');
    }

    if (visible && search) {
      visible = text.includes(search);
    }

    node.style.display = visible ? '' : 'none';
  });
}

// Smooth scroll to target on sidebar click
document.querySelectorAll('.tree-node').forEach(node => {
  node.addEventListener('click', function(e) {
    e.preventDefault();
    const id = this.getAttribute('href')?.slice(1);
    if (id) {
      const el = document.getElementById(id);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.style.outline = '2px solid var(--accent)';
        setTimeout(() => el.style.outline = '', 2000);
      }
    }
    document.querySelectorAll('.tree-node').forEach(n => n.classList.remove('active'));
    this.classList.add('active');
  });
});

// Apply default filter on load
applyFilters();
