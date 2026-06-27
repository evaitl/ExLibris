(function () {
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

  document.addEventListener("keydown", function (event) {
    if (event.key !== "PageUp" && event.key !== "PageDown") {
      return;
    }
    if (isTyping()) {
      return;
    }

    const nav = document.querySelector("[data-page-nav]");
    if (!nav) {
      return;
    }

    const url =
      event.key === "PageDown"
        ? nav.getAttribute("data-next-url")
        : nav.getAttribute("data-prev-url");
    if (!url) {
      return;
    }

    event.preventDefault();
    window.location.href = url;
  });
})();
