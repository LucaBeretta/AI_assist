// @ts-check
/**
 * Projects: the folders of saved developer chats, to read.
 *
 * File reads use the agent's own rules on the server: only inside a folder
 * the person chose for a developer chat, never secrets, binaries or
 * dependency folders. This view never changes a file: the agent does that
 * from a developer conversation, after the person approves each step.
 */
import { h, icon, replace } from "./dom.js";
import { diffLines, fileTree, formatBytes, relativeTime } from "./format.js";

const OUTCOMES = { command_succeeded: "is-success", command_failed: "is-danger", command_timed_out: "is-amber", changed: "is-success" };

/** @param {any} ctx */
export function createProjectsSidebar(ctx) {
  const list = h("ul", { class: "list", "aria-labelledby": "projects-heading" });
  const element = h("div", { class: "sidebar-body" }, h("div", { class: "sidebar-heading", id: "projects-heading" }, "Developer projects"), list);

  /** @param {any} state @param {any} previous */
  function render(state, previous) {
    if (state.view !== "projects") return;
    if (previous && previous.view === "projects" && state.projects === previous.projects
        && state.project?.chatId === previous.project?.chatId && state.projectsError === previous.projectsError) return;
    if (state.projectsError) { replace(list, h("li", { class: "empty-note" }, state.projectsError)); return; }
    if (!state.projects) { replace(list, h("li", { class: "list-item", "aria-hidden": "true" }, h("div", { class: "skeleton" }))); return; }
    if (!state.projects.length) {
      replace(list, h("li", { class: "empty-note" }, "No developer projects yet. Open a folder under Developer in the desktop app to start one."));
      return;
    }
    replace(list, state.projects.map((/** @type {any} */ project) => h("li", null,
      h("button", { class: "list-item", type: "button", "aria-current": state.project?.chatId === project.chat_id ? "true" : null,
        onClick: () => ctx.openProject(project.chat_id) },
        h("span", { class: "list-item-title" }, project.title),
        h("span", { class: "list-item-meta" }, h("span", { class: "mono" }, project.project_path.split(/[\\/]/).pop() || project.project_path),
          "· " + relativeTime(project.updated_at), project.exists ? null : h("span", { class: "badge is-amber" }, "missing"))))));
  }
  return { element, render };
}

