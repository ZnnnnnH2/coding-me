from __future__ import annotations

import html
from html.parser import HTMLParser
import re

from codeingme.agents.base import AgentContext, AgentResult, BaseAgent, StructuredGenerationBundle
from codeingme.runtime import FilePatch, FilePatchPlan


class _TaskListHTMLValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_html = False
        self.has_heading = False
        self.has_style = False
        self.has_script = False
        self.has_search_input = False
        self.has_filter_button = False
        self.has_spotlight = False
        self.in_task_list = False
        self.has_task_list = False
        self.has_placeholder = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "html":
            self.has_html = True
        if tag in {"h1", "h2"}:
            self.has_heading = True
        if tag == "style":
            self.has_style = True
        if tag == "script":
            self.has_script = True
        if tag == "input" and attributes.get("id") == "task-search":
            self.has_search_input = True
        if tag == "button" and attributes.get("data-filter"):
            self.has_filter_button = True
        if tag in {"section", "aside", "div"} and attributes.get("id") == "task-spotlight":
            self.has_spotlight = True
        if tag == "ul" and attributes.get("id") == "task-list":
            self.has_task_list = True
            self.in_task_list = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "ul":
            self.in_task_list = False

    def handle_data(self, data: str) -> None:
        if self.in_task_list and "{{TASK_ITEMS}}" in data:
            self.has_placeholder = True


