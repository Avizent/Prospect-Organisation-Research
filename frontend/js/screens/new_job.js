/**
 * New-job screen — POST /api/jobs.
 *
 * The form maps directly onto CreateJobBody (company_name +
 * company_url). On success, navigate to #/jobs/<id>. No Stage 1
 * runs from this screen; that's deliberate — Step 11 is fake-safe
 * and the orchestrator that advances created -> researching is
 * out of scope for Step 12.
 *
 * Step 49: polished visual design — hero, two-column layout,
 * "What happens next?" panel, operator tip.
 */

import { api, ApiError } from "../api.js";
import { clear, el, formatDetail, navigate, toast } from "../util.js";

export function render(container) {
  clear(container);

  const banner = el("div", { class: "form-banner hidden", role: "alert" });

  // Form submission handler — behaviour is identical to the original.
  const form = el("form", {
    class: "nj-form",
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
    banner,
    el("h3", { class: "nj-card-title", text: "Prospect Details" }),
    el("p", { class: "nj-card-subtitle",
      text: "Specify the target corporate entity below",
    }),
    el("div", { class: "nj-field" }, [
      el("label", { class: "nj-label", for: "nj-company-name",
        text: "Company Name",
      }),
      el("input", {
        id: "nj-company-name",
        class: "nj-input",
        name: "company_name",
        type: "text",
        required: true,
        maxlength: 300,
        autocomplete: "off",
        placeholder: "e.g. Acme Corporation",
      }),
    ]),
    el("div", { class: "nj-field" }, [
      el("label", { class: "nj-label", for: "nj-company-url",
        text: "Website URL",
      }),
      el("input", {
        id: "nj-company-url",
        class: "nj-input",
        name: "company_url",
        type: "url",
        required: true,
        maxlength: 2048,
        autocomplete: "off",
        placeholder: "https://",
      }),
    ]),
    el("button", {
      type: "submit",
      class: "btn btn-primary nj-submit",
      text: "Start Research Sweep",
    }),
  ]);

  const formCard = el("div", { class: "card nj-form-card" }, [form]);

  // "What happens next?" — right-column info card.
  const whatNextCard = el("div", { class: "card nj-whatnext" }, [
    el("div", { class: "card-header", text: "What happens next?" }),
    el("div", { class: "card-body" }, [
      el("div", { class: "nj-whatnext-step" }, [
        el("span", { class: "nj-whatnext-icon", text: "1" }),
        el("div", {}, [
          el("strong", { text: "Data Ingestion" }),
          el("p", { class: "nj-whatnext-desc",
            text: "Analyse corporate repositories, public data and sector signals.",
          }),
        ]),
      ]),
      el("div", { class: "nj-whatnext-step" }, [
        el("span", { class: "nj-whatnext-icon", text: "2" }),
        el("div", {}, [
          el("strong", { text: "Stakeholder Auditing" }),
          el("p", { class: "nj-whatnext-desc",
            text: "Map relevant decision-makers and organisational context.",
          }),
        ]),
      ]),
      el("div", { class: "nj-whatnext-step" }, [
        el("span", { class: "nj-whatnext-icon", text: "3" }),
        el("div", {}, [
          el("strong", { text: "Pitch Generation" }),
          el("p", { class: "nj-whatnext-desc",
            text: "Prepare a structured sales briefing and export-ready documents.",
          }),
        ]),
      ]),
    ]),
  ]);

  // Operator tip — below the "What happens next?" card.
  const tipCard = el("div", { class: "card nj-operator-tip" }, [
    el("div", { class: "card-body" }, [
      el("p", { class: "nj-tip-text",
        text: (
          "Corporate brief preparations are generated to support operator "
          + "review. Verify factual claims before external use."
        ),
      }),
    ]),
  ]);

  const sideColumn = el("div", { class: "nj-side" }, [
    whatNextCard,
    tipCard,
  ]);

  const layout = el("div", { class: "nj-layout" }, [formCard, sideColumn]);

  const hero = el("div", { class: "nj-hero" }, [
    el("h2", { class: "nj-hero-title",
      text: "Create Corporate Briefing Report",
    }),
    el("p", { class: "nj-hero-subtitle",
      text: (
        "Initiate deep intelligence sweeps across commercial registry files, "
        + "search indices, and technical pipelines to draft complete, "
        + "client-ready sales briefings."
      ),
    }),
  ]);

  container.appendChild(el("div", { class: "page nj-page" }, [
    hero,
    layout,
  ]));
}
