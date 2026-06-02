/**
 * Login screen — POST /auth/login.
 *
 * On success, redirect to #/jobs/new. On failure, show the error
 * detail returned by the backend (rate-limit messages, "invalid
 * username or password") in a banner.
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
      try {
        await api.login(username, password);
        toast("Signed in.");
        navigate("#/jobs/new");
      } catch (err) {
        if (err instanceof ApiError) {
          banner.textContent =
            typeof err.detail === "string"
              ? err.detail
              : `Sign-in failed (HTTP ${err.status}).`;
        } else {
          banner.textContent = "Sign-in failed \u2014 unexpected error.";
        }
        banner.classList.remove("hidden");
      }
    },
  }, [
    el("h2", { class: "ls-title", text: "Sign in" }),
    el("p", { class: "ls-subtitle", text: "ANS Prospect Intelligence Tool" }),
    banner,
    el("div", { class: "nj-field" }, [
      el("label", { class: "nj-label", for: "ls-username", text: "Username" }),
      el("input", {
        id: "ls-username",
        class: "nj-input",
        name: "username", type: "text", required: true,
        autocomplete: "username",
      }),
    ]),
    el("div", { class: "nj-field" }, [
      el("label", { class: "nj-label", for: "ls-password", text: "Password" }),
      el("input", {
        id: "ls-password",
        class: "nj-input",
        name: "password", type: "password", required: true,
        autocomplete: "current-password",
      }),
    ]),
    el("button", { type: "submit", class: "btn btn-primary nj-submit",
                   text: "Sign in" }),
  ]);

  container.appendChild(el("div", { class: "ls-page" }, [
    el("div", { class: "ls-card" }, [form]),
  ]));
}
