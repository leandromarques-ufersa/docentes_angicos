(() => {
  const script = document.currentScript;
  const currentVersion = script.dataset.version;
  const endpoint = new URL('version.json', script.src);
  let busy = false;
  let announced = false;

  async function check() {
    if (busy || announced || document.hidden) return;
    busy = true;
    try {
      const url = new URL(endpoint);
      url.searchParams.set('_', Date.now());
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) return;
      const latest = await response.json();
      if (!/^[a-f0-9]{20}$/.test(latest.version)) return;
      const freshness = document.getElementById('sync-freshness');
      if (freshness && latest.checked_at) {
        const checked = new Date(latest.checked_at);
        if (!Number.isNaN(checked.getTime())) {
          const formatted = new Intl.DateTimeFormat('pt-BR', {
            timeZone: 'America/Fortaleza', dateStyle: 'short', timeStyle: 'short'
          }).format(checked);
          freshness.textContent = `Listas verificadas em ${formatted} (Fortaleza).`;
          if (latest.pending_removals) freshness.textContent += ' Algumas ausências aguardam confirmação.';
          if (Date.now() - checked.getTime() > 3 * 86400000) {
            freshness.textContent += ' A checagem está atrasada; consulte também o SIGAA.';
          }
        }
      }
      if (latest.version === currentVersion) return;
      announced = true;
      const banner = document.createElement('aside');
      banner.className = 'update-notice';
      banner.setAttribute('role', 'status');
      const message = document.createElement('span');
      message.textContent = 'Há uma nova versão desta página disponível.';
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = 'Atualizar página';
      button.addEventListener('click', () => {
        const target = new URL(location.href);
        target.searchParams.set('v', latest.version);
        location.replace(target.href);
      });
      banner.append(message, button);
      document.querySelector('main').before(banner);
    } catch {
      // Keep the page usable when offline or while a deployment propagates.
    } finally {
      busy = false;
    }
  }

  check();
  setInterval(check, 5 * 60 * 1000);
  document.addEventListener('visibilitychange', check);
  window.addEventListener('focus', check);
})();
