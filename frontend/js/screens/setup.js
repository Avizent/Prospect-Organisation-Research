/**
 * First-run setup screen — POST /auth/setup.
 *
 * The single-user admin account is created here. Recovery email is
 * optional; if provided, /auth/forgot will be able to issue reset
 * links to it. After a successful setup we redirect to #/login so
 * the operator goes through the normal session flow.
 */

import { api, ApiError } from "../api.js";
import { clear, el, navigate, toast } from "../util.js";

export function render(container) {
  clear(container);

  const banner = el("div", { class: "form-banner hidden", role: "alert" });

  const form = el("form", {
    class: "ls-form",
    onsubmit: async (event) => {
      event.preventDefault();
      banner.classList.add("hidden");
      const username = form.elements.username.value.trim();
      const password = form.elements.password.value;
      const recovery = form.elements.recovery_email.value.trim();
      const body = { username, password };
      if (recovery) body.recovery_email = recovery;
      try {
        await api.setup(body);
        toast("Setup complete. Please sign in.");
        navigate("#/login");
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.detail && typeof err.detail === "object"
              && Array.isArray(err.detail.password_errors)) {
            banner.textContent =
              "Password rejected: "
              + err.detail.password_errors.join("; ");
          } else if (typeof err.detail === "string") {
            banner.textContent = err.detail;
          } else {
            banner.textContent = `Setup failed (HTTP ${err.status}).`;
          }
        } else {
          banner.textContent = "Setup failed \u2014 unexpected error.";
        }
        banner.classList.remove("hidden");
      }
    },
  }, [
    el("h2", { class: "ls-title", text: "First-run setup" }),
    el("p", {
      class: "ls-subtitle",
      text:
        "Create the single admin account for this installation. "
        + "This screen disappears once setup is complete.",
    }),
    banner,
    el("div", { class: "nj-field" }, [
      el("label", { class: "nj-label", for: "ls-setup-username",
                    text: "Username" }),
      el("input", {
        id: "ls-setup-username",
        class: "nj-input",
        name: "username", type: "text", required: true,
      }),
    ]),
    el("div", { class: "nj-field" }, [
      el("label", { class: "nj-label", for: "ls-setup-password",
                    text: "Password" }),
      el("input", {
        id: "ls-setup-password",
        class: "nj-input",
        name: "password", type: "password", required: true,
      }),
    ]),
    el("div", { class: "nj-field" }, [
      el("label", { class: "nj-label", for: "ls-setup-recovery",
                    text: "Recovery email (optional)" }),
      el("input", {
        id: "ls-setup-recovery",
        class: "nj-input",
        name: "recovery_email", type: "email",
      }),
    ]),
    el("button", { type: "submit", class: "btn btn-primary nj-submit",
                   text: "Create admin account" }),
  ]);

  container.appendChild(el("div", { class: "ls-page" }, [
    el("div", { class: "ls-card" }, [form]),
  ]));
}
