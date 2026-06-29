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
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    if (isTyping()) {
      return;
    }

    const url =
      event.key === "ArrowRight"
        ? document.body.dataset.bookNextUrl
        : document.body.dataset.bookPrevUrl;
    if (!url) {
      return;
    }

    event.preventDefault();
    window.location.href = url;
  });
})();
