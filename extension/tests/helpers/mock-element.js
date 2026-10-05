// 聊天页读取测试共用的简化页面元素（chat-reader.test.js、chat-job-ingest.test.js；体检第 77 条：原来两份逐行相同）
export class MockElement {
  constructor({
    tagName = "div",
    className = "",
    textContent = "",
    vue = null,
    visible = true,
  } = {}) {
    this.tagName = tagName.toUpperCase();
    this.className = className;
    this.textContent = textContent;
    this.__vue__ = vue;
    this.visible = visible;
    this.parentElement = null;
    this.children = [];
    this.style = {
      display: visible ? "block" : "none",
      visibility: visible ? "visible" : "hidden",
    };
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  getClientRects() {
    return this.visible ? [{ width: 100, height: 20 }] : [];
  }
}
