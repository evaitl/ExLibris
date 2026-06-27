(function () {
  const DEBOUNCE_MS = 400;

  function isTyping() {
    const el = document.activeElement;
    if (!el) {
      return false;
    }
    const tag = el.tagName;
    return (
      tag === "INPUT" ||
      tag === "SELECT" ||
      tag === "TEXTAREA" ||
      el.isContentEditable
    );
  }

  const filterForm = document.getElementById("library-filter-form");
  const helpDialog = document.getElementById("keyboard-help");

  function submitFilterForm() {
    if (!filterForm) {
      return;
    }
    filterForm.submit();
  }

  if (filterForm) {
    let debounceTimer = null;

    filterForm.querySelectorAll("[data-filter-search]").forEach((input) => {
      input.addEventListener("input", () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(submitFilterForm, DEBOUNCE_MS);
      });
    });

    filterForm.querySelectorAll("[data-filter-auto]").forEach((control) => {
      control.addEventListener("change", submitFilterForm);
    });
  }

  document.querySelectorAll("[data-sort-dir]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = button.closest("form");
      if (!form) {
        return;
      }
      const input = form.querySelector('input[name="sort_dir"]');
      if (input) {
        input.value = button.dataset.sortDir;
      }
      form.submit();
    });
  });

  function openHelp() {
    if (helpDialog && typeof helpDialog.showModal === "function") {
      helpDialog.showModal();
    }
  }

  function closeHelp() {
    if (helpDialog && helpDialog.open) {
      helpDialog.close();
    }
  }

  document.querySelectorAll("[data-keyboard-help-open]").forEach((button) => {
    button.addEventListener("click", openHelp);
  });

  document.querySelectorAll("[data-keyboard-help-close]").forEach((button) => {
    button.addEventListener("click", closeHelp);
  });

  if (helpDialog) {
    helpDialog.addEventListener("click", (event) => {
      const rect = helpDialog.getBoundingClientRect();
      const inDialog =
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width;
      if (!inDialog) {
        closeHelp();
      }
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "?" && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if (!isTyping()) {
        event.preventDefault();
        if (helpDialog && helpDialog.open) {
          closeHelp();
        } else {
          openHelp();
        }
      }
      return;
    }

    if (event.key === "Escape") {
      if (helpDialog && helpDialog.open) {
        event.preventDefault();
        closeHelp();
        return;
      }
      if (!isTyping() && filterForm && filterForm.dataset.clearUrl) {
        event.preventDefault();
        window.location.href = filterForm.dataset.clearUrl;
      }
      return;
    }

    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if (!isTyping()) {
        event.preventDefault();
        const titleSearch = document.getElementById("search-title");
        if (titleSearch) {
          titleSearch.focus();
          titleSearch.select();
        }
      }
      return;
    }

    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      if (isTyping()) {
        return;
      }

      const nav = document.querySelector("[data-page-nav]");
      if (!nav) {
        return;
      }

      const url =
        event.key === "ArrowRight"
          ? nav.getAttribute("data-next-url")
          : nav.getAttribute("data-prev-url");
      if (!url) {
        return;
      }

      event.preventDefault();
      window.location.href = url;
    }
  });
})();
