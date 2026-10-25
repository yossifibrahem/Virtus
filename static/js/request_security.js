// Apply session-bound request protection to JSON, uploads, and streamed requests.
(() => {
  const nativeFetch = window.fetch.bind(window);
  const token = document.querySelector('meta[name="lumen-csrf"]')?.content || '';
  window.fetch = (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    if (url.origin === location.origin && url.pathname.startsWith('/api/')) {
      const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
      headers.set('X-Lumen-CSRF', token);
      init = { ...init, headers };
    }
    return nativeFetch(input, init);
  };
})();
