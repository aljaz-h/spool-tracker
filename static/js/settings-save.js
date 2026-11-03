/* Settings: toggles, selects and chips save the moment they change (an htmx
   form with hx-trigger="change"). This puts a "Saved" next to the control when
   that save goes through, or says it didn't, so the page never saves silently. */
(function () {
  function statusFor(form) {
    // A form with many controls (Notifications) names one place for it; otherwise
    // it goes beside the control's row.
    const named = form.querySelector('[data-save-status]');
    if (named) { named.classList.add('s-saved'); return named; }
    const holder = form.querySelector('.s-row-control') || form;
    let status = holder.querySelector(':scope > .s-saved');
    if (!status) {
      status = document.createElement('span');
      status.className = 's-saved';
      status.setAttribute('role', 'status');
      holder.insertBefore(status, holder.firstChild);
    }
    return status;
  }

  document.addEventListener('htmx:afterRequest', (event) => {
    const form = event.detail && event.detail.elt;
    if (!form || !form.matches || !form.matches('form[hx-post]') || !form.closest('[data-settings]')) return;
    if (!(form.getAttribute('hx-trigger') || '').includes('change')) return;
    const status = statusFor(form);
    clearTimeout(status._timer);
    if (event.detail.successful) {
      status.classList.remove('is-error');
      status.textContent = '✓ Saved';
    } else {
      status.classList.add('is-error');
      status.textContent = "Couldn't save - try again";
    }
    status._timer = setTimeout(() => { status.textContent = ''; }, 2500);
  });
})();