class FrontendAgent(BaseAgent):
    role = "frontend"

    def run(self, context: AgentContext) -> AgentResult:
        html_source = self._default_html_source(context)
        artifacts: dict[str, object] = {
            "component": "demo_app/static/task_list.html",
            "generation_mode": "template",
        }
        bundle, llm_artifacts = self._llm_structured_files(
            context,
            system_prompt=(
                "You are the frontend agent in a state-machine-driven app generator. "
                "Return a JSON object with keys summary, files, components, and risks. "
                "The files field must be a list of generated files."
            ),
            user_prompt=(
                f"Requirement: {context.requirement.summary}\n"
                "Response format:\n"
                "- Return JSON only.\n"
                '- Include a files array with one object for path "demo_app/static/task_list.html".\n'
                '- Each file object must use keys path, language, and content.\n'
                '- The content value may be plain HTML or a ```html fenced block.\n'
                '- Include components as a list of component names.\n'
                '- Include risks as a list of short risk notes.\n'
                "Constraints:\n"
                "- Return a full HTML document.\n"
                "- Keep a {{TASK_ITEMS}} placeholder inside <ul id=\"task-list\">.\n"
                "- Translate the requirement into the page title, heading, intro copy, and visual tone instead of using a generic tasks demo.\n"
                "- Include a tactile visual system, responsive layout, and clear typographic hierarchy.\n"
                "- Add an interactive command bar with search/filter controls and a spotlight detail panel.\n"
                "- You may include one small inline <script> block for progressive enhancement, but do not rely on external assets or frameworks.\n"
                "- Do not include any prose outside the JSON object."
            ),
            max_tokens=1200,
            required_files={"demo_app/static/task_list.html": "html"},
            collection_fields=["components", "risks"],
            validator=self._is_valid_frontend_bundle,
        )
        artifacts.update(llm_artifacts)
        if bundle is not None:
            llm_source = self._file_content(bundle, "demo_app/static/task_list.html")
            if llm_source is not None:
                html_source = llm_source
            artifacts["components"] = bundle.collections["components"] or self._infer_components(llm_source or "")
            artifacts["risks"] = bundle.collections["risks"]
            artifacts["generation_mode"] = "llm"

        return AgentResult(
            role=self.role,
            summary="Generated an interactive task command center for the FastAPI demo",
            artifacts=artifacts,
            file_plan=FilePatchPlan(
                name="frontend_demo",
                patches=[FilePatch(path="demo_app/static/task_list.html", content=html_source)],
            ),
        )

    def _default_html_source(self, context: AgentContext) -> str:
        page_title = self._page_title(context)
        headline = self._headline(context)
        lede = self._lede(context)
        section_title = self._section_title(context)
        template = r"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>__PAGE_TITLE__</title>
    <style>
      :root {
        --paper: #f7f1e7;
        --paper-deep: #e5d7c4;
        --ink: #13212d;
        --ink-soft: #455765;
        --panel: rgba(255, 251, 245, 0.76);
        --line: rgba(19, 33, 45, 0.12);
        --accent: #c15b35;
        --accent-soft: rgba(193, 91, 53, 0.14);
        --teal: #1f6c74;
        --teal-soft: rgba(31, 108, 116, 0.14);
        --shadow: 0 22px 70px rgba(17, 30, 41, 0.14);
        --radius-xl: 30px;
        --radius-lg: 22px;
        --radius-md: 16px;
      }

      * {
        box-sizing: border-box;
      }

      html {
        color-scheme: light;
      }

      body {
        margin: 0;
        min-height: 100vh;
        color: var(--ink);
        font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Palatino, serif;
        background:
          radial-gradient(circle at 12% 18%, rgba(193, 91, 53, 0.18), transparent 34%),
          radial-gradient(circle at 86% 14%, rgba(31, 108, 116, 0.18), transparent 30%),
          radial-gradient(circle at 50% 120%, rgba(19, 33, 45, 0.11), transparent 42%),
          linear-gradient(180deg, #fbf7f0 0%, #efe3d2 54%, #d9e2eb 100%);
        position: relative;
        overflow-x: hidden;
      }

      body::before {
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        background-image:
          linear-gradient(rgba(19, 33, 45, 0.035) 1px, transparent 1px),
          linear-gradient(90deg, rgba(19, 33, 45, 0.035) 1px, transparent 1px);
        background-size: 28px 28px;
        mask-image: radial-gradient(circle at center, black 42%, transparent 88%);
      }

      .shell {
        width: min(1180px, calc(100% - 2rem));
        margin: 0 auto;
        padding: 2rem 0 4rem;
        position: relative;
      }

      .panel {
        background: var(--panel);
        border: 1px solid rgba(255, 255, 255, 0.65);
        box-shadow: var(--shadow);
        backdrop-filter: blur(14px);
      }

      .hero {
        border-radius: var(--radius-xl);
        padding: clamp(1.5rem, 3vw, 2.5rem);
        position: relative;
        overflow: hidden;
      }

      .hero::after {
        content: "";
        position: absolute;
        inset: auto -10% -32% auto;
        width: 320px;
        height: 320px;
        border-radius: 50%;
        background: radial-gradient(circle, rgba(193, 91, 53, 0.22), transparent 68%);
        pointer-events: none;
      }

      .eyebrow-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        margin-bottom: 1.5rem;
        flex-wrap: wrap;
      }

      .eyebrow {
        letter-spacing: 0.24em;
        text-transform: uppercase;
        font-size: 0.72rem;
        color: var(--ink-soft);
      }

      .pulse-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.55rem 0.9rem;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.82);
        border: 1px solid var(--line);
        font-size: 0.88rem;
      }

      .pulse-badge::before {
        content: "";
        width: 0.6rem;
        height: 0.6rem;
        border-radius: 999px;
        background: var(--teal);
        box-shadow: 0 0 0 0 rgba(31, 108, 116, 0.45);
        animation: pulse 2.4s infinite;
      }

      .hero-grid {
        display: grid;
        grid-template-columns: minmax(0, 1.5fr) minmax(310px, 1fr);
        gap: 1.5rem;
        align-items: end;
      }

      .hero-copy p {
        margin: 0;
      }

      .kicker {
        font-size: 0.95rem;
        color: var(--accent);
        margin-bottom: 0.85rem;
      }

      h1 {
        margin: 0;
        font-size: clamp(2.8rem, 7vw, 5.8rem);
        line-height: 0.94;
        letter-spacing: -0.06em;
        max-width: 11ch;
      }

      .lede {
        margin-top: 1rem;
        max-width: 54ch;
        font-size: 1.04rem;
        line-height: 1.7;
        color: var(--ink-soft);
      }

      .stat-rack {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 0.9rem;
      }

      .stat-card {
        padding: 1.1rem;
        border-radius: var(--radius-lg);
        background: rgba(255, 255, 255, 0.78);
        border: 1px solid rgba(19, 33, 45, 0.08);
      }

      .stat-card span {
        display: block;
        font-size: 0.75rem;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--ink-soft);
        margin-bottom: 0.55rem;
      }

      .stat-card strong {
        display: block;
        font-size: clamp(1.7rem, 3vw, 2.5rem);
        line-height: 1;
        margin-bottom: 0.35rem;
      }

      .stat-card em {
        display: block;
        font-style: normal;
        color: var(--ink-soft);
        font-size: 0.95rem;
      }

      .workbench {
        margin-top: 1.4rem;
      }

      .command-bar {
        display: flex;
        gap: 1rem;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        padding: 1rem 1.15rem;
        border-radius: var(--radius-lg);
        margin-bottom: 1rem;
      }

      .search-field {
        display: grid;
        gap: 0.45rem;
        min-width: min(100%, 320px);
        flex: 1 1 280px;
      }

      .search-field span,
      .sort-wrap span {
        font-size: 0.78rem;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--ink-soft);
      }

      .search-field input {
        width: 100%;
        padding: 0.95rem 1rem;
        border-radius: 999px;
        border: 1px solid rgba(19, 33, 45, 0.12);
        background: rgba(255, 255, 255, 0.88);
        color: var(--ink);
        font: inherit;
        outline: none;
        transition: border-color 180ms ease, box-shadow 180ms ease, transform 180ms ease;
      }

      .search-field input:focus {
        border-color: rgba(31, 108, 116, 0.48);
        box-shadow: 0 0 0 5px rgba(31, 108, 116, 0.12);
        transform: translateY(-1px);
      }

      .controls-cluster {
        display: flex;
        align-items: end;
        gap: 1rem;
        flex-wrap: wrap;
      }

      .filters {
        display: flex;
        gap: 0.55rem;
        flex-wrap: wrap;
      }

      .filters button,
      .ghost-button {
        border: 0;
        border-radius: 999px;
        padding: 0.82rem 1rem;
        background: rgba(255, 255, 255, 0.84);
        color: var(--ink);
        font: inherit;
        cursor: pointer;
        transition: transform 180ms ease, background 180ms ease, box-shadow 180ms ease, color 180ms ease;
        box-shadow: inset 0 0 0 1px rgba(19, 33, 45, 0.1);
      }

      .filters button:hover,
      .ghost-button:hover,
      .filters button:focus-visible,
      .ghost-button:focus-visible {
        transform: translateY(-1px);
        background: rgba(255, 255, 255, 1);
      }

      .filters button.is-active,
      .ghost-button.is-active {
        background: var(--ink);
        color: #f8f2ea;
        box-shadow: 0 14px 32px rgba(19, 33, 45, 0.16);
      }

      .sort-wrap {
        display: grid;
        gap: 0.45rem;
      }

      .content-grid {
        display: grid;
        grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.9fr);
        gap: 1rem;
        align-items: start;
      }

      .queue,
      .spotlight {
        border-radius: var(--radius-xl);
        padding: 1.3rem;
      }

      .section-heading {
        display: flex;
        justify-content: space-between;
        align-items: end;
        gap: 1rem;
        margin-bottom: 1rem;
        flex-wrap: wrap;
      }

      h2 {
        margin: 0;
        font-size: 1.5rem;
      }

      .section-heading p,
      .spotlight-copy,
      .empty-state {
        margin: 0;
        color: var(--ink-soft);
        line-height: 1.6;
      }

      .task-feed {
        list-style: none;
        padding: 0;
        margin: 0;
        display: grid;
        gap: 0.9rem;
      }

      .task-feed > li {
        list-style: none;
      }

      .task-card {
        position: relative;
        overflow: hidden;
        border-radius: var(--radius-lg);
        padding: 1.15rem 1.15rem 1rem;
        background: linear-gradient(145deg, rgba(255, 250, 243, 0.96), rgba(243, 233, 216, 0.94));
        border: 1px solid rgba(19, 33, 45, 0.08);
        cursor: pointer;
        transform: translateY(14px);
        opacity: 0;
        animation: rise 680ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
        animation-delay: calc(var(--stagger, 0) * 55ms);
        transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease, background 180ms ease;
      }

      .task-card::after {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 4px;
        background: linear-gradient(180deg, var(--accent), rgba(193, 91, 53, 0.05));
      }

      .task-card[data-completed="true"] {
        background: linear-gradient(145deg, rgba(243, 248, 246, 0.96), rgba(224, 239, 236, 0.95));
      }

      .task-card[data-completed="true"]::after {
        background: linear-gradient(180deg, var(--teal), rgba(31, 108, 116, 0.05));
      }

      .task-card:hover,
      .task-card:focus-visible,
      .task-card.is-active {
        transform: translateY(-2px);
        border-color: rgba(19, 33, 45, 0.16);
        box-shadow: 0 22px 46px rgba(19, 33, 45, 0.13);
      }

      .task-card.is-hidden {
        display: none;
      }

      .task-card__top,
      .task-card__footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        flex-wrap: wrap;
      }

      .task-card__index {
        font-size: 0.84rem;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--ink-soft);
      }

      .task-card__badge {
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        padding: 0.35rem 0.72rem;
        border-radius: 999px;
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
      }

      .task-card__badge::before {
        content: "";
        width: 0.48rem;
        height: 0.48rem;
        border-radius: 999px;
        background: currentColor;
      }

      .task-card__badge.is-open {
        color: var(--accent);
        background: var(--accent-soft);
      }

      .task-card__badge.is-done {
        color: var(--teal);
        background: var(--teal-soft);
      }

      .task-card h3 {
        margin: 0.95rem 0 0.45rem;
        font-size: 1.25rem;
        line-height: 1.15;
      }

      .task-card p {
        margin: 0;
        color: var(--ink-soft);
        line-height: 1.65;
      }

      .task-card__footer {
        margin-top: 1rem;
        font-size: 0.9rem;
        color: var(--ink-soft);
      }

      .spotlight {
        position: sticky;
        top: 1rem;
        display: grid;
        gap: 1rem;
      }

      .spotlight-label {
        margin: 0;
        font-size: 0.78rem;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--ink-soft);
      }

      .spotlight h2 {
        font-size: clamp(1.6rem, 3vw, 2.3rem);
        line-height: 1.05;
      }

      .spotlight-meta {
        display: flex;
        gap: 0.55rem;
        flex-wrap: wrap;
      }

      .status-pill {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0.5rem 0.78rem;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.84);
        border: 1px solid rgba(19, 33, 45, 0.08);
        font-size: 0.88rem;
      }

      .status-pill.is-muted {
        color: var(--ink-soft);
      }

      .progress-wrap {
        display: grid;
        gap: 0.55rem;
      }

      .progress-wrap span {
        color: var(--ink-soft);
        font-size: 0.92rem;
      }

      .progress-rail {
        height: 0.75rem;
        border-radius: 999px;
        background: rgba(19, 33, 45, 0.08);
        overflow: hidden;
      }

      .progress-rail i {
        display: block;
        height: 100%;
        width: 34%;
        border-radius: inherit;
        background: linear-gradient(90deg, var(--accent), var(--teal));
        transition: width 220ms ease;
      }

      .spotlight-note {
        padding: 1rem;
        border-radius: var(--radius-md);
        background: rgba(255, 255, 255, 0.78);
        border: 1px solid rgba(19, 33, 45, 0.08);
        color: var(--ink-soft);
        line-height: 1.7;
      }

      .empty-state {
        margin-top: 1rem;
        padding: 1rem;
        border-radius: var(--radius-md);
        background: rgba(255, 255, 255, 0.72);
      }

      @keyframes rise {
        from {
          opacity: 0;
          transform: translateY(14px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      @keyframes pulse {
        0% {
          box-shadow: 0 0 0 0 rgba(31, 108, 116, 0.45);
        }
        72% {
          box-shadow: 0 0 0 12px rgba(31, 108, 116, 0);
        }
        100% {
          box-shadow: 0 0 0 0 rgba(31, 108, 116, 0);
        }
      }

      @media (max-width: 940px) {
        .hero-grid,
        .content-grid {
          grid-template-columns: 1fr;
        }

        .spotlight {
          position: static;
        }
      }

      @media (max-width: 640px) {
        .shell {
          width: min(100% - 1rem, 100%);
          padding-top: 1rem;
        }

        .hero,
        .queue,
        .spotlight {
          border-radius: 24px;
        }

        .stat-rack {
          grid-template-columns: 1fr;
        }

        .command-bar {
          padding: 0.95rem;
        }

        .filters {
          width: 100%;
        }

        .filters button,
        .ghost-button {
          flex: 1 1 140px;
          justify-content: center;
        }
      }
    </style>
  </head>
  <body>
    <main class="shell">
      <section class="hero panel">
        <div class="eyebrow-row">
          <span class="eyebrow">Live Work Surface</span>
          <span class="pulse-badge" id="task-pulse">Queue synchronized</span>
        </div>
        <div class="hero-grid">
          <div class="hero-copy">
            <p class="kicker">Beautiful enough to invite focus, practical enough to move work.</p>
            <h1>__HEADLINE__</h1>
            <p class="lede">__LEDE__</p>
          </div>
          <div class="stat-rack" aria-label="Queue summary">
            <article class="stat-card">
              <span>Total</span>
              <strong data-stat="total">0</strong>
              <em>Tasks currently in the queue</em>
            </article>
            <article class="stat-card">
              <span>Open</span>
              <strong data-stat="open">0</strong>
              <em>Work that still needs momentum</em>
            </article>
            <article class="stat-card">
              <span>Complete</span>
              <strong data-stat="done">0</strong>
              <em>Items cleared for review</em>
            </article>
          </div>
        </div>
      </section>

      <section class="workbench">
        <div class="command-bar panel">
          <label class="search-field" for="task-search">
            <span>Search the queue</span>
            <input id="task-search" type="search" placeholder="Filter by title or status" />
          </label>
          <div class="controls-cluster">
            <div class="filters" role="group" aria-label="Task filters">
              <button class="is-active" type="button" data-filter="all">All</button>
              <button type="button" data-filter="open">Open</button>
              <button type="button" data-filter="done">Completed</button>
            </div>
            <div class="sort-wrap">
              <span>Order</span>
              <button id="sort-toggle" class="ghost-button" type="button">Natural flow</button>
            </div>
          </div>
        </div>

        <div class="content-grid">
          <section class="queue panel">
            <div class="section-heading">
              <div>
                <h2>__SECTION_TITLE__</h2>
                <p id="task-count-label">0 visible tasks</p>
              </div>
            </div>
            <ul id="task-list" class="task-feed">
              {{TASK_ITEMS}}
            </ul>
            <p id="task-empty" class="empty-state" hidden>No tasks match the current filters. Try widening the search.</p>
          </section>

          <aside id="task-spotlight" class="spotlight panel">
            <p class="spotlight-label">Task Spotlight</p>
            <h2 id="spotlight-title">Pick a task to inspect</h2>
            <p id="spotlight-copy" class="spotlight-copy">
              Click a card to surface its current state, queue position, and focus hint.
            </p>
            <div class="spotlight-meta">
              <span id="spotlight-status" class="status-pill is-muted">Awaiting selection</span>
              <span id="spotlight-index" class="status-pill is-muted">Queue item</span>
            </div>
            <div class="progress-wrap">
              <span id="spotlight-progress-label">Progress signal</span>
              <div class="progress-rail" aria-hidden="true">
                <i id="spotlight-progress"></i>
              </div>
            </div>
            <div class="spotlight-note" id="spotlight-note">
              Use the command bar to slice the queue, then move through tasks without losing visual context.
            </div>
          </aside>
        </div>
      </section>
    </main>

    <script>
      (() => {
        const list = document.getElementById("task-list");
        const search = document.getElementById("task-search");
        const filterButtons = Array.from(document.querySelectorAll("[data-filter]"));
        const sortToggle = document.getElementById("sort-toggle");
        const countLabel = document.getElementById("task-count-label");
        const pulse = document.getElementById("task-pulse");
        const empty = document.getElementById("task-empty");
        const statTotal = document.querySelector('[data-stat="total"]');
        const statOpen = document.querySelector('[data-stat="open"]');
        const statDone = document.querySelector('[data-stat="done"]');
        const spotlightTitle = document.getElementById("spotlight-title");
        const spotlightCopy = document.getElementById("spotlight-copy");
        const spotlightStatus = document.getElementById("spotlight-status");
        const spotlightIndex = document.getElementById("spotlight-index");
        const spotlightNote = document.getElementById("spotlight-note");
        const spotlightProgress = document.getElementById("spotlight-progress");
        const spotlightProgressLabel = document.getElementById("spotlight-progress-label");

        if (!list || !search || !sortToggle) {
          return;
        }

        const escapeHtml = (value) =>
          value.replace(/[&<>"']/g, (char) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
          }[char] || char));

        let currentFilter = "all";
        let sortCompletedFirst = false;
        let activeTaskId = null;

        const tasks = Array.from(list.querySelectorAll("li")).map((item, index) => {
          const completed = item.dataset.completed === "true";
          const rawText = item.textContent || "";
          const cleanedTitle = rawText.replace(/\\s+\\((done|todo)\\)\\s*$/i, "").trim();
          const title = cleanedTitle || `Task ${index + 1}`;
          const helperCopy = completed
            ? "Completed and ready for review."
            : "Still active and worth immediate attention.";

          item.classList.add("task-card");
          item.style.setProperty("--stagger", String(index));
          item.tabIndex = 0;
          item.dataset.completed = String(completed);
          item.dataset.title = title.toLowerCase();
          item.dataset.taskId = String(index);
          item.innerHTML = `
            <div class="task-card__top">
              <span class="task-card__index">Queue ${String(index + 1).padStart(2, "0")}</span>
              <span class="task-card__badge ${completed ? "is-done" : "is-open"}">
                ${completed ? "Completed" : "In Motion"}
              </span>
            </div>
            <h3>${escapeHtml(title)}</h3>
            <p>${helperCopy}</p>
            <div class="task-card__footer">
              <span>${completed ? "Cleared" : "Open focus"}</span>
              <span>${completed ? "Stable" : "Needs action"}</span>
            </div>
          `;

          const task = { element: item, title, completed, index };
          item.addEventListener("click", () => setSpotlight(task));
          item.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              setSpotlight(task);
            }
          });
          return task;
        });

        const renderStats = () => {
          const total = tasks.length;
          const done = tasks.filter((task) => task.completed).length;
          const open = total - done;
          statTotal.textContent = String(total);
          statOpen.textContent = String(open);
          statDone.textContent = String(done);
        };

        const visibleTasks = () =>
          tasks.filter((task) => !task.element.classList.contains("is-hidden"));

        const setSpotlight = (task) => {
          activeTaskId = String(task.index);
          tasks.forEach((candidate) => {
            candidate.element.classList.toggle("is-active", candidate.index === task.index);
          });

          spotlightTitle.textContent = task.title;
          spotlightCopy.textContent = task.completed
            ? "This item is complete. Use it as a calm anchor for what good throughput looks like."
            : "This item is still moving. Keep it visible, reduce friction, and drive it toward completion.";
          spotlightStatus.textContent = task.completed ? "Completed" : "Open";
          spotlightStatus.classList.toggle("is-muted", false);
          spotlightIndex.textContent = `Queue ${String(task.index + 1).padStart(2, "0")}`;
          spotlightNote.textContent = task.completed
            ? "Completed items stay visible here so the interface still feels resolved, not merely filtered."
            : "Open items get a stronger progress signal so the next decision stays obvious.";
          spotlightProgressLabel.textContent = task.completed ? "Completion signal" : "Focus signal";
          spotlightProgress.style.width = task.completed ? "100%" : "58%";
        };

        const applyFilters = () => {
          const query = search.value.trim().toLowerCase();
          const orderedTasks = [...tasks].sort((left, right) => {
            if (!sortCompletedFirst) {
              return left.index - right.index;
            }
            if (left.completed === right.completed) {
              return left.index - right.index;
            }
            return Number(right.completed) - Number(left.completed);
          });

          orderedTasks.forEach((task) => list.appendChild(task.element));

          tasks.forEach((task) => {
            const matchesQuery =
              !query ||
              task.title.toLowerCase().includes(query) ||
              (task.completed ? "completed done" : "open in motion").includes(query);
            const matchesFilter =
              currentFilter === "all" ||
              (currentFilter === "done" && task.completed) ||
              (currentFilter === "open" && !task.completed);
            task.element.classList.toggle("is-hidden", !(matchesQuery && matchesFilter));
          });

          const visible = visibleTasks();
          countLabel.textContent = `${visible.length} visible task${visible.length === 1 ? "" : "s"}`;
          empty.hidden = visible.length !== 0;
          pulse.textContent =
            currentFilter === "all"
              ? `Showing ${visible.length} tasks`
              : `Filter: ${currentFilter} (${visible.length})`;

          if (visible.length === 0) {
            tasks.forEach((task) => task.element.classList.remove("is-active"));
            spotlightTitle.textContent = "No tasks in this view";
            spotlightCopy.textContent = "Widen the filters or search query to bring work back into focus.";
            spotlightStatus.textContent = "Filtered";
            spotlightStatus.classList.add("is-muted");
            spotlightIndex.textContent = "Queue hidden";
            spotlightNote.textContent = "The interface keeps context even when the current slice is empty.";
            spotlightProgressLabel.textContent = "View signal";
            spotlightProgress.style.width = "14%";
            return;
          }

          const activeVisibleTask = visible.find((task) => String(task.index) === activeTaskId);
          setSpotlight(activeVisibleTask || visible[0]);
        };

        filterButtons.forEach((button) => {
          button.addEventListener("click", () => {
            currentFilter = button.dataset.filter || "all";
            filterButtons.forEach((candidate) => {
              candidate.classList.toggle("is-active", candidate === button);
            });
            applyFilters();
          });
        });

        sortToggle.addEventListener("click", () => {
          sortCompletedFirst = !sortCompletedFirst;
          sortToggle.classList.toggle("is-active", sortCompletedFirst);
          sortToggle.textContent = sortCompletedFirst ? "Completed first" : "Natural flow";
          applyFilters();
        });

        search.addEventListener("input", applyFilters);

        renderStats();
        applyFilters();
      })();
    </script>
  </body>
