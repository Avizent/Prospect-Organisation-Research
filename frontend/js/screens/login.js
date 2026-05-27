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
    class: "auth-form",
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
          banner.textContent = "Sign-in failed — unexpected error.";
        }
        banner.classList.remove("hidden");
      }
    },
  }, [
    el("h2", { text: "Sign in" }),
    banner,
    el("label", { class: "form-row" }, [
      el("span", { text: "Username" }),
      el("input", {
        name: "username", type: "text", required: true,
        autocomplete: "username",
      }),
    ]),
    el("label", { class: "form-row" }, [
      el("span", { text: "Password" }),
      el("input", {
        name: "password", type: "password", required: true,
        autocomplete: "current-password",
      }),
    ]),
    el("button", { type: "submit", class: "btn btn-primary",
                   text: "Sign in" }),
  ]);

  container.appendChild(form);
}
