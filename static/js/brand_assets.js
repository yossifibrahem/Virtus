// Keep browser icons in sync with the effective app theme, including Auto.
(() => {
  const root = document.documentElement;
  const icons = document.querySelectorAll('link[data-logo-dark][data-logo-light]');
  function updateIcons() {
    const theme = root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    icons.forEach(icon => {
      icon.href = theme === 'light' ? icon.dataset.logoLight : icon.dataset.logoDark;
    });
  }
  updateIcons();
  new MutationObserver(updateIcons).observe(root, {
    attributes: true,
    attributeFilter: ['data-theme'],
  });
})();
