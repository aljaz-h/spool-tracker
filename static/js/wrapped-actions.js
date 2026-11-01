/* Wrapped's image buttons: one click to download a recap as a PNG, or to copy
   it so it can be pasted straight into a chat or a post. If the image hasn't
   been made yet it is made first (one request), then used; nothing here
   reloads the page. Copying needs the browser's image clipboard, which only
   exists on https pages and on localhost - elsewhere the Copy buttons hide
   and Download is all there is. */
(function () {
  const canCopy = !!(window.isSecureContext && navigator.clipboard && window.ClipboardItem);

  document.querySelectorAll('[data-act="copy"], [data-copy-url]').forEach((el) => { if (!canCopy) el.hidden = true; });

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
      if (button.dataset.copyUrl) image = { url: button.dataset.copyUrl };
      else {
        if (needsMaking) flash(button, 'Making image…', 60000);
        image = await ensureImage(group);
      }
      if (act === 'copy') {
        await copyImage(image.url);
        flash(button, 'Copied ✓');
      } else {
        download(image.url, image.name);
        flash(button, 'Downloading…', 1800);
      }
    } catch (error) {
      flash(button, act === 'copy' ? "Couldn't copy" : "Couldn't make it", 3000);
      console.warn('Wrapped image action failed:', error);
    } finally {
      button.disabled = false;
    }
  });
})();
