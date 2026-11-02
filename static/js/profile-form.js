/* Settings > Account > Profile. Two small Alpine components:

   profileForm  - the profile form, its live "How housemates see you" preview
                  and the unsaved-changes bar. Nothing is saved until the form
                  is submitted (an ordinary POST, so it also works without
                  this script); this keeps track of what changed, checks the
                  display name as it is typed, previews a chosen photo and warns
                  before leaving with changes unsaved.
   shareToggle  - the "Share my activity" switch, which saves the moment it is
                  flipped and puts itself back if the save fails. */
(function () {
  const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

  /* The same rule as the tracker_extras.on_color filter: dark ink on a light
     fill, white on a dark one. */
  function inkFor(hex) {
    const value = String(hex || '').replace('#', '');
    if (value.length !== 6) return '#fff';
    const channel = (i) => {
      const c = parseInt(value.slice(i, i + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    const luminance = 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4);
    return luminance <= 0.19 ? '#fff' : '#1a1305';
  }

  window.profileForm = function (cfg) {
    return {
      name: cfg.name,
      bio: cfg.bio,
      color: cfg.color,
      photoUrl: cfg.photoUrl || '',
      pendingFile: null,
      removePhoto: false,
      nameState: 'idle', // idle | checking | ok | taken
      nameMessage: '',
      photoError: '',
      dragging: false,
      submitting: false,
      _timer: null,
      _seq: 0,

      get initial() { return (this.name.trim().charAt(0) || '?').toUpperCase(); },
      get ink() { return inkFor(this.color); },
      get bioCount() { return this.bio.length; },
      get bioOver() { return this.bio.length > cfg.bioMax; },
      get hasPhoto() { return !!this.photoUrl; },
      get dirty() {
        return this.name.trim() !== cfg.name || this.bio.trim() !== cfg.bio.trim() || this.color !== cfg.color
          || !!this.pendingFile || this.removePhoto;
      },
      get canSave() {
        return this.dirty && !this.submitting && this.name.trim() !== '' && this.name.trim().length <= cfg.nameMax
          && !this.bioOver && this.nameState !== 'taken';
      },

      init() {
        this.$watch('name', () => this.scheduleNameCheck());
        window.addEventListener('beforeunload', (event) => {
          if (this.dirty && !this.submitting) {
            event.preventDefault();
            event.returnValue = '';
          }
        });
        this.checkName();
      },

      /* ---- display name: unique on this server, checked as it is typed ---- */
      scheduleNameCheck() {
        clearTimeout(this._timer);
        if (this.name.trim() === '') {
          this.nameState = 'taken';
          this.nameMessage = 'Enter a display name.';
          return;
        }
        this.nameState = 'checking';
        this._timer = setTimeout(() => this.checkName(), 300);
      },
      async checkName() {
        const name = this.name.trim();
        if (name === '') return;
        const seq = ++this._seq;
        try {
          const response = await fetch(`${cfg.nameCheckUrl}?name=${encodeURIComponent(name)}`, {
            credentials: 'same-origin',
            headers: { Accept: 'application/json' },
          });
          if (!response.ok) throw new Error('check failed');
          const data = await response.json();
          if (seq !== this._seq) return; // a newer keystroke already asked
          this.nameState = data.available ? 'ok' : 'taken';
          this.nameMessage = data.message;
        } catch (error) {
          // The check is a convenience - the server decides on save.
          if (seq === this._seq) { this.nameState = 'idle'; this.nameMessage = ''; }
        }
      },

      /* ---- photo ---- */
      pickPhoto() { this.$refs.file.click(); },
      onFileInput(event) { this.useFile(event.target.files[0] || null); },
      onDrop(event) {
        this.dragging = false;
        const file = event.dataTransfer && event.dataTransfer.files[0];
        if (!file) return;
        if (this.useFile(file)) {
          const transfer = new DataTransfer();
          transfer.items.add(file);
          this.$refs.file.files = transfer.files; // so the form posts the dropped file
        }
      },
      useFile(file) {
        this.photoError = '';
        if (!file) return false;
        if (!PHOTO_TYPES.includes(file.type)) {
          this.photoError = 'That has to be a JPG, PNG or WebP image.';
          this.$refs.file.value = '';
          return false;
        }
        if (file.size > cfg.maxBytes) {
          this.photoError = 'That image is over 5 MB - choose a smaller one.';
          this.$refs.file.value = '';
          return false;
        }
        this.releasePreview();
        this.pendingFile = file;
        this.photoUrl = URL.createObjectURL(file);
        this.removePhoto = false;
        return true;
      },
      removeCurrentPhoto() {
        this.photoError = '';
        this.releasePreview();
        this.$refs.file.value = '';
        this.pendingFile = null;
        this.photoUrl = '';
        this.removePhoto = !!cfg.photoUrl; // nothing to delete on the server if there was none
      },
      releasePreview() {
        if (this.pendingFile && this.photoUrl.startsWith('blob:')) URL.revokeObjectURL(this.photoUrl);
      },

      /* ---- the unsaved-changes bar ---- */
      discard() {
        this.releasePreview();
        this.name = cfg.name;
        this.bio = cfg.bio;
        this.color = cfg.color;
        this.photoUrl = cfg.photoUrl || '';
        this.pendingFile = null;
        this.removePhoto = false;
        this.photoError = '';
        this.$refs.file.value = '';
        this.nameState = 'ok';
        this.nameMessage = 'Available';
      },
      onSubmit(event) {
        if (!this.canSave) { event.preventDefault(); return; }
        this.submitting = true;
      },
    };
  };

  window.shareToggle = function (cfg) {
    return {
      on: cfg.on,
      busy: false,
      status: 'idle', // idle | saved | error
      async save() {
        // x-model has already flipped `on`; keep that and undo it if the save fails.
        const wanted = this.on;
        this.busy = true;
        this.status = 'idle';
        try {
          const body = new FormData();
          if (wanted) body.append('share_activity', 'on');
          const response = await fetch(cfg.url, {
            method: 'POST',
            body,
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': cfg.csrf },
          });
          if (!response.ok) throw new Error('save failed');
          this.status = 'saved';
        } catch (error) {
          this.on = !wanted;
          this.status = 'error';
        } finally {
          this.busy = false;
        }
      },
    };
  };
})();
