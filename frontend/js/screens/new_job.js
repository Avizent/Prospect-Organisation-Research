/**
 * New-job screen — POST /api/jobs.
 *
 * The form maps directly onto CreateJobBody (company_name +
 * company_url). On success, navigate to #/jobs/<id>. No Stage 1
 * runs from this screen; that's deliberate — Step 11 is fake-safe
 * and the orchestrator that advances created -> researching is
 * out of scope for Step 12.
 */

import { api, ApiError } from "../api.js";
import { clear, el, formatDetail, navigate, toast } from "../util.js";

export function render(container) {
  clear(container);

  const banner = el("div", { class: "form-banner hidden", role: "alert" });

  const form = el("form", {
    class: "auth-form",
    onsubmit: async (event) => {
      event.preventDefault();
      banner.classList.add("hidden");
      const body = {
        company_name: form.elements.company_name.value.trim(),
        company_url: form.elements.company_url.value.trim(),
      };
      try {
        const result = await api.createJob(body);
        toast(`Created job ${result.job_id}.`);
        navigate(`#/jobs/${result.job_id}`);
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 401) {
            navigate("#/login");
            return;
          }
          banner.textContent =
            err.detail && typeof err.detail === "object"
              ? formatDetail(err.detail)
              : (typeof err.detail === "string"
                  ? err.detail
                  : `Create job failed (HTTP ${err.status}).`);
        } else {
          banner.textContent = "Create job failed — unexpected error.";
        }
        banner.classList.remove("hidden");
      }
    },
  }, [
    el("h2", { text: "New prospect" }),
    el("p", {
      class: "form-help",
      text:
        "Intake only. The job is created in state 'created' and "
        + "will sit there until the Stage 1 worker is wired up "
        + "(out of scope for step 12).",
    }),
    banner,
    el("label", { class: "form-row" }, [
      el("span", { text: "Company name" }),
      el("input", {
        name: "company_name", type: "text", required: true,
        maxlength: 300, autocomplete: "off",
      }),
    ]),
    el("label", { class: "form-row" }, [
      el("span", { text: "Company URL" }),
      el("input", {
        name: "company_url", type: "url", required: true,
        maxlength: 2048, autocomplete: "off",
        placeholder: "company URL",
      }),
    ]),
    el("button", { type: "submit", class: "btn btn-primary",
                   text: "Create job" }),
  ]);

  container.appendChild(form);
}
