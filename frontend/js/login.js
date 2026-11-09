(function () {
  const view = document.getElementById("login-view");
  const form = document.getElementById("login-form");
  const email = document.getElementById("login-email");
  const error = document.getElementById("login-error");
  const button = document.getElementById("login-submit");
  const label = button && button.querySelector(".lg-submit-label");
  if (!view || !form || !email || !error || !button || !label) return;
  button.dataset.idleLabel = label.textContent.trim() || "Continue";

  function focusEmail() {
    if (!view.hidden) email.focus();
  }

  const errorWatch = new MutationObserver(() => {
    form.classList.toggle("lg-has-error", error.textContent.trim().length > 0 && !error.classList.contains("lg-success"));
  });
  errorWatch.observe(error, { childList: true, characterData: true, subtree: true });

  button.addEventListener("click", () => window.setTimeout(watchLoading, 0));
  form.addEventListener("submit", () => window.setTimeout(watchLoading, 0));

  function watchLoading() {
    if (!button.disabled) return;
    label.textContent = "Signing in…";
    const done = new MutationObserver(() => {
      if (button.disabled) return;
      label.textContent = button.dataset.idleLabel || "Continue";
      done.disconnect();
    });
    done.observe(button, { attributes: true, attributeFilter: ["disabled"] });
  }

  new MutationObserver(() => {
    if (view.hidden && window.lgDisposeLoginEgg) window.lgDisposeLoginEgg();
    if (!view.hidden && window.startEgg) window.startEgg();
    focusEmail();
  }).observe(view, { attributes: true, attributeFilter: ["hidden"] });

  focusEmail();
})();
