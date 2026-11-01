/* Wrapped's image buttons: one click to download a recap as a PNG, or to copy
   it so it can be pasted straight into a chat or a post. If the image hasn't
   been made yet it is made first (one request), then used; nothing here
   reloads the page. Copying needs the browser's image clipboard, which only
   exists on https pages and on localhost - elsewhere the Copy buttons hide
   and Download is all there is. */
(function () {
  const canCopy = !!(window.isSecureContext && navigator.clipboard && window.ClipboardItem);

  function hideCopyIfUnsupported() {
    document.querySelectorAll('[data-act="copy"], [data-copy-url]').forEach((el) => { if (!canCopy) el.hidden = true; });
  }
  hideCopyIfUnsupported();

  /* A recap was just saved: bring the page up to date without reloading it -
     the buttons (now with Re-create and the image's address), the hint under
     the preview and the Saved recaps list are swapped for the server's. */
  async function refreshSaved() {
    try {
      const response = await fetch(window.location.href, { credentials: 'same-origin', headers: { Accept: 'text/html' } });
      if (!response.ok) return;
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      for (const selector of ['[data-wrapped-actions]', '.wp-fine', '.wp-saved']) {
        const fresh = doc.querySelector(selector);
        const current = document.querySelector(selector);
        if (fresh && current) current.replaceWith(fresh);
        else if (fresh && selector === '.wp-saved') document.querySelector('.wp-stage').after(fresh);
      }
      hideCopyIfUnsupported();
    } catch (error) {
      console.warn('Wrapped page refresh failed:', error);
    }
  }

  function flash(button, text, ms) {
    const label = button.querySelector('span') || button;
    const original = button.dataset.label || label.textContent;
    button.dataset.label = original;
    label.textContent = text;
    clearTimeout(button._t);
    button._t = setTimeout(() => { label.textContent = original; }, ms || 2200);
  }

  async function copyImage(url) {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error('image not found');
    const blob = await response.blob();
    // The clipboard only takes PNG for images, which is what this is.
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob.type === 'image/png' ? blob : new Blob([blob], { type: 'image/png' }) })]);
  }

  function download(url, name) {
    const a = document.createElement('a');
    a.href = url;
    a.download = name || 'spool-wrapped.png';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  /* Makes the image for the page's current period and options if the
     group doesn't have one yet; returns {url, name}. */
  async function ensureImage(group) {
    if (group.dataset.url) return { url: group.dataset.url, name: group.dataset.name };
    const form = group.querySelector('form[data-create]');
    const response = await fetch(form.action, {
      method: 'POST',
      body: new FormData(form),
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.message || "Couldn't make the image.");
    group.dataset.url = data.url;
    group.dataset.name = data.name;
    return data;
  }

  /* The year and month pickers: choosing an option opens that page. The option
     values are Spool's own addresses; anything that isn't on this site is ignored. */
  document.addEventListener('change', (event) => {
    const select = event.target.closest('select[data-wp-nav]');
    if (!select) return;
    let target;
    try { target = new URL(select.value, window.location.href); } catch (error) { return; }
    if (target.origin === window.location.origin) window.location.assign(target.pathname + target.search);
  });

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act], [data-copy-url]');
    if (!button) return;
    event.preventDefault();
    const group = button.closest('[data-wrapped-actions]');
    const act = button.dataset.act || 'copy';
    const needsMaking = group && !group.dataset.url;
    if (button.disabled) return;
    button.disabled = true;
    try {
      let image;
      let justSaved = false;
      if (button.dataset.copyUrl) image = { url: button.dataset.copyUrl };
      else {
        if (needsMaking) flash(button, 'Making image…', 60000);
        image = await ensureImage(group);
        justSaved = needsMaking;
      }
      if (act === 'copy') {
        await copyImage(image.url);
        flash(button, justSaved ? 'Copied and saved ✓' : 'Copied ✓');
      } else {
        download(image.url, image.name);
        flash(button, justSaved ? 'Saved ✓' : 'Downloading…', 1800);
      }
      // Making the image saves it as a recap: update the page so it shows
      // up under "Saved recaps" right away.
      if (justSaved) setTimeout(refreshSaved, 1800);
    } catch (error) {
      flash(button, act === 'copy' ? "Couldn't copy" : "Couldn't make it", 3000);
      console.warn('Wrapped image action failed:', error);
    } finally {
      button.disabled = false;
    }
  });
})();
