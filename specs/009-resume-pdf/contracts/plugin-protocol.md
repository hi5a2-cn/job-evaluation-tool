# Plugin Protocol & View Contracts: 插件内部通信与交互规范 (009-resume-pdf)

**Feature**: `009-resume-pdf`
**Scope**: 浏览器插件内部组件（`options.html`、`options.js`、`background.js`、`jet-client.js`）数据交互与视图规范。

---

## 1. 选项页简历交互与 DOM 结构 (`options.html`)

在 `options.html` 的 `#resume-section` 中，每份简历槽位（Slot 1–3）均提供文件上传、名称设置、画像查看与编辑能力。

### 1.1 提示文案与脱敏姓名输入框

- **两处说明文字替换**：
  在 `#resume-section .hint-text` 与 `#strict-industry-section .hint-text` 中，原有文案统一原样替换为：
  ```html
  <div class="hint-text">
    上传时，简历文字会在本机去掉姓名、手机号、邮箱等联系方式后发送给 DeepSeek 生成简历画像；之后岗位判断时只发送画像（编号为简历1/2/3，不发送简历名称）。本机只保存提取出的文字和画像，不保存 PDF 文件。
  </div>
  ```

- **脱敏姓名辅助输入框**：
  在简历列表上方增加可选输入框：
  ```html
  <div class="form-group" style="margin-bottom: 12px;">
    <label for="resume-self-name" style="font-size: 13px;">你的姓名（只在本机用于去掉姓名，不发送）：</label>
    <input type="text" id="resume-self-name" maxlength="20" placeholder="例如：李明" style="max-width: 320px;" />
  </div>
  ```

### 1.2 单份简历项 DOM 结构 (`.resume-item`)

```html
<div class="resume-item" data-slot="1">
  <div class="resume-header">
    <strong class="slot-number">简历 1</strong>
    <button type="button" class="btn-danger resume-delete-btn" style="padding: 4px 8px; font-size: 12px;">删除</button>
  </div>

  <!-- 文件选择区 -->
  <div class="form-group" style="margin-bottom: 8px;">
    <label style="font-size: 12px;">选择简历 PDF：</label>
    <div style="display: flex; gap: 8px; align-items: center;">
      <input type="file" accept="application/pdf" class="resume-file-input" style="font-size: 12px;" />
      <button type="button" class="resume-upload-btn btn-secondary" style="font-size: 12px; white-space: nowrap;" disabled>上传并生成画像</button>
    </div>
    <div class="resume-file-tip" style="font-size: 11px; color: #888; margin-top: 2px;">仅支持文字版 PDF，大小 ≤ 5MB；原文件仅在内存处理不存盘</div>
  </div>

  <!-- 简历名称 -->
  <div class="form-group" style="margin-bottom: 8px;">
    <label style="font-size: 12px;">简历名称（用于在插件中区分，不会发给模型）：</label>
    <input type="text" class="resume-name-input" maxlength="100" placeholder="选择 PDF 后自动生成，例如：后端开发-通用版" />
  </div>

  <!-- 简历画像 -->
  <div class="form-group" style="margin-bottom: 4px;">
    <label style="font-size: 12px;">简历画像（适合方向与亮点，会发给模型，最多 300 字）：</label>
    <textarea class="resume-profile-input" rows="4" maxlength="300" placeholder="上传 PDF 后由 AI 自动生成，也可以手动输入或修改..."></textarea>
    <div class="char-count resume-profile-count">已输入 0 / 300</div>
  </div>

  <!-- 重新生成按钮（仅在已提取文字时显示） -->
  <div class="resume-actions-row" style="margin-top: 4px; display: flex; gap: 8px;">
    <button type="button" class="resume-regen-btn btn-link" style="font-size: 12px; display: none;">重新生成画像</button>
  </div>
</div>
```

---

## 2. 客户端网络封装规范 (`jet-client.js`)

在 `jet-client.js` 中新增与更新与简历管理相关的方法：

```javascript
// extension/src/jet-client.js

// 1. 上传 PDF 并生成画像
async uploadResume(payload) {
  // payload: { slot, name, pdf_base64, self_name }（不含原始文件名）
  return this.call("POST", "/v1/resumes/upload", payload);
},

// 2. 重新生成指定 slot 的画像
async regenerateResume(slot, payload = {}) {
  // payload: { self_name }
  return this.call("POST", `/v1/resumes/${slot}/regenerate`, payload);
},

// 3. 获取所有简历列表（不含全文）
async getResumes() {
  return this.call("GET", "/v1/resumes");
},

// 4. 保存简历修改（仅更新 name 与 profile）
async putResumes(items) {
  return this.call("PUT", "/v1/resumes", { items });
},

// 5. 删除指定 slot 简历
async deleteResume(slot) {
  return this.call("DELETE", `/v1/resumes/${slot}`);
}
```

---

## 3. 后台消息调度规范 (`background.js`)

`background.js` 作为选项页与 `jetClient` 的中继层，处理以下消息类型：

| 消息类型 (`message.type`) | 载荷参数 (`message`) | 后台行为 | 返回响应 |
|---|---|---|---|
| `get_resumes` | 无 | 调用 `jetClient.getResumes()` | `{ ok: true, data: [...] }` |
| `upload_resume` | `{ slot, name, pdf_base64, self_name }` | 调用 `jetClient.uploadResume(...)` | `{ ok: true, data: {...} }` 或错误响应 |
| `regenerate_resume` | `{ slot, self_name }` | 调用 `jetClient.regenerateResume(slot, ...)` | `{ ok: true, data: {...} }` 或错误响应 |
| `put_resumes` / `save_resumes` | `{ items }` | 调用 `jetClient.putResumes(items)` | `{ ok: true, count: N }` |
| `delete_resume` | `{ slot }` | 调用 `jetClient.deleteResume(slot)` | `{ ok: true, slot: N }` |

---

## 4. 选项页行为交互与前端安全契约 (`options.js`)

### 4.1 文件选择与名称联动

1. 当用户点击文件输入框并选择 PDF 文件时：
   - 检查 `file.size` 是否超过 $5\text{MB}$（$5 \times 1024 \times 1024$ 字节）。若超限，立即清空输入并报错提示"文件大小不能超过 5MB"；
   - 提取文件名：若以 `.pdf` 结尾，自动切除后缀（如 `李四-Java架构师.pdf` -> `李四-Java架构师`）；
   - 将切除后缀的文件名自动填入对应的 `.resume-name-input`；
   - 启用"上传并生成画像"按钮。

### 4.2 Base64 编码与上传

1. 用户点击"上传并生成画像"：
   - 禁用上传按钮，按钮文字切换为"正在解析与生成..."；
   - 使用 `FileReader.readAsDataURL(file)` 读取文件；
   - 截取字符串 `result.split(',')[1]` 获取纯 Base64 编码；
   - 读取可选的姓名输入框 `#resume-self-name` 的值（若非空则去除空白）；姓名不写入浏览器存储，本机 Jet 也不保存（2026-10-05 修订）；
   - 发送 `upload_resume` 消息至后台；
   - 若成功返回：将画像填入文本框，更新字数统计，显示成功消息，展示"重新生成"按钮；
   - 若失败返回：显示具体服务端错误（如"无法读取文字，请上传文字版 PDF"）。

### 4.3 画像字数统计实时联动

文本框绑定 `input` 事件，计算当前字符长度：
- 格式：`已输入 ${len} / 300`；
- 当 $len > 300$ 时，添加 `.over-limit` 类高亮红色警示；
- 点击"保存修改"时，前端进行拦截：长度为 0 或大于 300 时阻断保存。
