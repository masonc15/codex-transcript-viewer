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

// Apply default filter on load
applyFilters();
