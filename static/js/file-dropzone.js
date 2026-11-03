/* Settings' file upload (partials/file_dropzone.html): choose a file or drop one
   on the area, see its name and size, remove it, then upload. The real file
   input stays hidden and is what the form posts. */
(function () {
  function formatSize(bytes) {
    if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }

  window.fileDropzone = function (cfg) {
    const accepted = String(cfg.accept || '').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
    return {
      file: null,
      error: '',
      dragging: false,
      title: cfg.title,
      get sizeLabel() { return this.file ? formatSize(this.file.size) : ''; },

      choose() { this.$refs.input.click(); },
      onInput(event) { this.use(event.target.files[0] || null); },
      onDrop(event) {
        this.dragging = false;
        const file = event.dataTransfer && event.dataTransfer.files[0];
        if (file && this.use(file)) {
          const transfer = new DataTransfer();
          transfer.items.add(file);
          this.$refs.input.files = transfer.files; // so the form posts the dropped file
        }
      },
      use(file) {
        this.error = '';
        if (!file) return false;
        const name = file.name.toLowerCase();
        if (accepted.length && !accepted.some((ext) => name.endsWith(ext))) {
          this.error = 'That file has to be ' + accepted.join(', ') + '.';
          this.$refs.input.value = '';
          return false;
        }
        if (file.size > cfg.maxMb * 1024 * 1024) {
          this.error = 'That file is over ' + cfg.maxMb + ' MB.';
          this.$refs.input.value = '';
          return false;
        }
        this.file = file;
        return true;
      },
      remove() {
        this.file = null;
        this.error = '';
        this.$refs.input.value = '';
      },
    };
  };
})();