/** @param {any} ctx */
export function createProjectsView(ctx) {
  const element = h("div", { class: "view-root" });
  let renderedKey = "";

  /** @param {any} folder @param {any} project @param {number} depth */
  function treeNode(folder, project, depth) {
    const filter = project.filter.toLowerCase();
    const folders = [...folder.folders.values()].sort((a, b) => a.name.localeCompare(b.name));
    const files = folder.files.filter((/** @type {any} */ file) => !filter || file.path.toLowerCase().includes(filter));
    const children = folders.map((child) => treeNode(child, project, depth + 1)).filter(Boolean);
    if (!children.length && !files.length) return null;
    const items = [
      ...children.map((child) => h("li", null, child)),
      ...files.map((/** @type {any} */ file) => h("li", null, h("button", {
        class: "tree-file", type: "button", title: `${file.path} · ${formatBytes(file.size_bytes)}`,
        "aria-current": project.path === file.path ? "true" : null, onClick: () => ctx.openProjectFile(file.path),
      }, file.name))),
    ];
    if (!folder.name) return h("ul", { class: "tree", "aria-label": "Project files" }, items);
    // Top-level folders start open; deeper ones open when a filter matches inside them.
    return h("details", { open: depth <= 1 || Boolean(filter) }, h("summary", null, folder.name), h("ul", null, items));
  }

  /** @param {any} project */
  function sourcePanel(project) {
    const content = project.content;
    if (!project.path) return h("div", { class: "state" }, h("p", { class: "muted" }, "Choose a file to read its source."));
    if (content?.loading) return h("div", { class: "page" }, h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
    if (content?.error) return h("div", { class: "page" }, h("div", { class: "notice is-danger", role: "alert" }, content.error));
    const lines = String(content?.content || "").replace(/\n$/, "").split("\n");
    return h("pre", { class: "source", tabindex: "0", "aria-label": `Source of ${project.path}` },
      h("code", null, lines.map((line) => h("span", { class: "source-line" }, line + "\n"))));
  }

  /** @param {any} project */
  function changesPanel(project) {
    const changes = (project.chat?.activity || []).filter((/** @type {any} */ item) => item.type === "changed" && item.diff);
    if (!changes.length) return h("div", { class: "state" }, h("p", { class: "muted" }, "No applied changes are recorded for this chat."));
    return h("div", { class: "page" }, changes.slice().reverse().map((/** @type {any} */ change) => h("section", { class: "diff" },
      h("div", { class: "diff-title" }, change.destination || change.path || change.text),
      h("pre", { tabindex: "0" }, h("code", null, diffLines(change.diff).map((line) =>
        h("span", { class: `diff-line ${line.kind}` }, line.text + "\n")))))));
  }

  /** @param {any} project */
  function activityPanel(project) {
    const activity = project.chat?.activity || [];
    if (!activity.length) return h("div", { class: "state" }, h("p", { class: "muted" }, "No agent activity is recorded for this chat."));
    return h("ol", { class: "activity", "aria-label": "Agent activity, newest first" }, activity.slice().reverse().map((/** @type {any} */ item) =>
      h("li", { class: "activity-item" },
        h("span", { class: `activity-type ${OUTCOMES[/** @type {keyof typeof OUTCOMES} */ (item.type)] || ""}` }, item.type.replace(/_/g, " ")),
        item.command ? h("code", { class: "mono" }, item.command) : null,
        h("pre", null, item.text))));
  }

  /** @param {any} state @param {any} previous */
  function render(state, previous) {
    if (state.view !== "projects") return;
    const project = state.project;
    const content = project?.content;
    const contentState = !content ? "none" : content.loading ? "loading" : content.error ? `error:${content.error}` : `file:${content.path}`;
    const key = project ? [project.chatId, project.files?.length, project.error, project.filter, project.path, contentState, project.tab, project.chat?.id].map(String).join("|")
      : `none|${state.projects?.length}`;
    if (key === renderedKey && previous?.view === "projects") return;
    const filterFocused = document.activeElement?.id === "project-filter";
    renderedKey = key;
    if (!project) {
      replace(element, h("header", { class: "view-header" }, h("h1", { class: "view-title" }, "Projects")),
        h("div", { class: "view-body" }, h("section", { class: "state" },
          h("div", { class: "state-kicker" }, "developer work"),
          h("h2", null, "Browse developer work"),
          h("p", { class: "muted" }, "Choose a project to read its files, the changes the agent applied, and the commands it ran. "
            + "To change something, open its conversation and describe the task: you approve each edit and command there."))));
      return;
    }
    const tabs = [["source", "Source"], ["changes", "Changes"], ["activity", "Activity"]];
    const tabList = h("div", { class: "tabs", role: "tablist", "aria-label": "Project views" }, tabs.map(([id, label]) =>
      h("button", { class: "tab", role: "tab", type: "button", id: `tab-${id}`, "aria-selected": String(project.tab === id),
        "aria-controls": "project-panel", tabindex: project.tab === id ? "0" : "-1", onClick: () => ctx.updateProject({ tab: id }),
        onKeydown: (/** @type {KeyboardEvent} */ event) => {
          const index = tabs.findIndex(([value]) => value === project.tab);
          const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
          if (!step) return;
          event.preventDefault();
          ctx.updateProject({ tab: tabs[(index + step + tabs.length) % tabs.length][0] });
          window.setTimeout(() => /** @type {HTMLElement | null} */ (document.querySelector(".tab[aria-selected='true']"))?.focus(), 0);
        } }, label)));
    const panel = project.tab === "changes" ? changesPanel(project) : project.tab === "activity" ? activityPanel(project) : sourcePanel(project);
    const filter = /** @type {HTMLInputElement} */ (h("input", { class: "input", id: "project-filter", type: "search", value: project.filter,
      placeholder: "Filter files", "aria-label": "Filter project files",
      onInput: () => ctx.updateProject({ filter: filter.value }) }));
    const tree = project.error ? h("div", { class: "notice is-danger", role: "alert" }, project.error)
      : !project.files ? h("div", { class: "skeleton" })
        : project.files.length ? (treeNode(fileTree(project.files), project, 0) || h("p", { class: "empty-note" }, "No matching files."))
          : h("p", { class: "empty-note" }, "No readable text files.");
    replace(element,
      h("header", { class: "view-header" },
        h("div", { class: "view-heading" }, h("h1", { class: "view-title" }, project.chat?.title || "Project"),
          h("div", { class: "view-subtitle" }, project.root || "")),
        h("span", { class: "badge is-accent", title: "This view only reads files" }, "read-only"),
        h("button", { class: "button button-small", type: "button", onClick: () => ctx.openChat(project.chatId) }, icon("chat"), "Conversation"),
        h("button", { class: "button button-small", type: "button", disabled: Boolean(ctx.store.get().streaming),
          onClick: () => ctx.newTask(project.chatId) }, icon("plus"), "New task")),
      h("div", { class: "project-layout" },
        h("div", { class: "project-tree" }, filter, tree),
        h("div", { class: "project-pane" }, tabList,
          h("div", { class: "tab-panel", id: "project-panel", role: "tabpanel", "aria-labelledby": `tab-${project.tab}` }, panel))));
    if (filterFocused) {
      const input = /** @type {HTMLInputElement | null} */ (document.getElementById("project-filter"));
      input?.focus();
      input?.setSelectionRange(input.value.length, input.value.length);
    }
  }
  return { element, render };
}
