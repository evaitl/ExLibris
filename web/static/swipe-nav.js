(function () {
  const MIN_DISTANCE = 60;

  function isInteractiveTarget(target) {
    if (!target || !target.closest) {
      return false;
    }
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
      return true;
    }
    if (target.isContentEditable) {
      return true;
    }
    return Boolean(target.closest("dialog, button, label"));
  }

  function navigationUrls() {
    const pageNav = document.querySelector("[data-page-nav]");
    if (pageNav) {
      return {
        prev: pageNav.getAttribute("data-prev-url") || "",
        next: pageNav.getAttribute("data-next-url") || "",
      };
    }
    return {
      prev: document.body.dataset.bookPrevUrl || "",
      next: document.body.dataset.bookNextUrl || "",
    };
  }

  let startX = 0;
  let startY = 0;
  let tracking = false;
  let suppressClick = false;

  document.addEventListener(
    "click",
    function (event) {
      if (suppressClick) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true
  );

  document.addEventListener(
    "touchstart",
    function (event) {
      if (event.touches.length !== 1) {
        tracking = false;
        return;
      }
      if (isInteractiveTarget(event.target)) {
        tracking = false;
        return;
      }
      startX = event.touches[0].clientX;
      startY = event.touches[0].clientY;
      tracking = true;
    },
    { passive: true }
  );

  document.addEventListener(
    "touchend",
    function (event) {
      if (!tracking || event.changedTouches.length !== 1) {
        tracking = false;
        return;
      }
      tracking = false;

      const dx = event.changedTouches[0].clientX - startX;
      const dy = event.changedTouches[0].clientY - startY;
      if (Math.abs(dx) < MIN_DISTANCE) {
        return;
      }
      if (Math.abs(dx) <= Math.abs(dy)) {
        return;
      }

      const urls = navigationUrls();
      const url = dx < 0 ? urls.next : urls.prev;
      if (!url) {
        return;
      }

      suppressClick = true;
      window.setTimeout(function () {
        suppressClick = false;
      }, 500);
      window.location.href = url;
    },
    { passive: true }
  );

  document.addEventListener(
    "touchcancel",
    function () {
      tracking = false;
    },
    { passive: true }
  );
})();