</html>
"""
        return (
            template.replace("__PAGE_TITLE__", html.escape(page_title, quote=True))
            .replace("__HEADLINE__", html.escape(headline))
            .replace("__LEDE__", html.escape(lede))
            .replace("__SECTION_TITLE__", html.escape(section_title))
        )

    def _is_valid_frontend_bundle(self, bundle: StructuredGenerationBundle) -> bool:
        content = self._file_content(bundle, "demo_app/static/task_list.html")
        if content is None or not self._is_html_file("demo_app/static/task_list.html", content):
            return False
        parser = _TaskListHTMLValidator()
        try:
            parser.feed(content)
        except Exception:
            return False
        return (
            parser.has_html
            and parser.has_heading
            and parser.has_style
            and parser.has_script
            and parser.has_search_input
            and parser.has_filter_button
            and parser.has_spotlight
            and parser.has_task_list
            and parser.has_placeholder
        )

    def _infer_components(self, content: str) -> list[str]:
        components: list[str] = []
        if "{{TASK_ITEMS}}" in content:
            components.append("TaskListPage")
        if 'id="task-search"' in content:
            components.append("TaskCommandBar")
        if 'id="task-spotlight"' in content:
            components.append("TaskSpotlight")
        return components

    def _page_title(self, context: AgentContext) -> str:
        return f"{self._primary_label(context)} Atlas"

    def _headline(self, context: AgentContext) -> str:
        return f"{self._primary_label(context)} Control Room"

    def _lede(self, context: AgentContext) -> str:
        return (
            f"A tactile, high-contrast dashboard for scanning {self._lower_label(context)}, "
            "isolating what still needs movement, and keeping finished work visible without clutter."
        )

    def _section_title(self, context: AgentContext) -> str:
        return f"{self._primary_label(context)} Queue"

    def _primary_label(self, context: AgentContext) -> str:
        if context.schemas:
            return self._humanize_identifier(context.schemas[0].name)
        return "Task"

    def _lower_label(self, context: AgentContext) -> str:
        return self._primary_label(context).lower()

    def _humanize_identifier(self, value: str) -> str:
        parts = re.findall(r"[A-Z]?[a-z0-9]+", value)
        return " ".join(part.capitalize() for part in parts) or value
