"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  data: null, // 服务端最近一次返回的完整状态
  sections: [], // 正在编辑的副本
  currentIndex: 1, // 章节号（不是数组下标）
  preview: false,
  dirty: false,
  saving: false,
  busy: false,
};

let previewTimer = 0;

/* ------------------------------------------------------------------ 工具 */

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function")
      node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fmtSize(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function countChars(text) {
  return String(text || "").replace(/\s+/g, "").length;
}

function current() {
  return (
    state.sections.find((section) => section.index === state.currentIndex) ||
    state.sections[0]
  );
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`服务返回了非 JSON 内容（HTTP ${response.status}）`);
  }
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.hint = payload.hint || "";
    throw error;
  }
  return payload;
}

function showStatus(text, kind = "") {
  const node = $("status");
  node.textContent = text;
  node.className = `status ${kind}`.trim();
}

function showBanner(lines, kind = "warn") {
  const box = $("banner");
  const body = $("banner-body");
  body.replaceChildren(
    lines.length === 1
      ? h("div", { text: lines[0] })
      : h("ul", {}, lines.map((line) => h("li", { text: line }))),
  );
  box.className = `banner ${kind}`.trim();
  box.hidden = false;
}

function hideBanner() {
  $("banner").hidden = true;
}

function showError(error) {
  const lines = [error.message || String(error)];
  if (error.hint) lines.push(error.hint);
  showBanner(lines, "err");
  showStatus("出错了", "err");
}

function markDirty() {
  if (state.dirty) return;
  state.dirty = true;
  document.title = "● 校对 · video2doc";
}

function clearDirty() {
  state.dirty = false;
  document.title = "校对 · video2doc";
}

function setBusy(busy) {
  state.busy = busy;
  $("save").disabled = busy;
  $("rewrite").disabled = busy;
  $("save").innerHTML = busy ? "处理中…" : '保存并渲染 <kbd>Ctrl S</kbd>';
}

/* ------------------------------------------------------------------ 装载 */

function adopt(data, { keep = null } = {}) {
  state.data = data;
  state.sections = data.sections.map((section) => ({
    ...section,
    images: section.images.map((image) => ({ ...image })),
  }));

  if (keep != null && state.sections.some((section) => section.index === keep)) {
    state.currentIndex = keep;
  } else if (!state.sections.some((section) => section.index === state.currentIndex)) {
    state.currentIndex = state.sections[0] ? state.sections[0].index : 1;
  }

  clearDirty();
  hideBanner();
  const titleInput = $("doc-title");
  titleInput.value = data.title;
  // 配置文件里写死 render.title 时，界面改标题不会生效，干脆锁住并说明原因
  titleInput.disabled = Boolean(data.title_locked);
  titleInput.title = data.title_locked
    ? `配置文件里的 render.title 优先：「${data.title_locked}」`
    : "";
  $("intro").value = data.intro;

  const link = $("tutorial-link");
  link.hidden = !data.tutorial.exists;
  link.href = "/tutorial.html";

  renderSectionList();
  renderEditor();
  renderThumbs();
  renderPool();
  renderUsage();

  if (data.warnings && data.warnings.length) showBanner(data.warnings, "warn");
  if (data.render) {
    showStatus(
      `已渲染 · ${fmtSize(data.render.size)} · ${data.render.elapsed}s`,
      "ok",
    );
  }
  if (data.rewrite) {
    const delta = data.rewrite.delta >= 0 ? `+${data.rewrite.delta}` : `${data.rewrite.delta}`;
    showStatus(
      `第 ${data.rewrite.section} 节已重写 · ${delta} 字 · 已重新渲染`,
      "ok",
    );
  }
}

function renderSectionList() {
  const list = $("section-list");
  list.replaceChildren();
  for (const section of state.sections) {
    const active = section.index === state.currentIndex;
    const button = h(
      "button",
      { type: "button", onclick: () => selectSection(section.index) },
      h("span", { class: "num", text: String(section.index).padStart(2, "0") }),
      h("span", { class: "label", text: section.title }),
      h("span", { class: "meta", text: `${section.chars}字` }),
      section.overridden ? h("span", { class: "badge", title: "有人工修改", text: "✓" }) : null,
    );
    list.append(h("li", { class: active ? "active" : "" }, button));
  }
  $("section-count").textContent = `${state.sections.length} 节`;
}

