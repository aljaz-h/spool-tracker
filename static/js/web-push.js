/* Settings > Notifications > "Notifications on this device": turns browser /
   installed-app push on and off for this one device. The server half is
   tracker/webpush_views.py; the service worker (templates/sw.js) shows the
   notification when one arrives.

   Everything works without this script except this card, which just stays on
   "Checking..." - the Push tick-boxes and the Apprise block above and below
   are plain forms. */
(function () {
  const root = document.querySelector('[data-webpush]');
  if (!root) return;

  const $ = (sel) => root.querySelector(sel);
  const statusEl = $('[data-wp-status]');
  const enableBtn = $('[data-wp-enable]');
  const disableBtn = $('[data-wp-disable]');
  const testBtn = $('[data-wp-test]');
  const hintEl = $('[data-wp-hint]');
  const hintText = $('[data-wp-hint-text]');
  const url = (name) => root.dataset[name];

  const ua = navigator.userAgent || '';
  const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

  function csrf() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  function post(endpoint, body) {
    return fetch(endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
      body: JSON.stringify(body || {}),
    }).then((r) => r.json().catch(() => ({ ok: false })).then((data) => ({ status: r.status, data })));
  }

  function show(state) {
    statusEl.textContent = state.text;
    enableBtn.hidden = !state.enable;
    disableBtn.hidden = !state.disable;
    testBtn.hidden = !state.test;
    enableBtn.disabled = !!state.busy;
    disableBtn.disabled = !!state.busy;
    testBtn.disabled = !!state.busy;
    hintEl.hidden = !state.hint;
    hintText.textContent = state.hint || '';
  }

  function keyBytes(b64url) {
    const pad = '='.repeat((4 - (b64url.length % 4)) % 4);
    const raw = atob((b64url + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  function refreshDevices() {
    if (window.htmx) window.htmx.ajax('GET', url('devicesUrl'), { target: '#push-devices', swap: 'innerHTML' });
  }

  async function currentSubscription() {
    const reg = await navigator.serviceWorker.ready;
    return reg.pushManager.getSubscription();
  }

  async function refresh() {
    if (!supported) {
      show({
        text: 'Not available in this browser.',
        hint: isIOS && !standalone
          ? 'On an iPhone or iPad, tap Share, then Add to Home Screen, and open Spool from the Home Screen. The option appears there.'
          : 'This browser can\'t receive push notifications. Try a recent Chrome, Edge, Firefox or Safari.',
      });
      return;
    }
    if (Notification.permission === 'denied') {
      show({
        text: 'Blocked for this site.',
        hint: 'Notifications are blocked in this browser. Allow them for this site in the browser\'s site settings, then reload this page.',
      });
      return;
    }
    let subscribed = false;
    try {
      const sub = await currentSubscription();
      if (sub) subscribed = (await post(url('statusUrl'), { endpoint: sub.endpoint })).data.subscribed === true;
    } catch (e) { /* treated as off */ }
    show(subscribed
      ? { text: 'On for this device.', disable: true, test: true }
      : { text: 'Off for this device.', enable: true });
  }

  function fail(text) {
    show({ text, enable: true });
  }

  function enable() {
    // Asking for permission has to start inside the click, before any await,
    // or Safari treats it as not user-initiated.
    const asked = Notification.permission === 'default' ? Notification.requestPermission() : Promise.resolve(Notification.permission);
    show({ text: 'Turning on…', busy: true });
    asked.then(async (permission) => {
      if (permission !== 'granted') {
        refresh();
        return;
      }
      try {
        const keyRes = await fetch(url('keyUrl'), { credentials: 'same-origin' });
        const { key } = await keyRes.json();
        const reg = await navigator.serviceWorker.ready;
        let sub = await reg.pushManager.getSubscription();
        if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
        const json = sub.toJSON();
        const res = await post(url('subscribeUrl'), { endpoint: json.endpoint, keys: json.keys });
        if (!res.data.ok) {
          fail(res.data.error || 'Couldn\'t turn notifications on.');
          return;
        }
        refreshDevices();
        refresh();
      } catch (e) {
        fail('Couldn\'t turn notifications on. Check your connection and try again.');
      }
    });
  }

  async function disable() {
    show({ text: 'Turning off…', busy: true });
    try {
      const sub = await currentSubscription();
      if (sub) {
        await post(url('unsubscribeUrl'), { endpoint: sub.endpoint });
        await sub.unsubscribe();
      }
    } catch (e) { /* the server row is gone either way or is cleaned up on the next send */ }
    refreshDevices();
    refresh();
  }

  async function sendTest() {
    show({ text: 'Sending a test…', busy: true, disable: true, test: true });
    try {
      const res = await post(url('testUrl'));
      await refresh();
      statusEl.textContent = res.data.ok && res.data.sent
        ? 'Test sent. It should appear in a moment.'
        : (res.data.error || 'The test couldn\'t be delivered. Try turning notifications off and on again.');
    } catch (e) {
      await refresh();
      statusEl.textContent = 'The test couldn\'t be sent. Check your connection.';
    }
  }

  enableBtn.addEventListener('click', enable);
  disableBtn.addEventListener('click', disable);
  testBtn.addEventListener('click', sendTest);
  // Removing a device from the list can remove this one.
  document.body.addEventListener('htmx:afterSwap', (e) => {
    if (e.detail && e.detail.target && e.detail.target.id === 'push-devices') refresh();
  });

  refresh();
})();