function renderEditor() {
  const section = current();
  if (!section) return;
  $("section-title").value = section.title;
  $("markdown").value = section.markdown;
  $("section-time").textContent = `${section.start_hms} – ${section.end_hms}`;
  $("section-chars").textContent = `${section.chars} 字`;
  updateMeta();
  if (state.preview) refreshPreview();
}

function updateMeta() {
  const section = current();
  if (!section) return;
  $("section-chars").textContent = `${countChars(section.markdown)} 字`;
}

function selectSection(index) {
  if (state.busy) return;
  state.currentIndex = index;
  renderSectionList();
  renderEditor();
  renderThumbs();
  renderPool();
}

function renderUsage() {
  const usage = state.data ? state.data.usage : null;
  if (!usage) {
    $("usage").textContent = "";
    return;
  }
  const tokens = (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
  $("usage").textContent = `本任务 LLM ${usage.calls || 0} 次 · ${(tokens / 1000).toFixed(1)}k tokens`;
}

/* ------------------------------------------------------------------ 画面 */

function renderThumbs() {
  const section = current();
  const box = $("thumbs");
  box.replaceChildren();
  if (!section) return;

  $("image-count").textContent = `${section.images.length} 张`;
  if (!section.images.length) {
    box.append(h("div", { class: "empty", text: "这一节还没有配图，可从下方借用" }));
    return;
  }

  section.images.forEach((image, position) => {
    const card = h(
      "figure",
      { class: "thumb" },
      h("img", { src: image.url, alt: `画面 ${position + 1}`, loading: "lazy" }),
      h(
        "figcaption",
        {},
        h("b", { text: String(position + 1) }),
        h("span", { text: image.hms }),
      ),
      h(
        "div",
        { class: "thumb-actions" },
        h(
          "button",
          {
            type: "button",
            title: `在光标处插入 [[img:${position + 1}]]`,
            onclick: () => insertToken(position + 1),
          },
          "插入",
        ),
        h(
          "button",
          {
            type: "button",
            disabled: position === 0,
            title: "前移（会改变 [[img:N]] 的指向）",
            onclick: () => moveImage(position, -1),
          },
          "↑",
        ),
        h(
          "button",
          {
            type: "button",
            disabled: position === section.images.length - 1,
            title: "后移（会改变 [[img:N]] 的指向）",
            onclick: () => moveImage(position, 1),
          },
          "↓",
        ),
        h(
          "button",
          { type: "button", title: "从本节移除", onclick: () => removeImage(position) },
          "✕",
        ),
      ),
    );
    if (image.caption) {
      card.append(h("div", { class: "caption", text: image.caption }));
    }
    box.append(card);
  });
}

function renderPool() {
  const section = current();
  const box = $("pool");
  box.replaceChildren();
  if (!section || !state.data) return;

  const owned = new Set(section.images.map((image) => image.image));
  const others = state.data.pool.filter((item) => !owned.has(item.image));
  if (!others.length) {
    box.append(h("div", { class: "tiny", text: "整个文档的图片都已经在这一节了" }));
    return;
  }

  for (const item of others) {
    box.append(
      h(
        "div",
        { class: "pool-item" },
        h("img", { src: item.url, alt: "", loading: "lazy" }),
        h(
          "div",
          { class: "meta" },
          h("div", { text: `第 ${item.section} 节 · ${item.hms}` }),
          h("div", { text: item.caption || item.kind || "" }),
        ),
        h("button", { type: "button", onclick: () => addFromPool(item) }, "加"),
      ),
    );
  }
}

function touchImages() {
  markDirty();
  renderSectionList();
  renderThumbs();
  renderPool();
  if (state.preview) schedulePreview();
}

function moveImage(position, delta) {
  const section = current();
  const target = position + delta;
  if (!section || target < 0 || target >= section.images.length) return;
  const [image] = section.images.splice(position, 1);
  section.images.splice(target, 0, image);
  touchImages();
}

function removeImage(position) {
  const section = current();
  if (!section) return;
  const [image] = section.images.splice(position, 1);
  if (!image) return;
  touchImages();
  showStatus(`已移除画面 ${image.hms}，保存后生效`, "warn");
}

function addFromPool(item) {
  const section = current();
  if (!section) return;
  state.sections.forEach((other) => {
    if (other.index !== section.index) {
      other.images = other.images.filter((image) => image.image !== item.image);
    }
  });
  section.images.push({ ...item, id: section.images.length + 1 });
  touchImages();
  showStatus(`已把 ${item.hms} 的画面加到本节，记得在正文里插 [[img:${section.images.length}]]`, "ok");
}

function insertToken(number) {
  const area = $("markdown");
  const token = `\n\n[[img:${number}]]\n\n`;
  const start = area.selectionStart ?? area.value.length;
  const end = area.selectionEnd ?? start;
  area.setRangeText(token, start, end, "end");
  area.dispatchEvent(new Event("input", { bubbles: true }));
  area.focus();
}

/* ------------------------------------------------------------------ 预览 */

function schedulePreview() {
  if (!state.preview) return;
  window.clearTimeout(previewTimer);
  previewTimer = window.setTimeout(refreshPreview, 500);
}

async function refreshPreview() {
  const section = current();
  if (!section) return;
  const box = $("preview");
  box.innerHTML = '<div class="muted">正在预览…</div>';
  try {
    const data = await api("/api/preview", {
      method: "POST",
      body: JSON.stringify({
        markdown: section.markdown,
        images: section.images.map((image) => image.image),
        title: section.title,
      }),
    });
    box.innerHTML = data.html || '<div class="muted">（空）</div>';
  } catch (error) {
    box.textContent = `预览失败：${error.message}`;
  }
}

function togglePreview(on) {
  state.preview = on;
  $("preview").hidden = !on;
  $("markdown").hidden = on;
  if (on) refreshPreview();
  else $("markdown").focus();
}

/* ------------------------------------------------------------------ 保存 */

function payload() {
  return {
    title: $("doc-title").value,
    intro: $("intro").value,
    sections: state.sections.map((section) => ({
      index: section.index,
      title: section.title,
      markdown: section.markdown,
      images: section.images.map((image) => image.image),
    })),
  };
}

async function save() {
  if (state.saving || state.busy) return;
  state.saving = true;
  setBusy(true);
  showStatus("正在保存并重新渲染…");
  try {
    const data = await api("/api/save", {
      method: "POST",
      body: JSON.stringify(payload()),
    });
    const keep = state.currentIndex;
    adopt(data, { keep });
    showStatus(
      `已保存并渲染 · ${fmtSize(data.render.size)} · ${data.render.elapsed}s`,
      "ok",
    );
  } catch (error) {
    showError(error);
  } finally {
    state.saving = false;
    setBusy(false);
  }
}

async function rewriteSection() {
  const section = current();
  if (!section || state.busy) return;
  const hint = $("hint").value.trim();
  const ok = window.confirm(
    `让模型重写第 ${section.index} 节「${section.title}」？\n\n` +
      "会调用一次模型（花钱），并清除这一节已有的人工修改。\n" +
      "其他章节的修改不受影响。",
  );
  if (!ok) return;

  state.saving = true;
  setBusy(true);
  showStatus(`模型正在重写第 ${section.index} 节，通常要 1–3 分钟…`);
  try {
    const data = await api("/api/rewrite", {
      method: "POST",
      body: JSON.stringify({ section: section.index, hint }),
    });
    adopt(data, { keep: section.index });
    $("hint").value = "";
  } catch (error) {
    showError(error);
  } finally {
    state.saving = false;
    setBusy(false);
  }
}

/* ------------------------------------------------------------------ 启动 */

async function boot() {
  try {
    const data = await api("/api/state");
    adopt(data);
  } catch (error) {
    showError(error);
    return;
  }

  $("doc-title").addEventListener("input", (event) => {
    if (state.data) state.data.title = event.target.value;
    markDirty();
  });

  $("intro").addEventListener("input", (event) => {
    if (state.data) state.data.intro = event.target.value;
    markDirty();
  });

  $("section-title").addEventListener("input", (event) => {
    const section = current();
    if (!section) return;
    section.title = event.target.value;
    markDirty();
    renderSectionList();
    schedulePreview();
  });

  $("markdown").addEventListener("input", (event) => {
    const section = current();
    if (!section) return;
    section.markdown = event.target.value;
    section.chars = countChars(section.markdown);
    markDirty();
    updateMeta();
    schedulePreview();
  });

  $("preview-toggle").addEventListener("change", (event) => {
    togglePreview(event.target.checked);
  });

  $("save").addEventListener("click", save);
  $("rewrite").addEventListener("click", rewriteSection);
  $("banner-close").addEventListener("click", hideBanner);

  window.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      save();
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

boot();
