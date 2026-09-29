/* ==========================================================================
   档案装盒编目与库位盘点系统 — 前端逻辑
   ========================================================================== */

// ---------------------------------------------------------------------------
// 全局状态
// ---------------------------------------------------------------------------

const state = {
  currentView: "catalog",
  currentUser: "admin",
  currentRole: "管理员",
  fonds: [],
  files: [],
  boxes: [],
  warehouses: [],
  shelves: [],
  transfers: [],
  inventoryTasks: [],
  differences: [],
  borrowings: [],
};

// ---------------------------------------------------------------------------
// API 封装
// ---------------------------------------------------------------------------

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-Username": state.currentUser,
    ...(options.headers || {}),
  };
  const res = await fetch(path, { ...options, headers });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || `请求失败 (${res.status})`);
  }
  return data;
}

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

function esc(str) {
  if (str === null || str === undefined) return "";
  const div = document.createElement("div");
  div.textContent = String(str);
  return div.innerHTML;
}

function formatDate(dateStr) {
  if (!dateStr) return "—";
  return dateStr;
}

function statusBadge(status) {
  const map = {
    "在库": "badge-success",
    "借出": "badge-warning",
    "移位中": "badge-info",
    "待执行": "badge-info",
    "已确认": "badge-success",
    "中断": "badge-danger",
    "待开始": "badge-secondary",
    "进行中": "badge-info",
    "已完成": "badge-success",
    "已冻结": "badge-teal",
    "待处理": "badge-warning",
    "已处理": "badge-success",
    "已补录": "badge-teal",
    "待审批": "badge-warning",
    "已批准": "badge-success",
    "已拒绝": "badge-danger",
    "已归还": "badge-secondary",
    "超期": "badge-danger",
    "公开": "badge-secondary",
    "内部": "badge-info",
    "秘密": "badge-warning",
    "机密": "badge-danger",
    "***": "badge-secondary",
  };
  const cls = map[status] || "badge-secondary";
  return `<span class="badge ${cls}">${esc(status)}</span>`;
}

function mono(text) {
  return `<span class="mono" title="点击复制">${esc(text)}</span>`;
}

function showAlert(message, type = "info") {
  const main = document.getElementById("main-content");
  const alert = document.createElement("div");
  alert.className = `alert alert-${type}`;
  alert.textContent = message;
  main.insertBefore(alert, main.firstChild);
  setTimeout(() => alert.remove(), 4000);
}

function openModal(title, bodyHtml, footerHtml = "") {
  const overlay = document.getElementById("modal-overlay");
  const content = document.getElementById("modal-content");
  content.innerHTML = `
    <div class="modal-header">
      <h3>${esc(title)}</h3>
      <button class="modal-close" onclick="closeModal()">×</button>
    </div>
    <div class="modal-body">${bodyHtml}</div>
    ${footerHtml ? `<div class="modal-footer">${footerHtml}</div>` : ""}
  `;
  overlay.classList.add("active");
}

function closeModal() {
  document.getElementById("modal-overlay").classList.remove("active");
}

// 点击模态框外部关闭
document.getElementById("modal-overlay").addEventListener("click", (e) => {
  if (e.target.id === "modal-overlay") closeModal();
});

// 复制档号
document.addEventListener("click", (e) => {
  if (e.target.classList.contains("mono")) {
    const text = e.target.textContent;
    navigator.clipboard.writeText(text).then(() => {
      const orig = e.target.textContent;
      e.target.textContent = "已复制!";
      setTimeout(() => { e.target.textContent = orig; }, 1000);
    });
  }
});

// ---------------------------------------------------------------------------
// 用户切换
// ---------------------------------------------------------------------------

async function switchUser() {
  const select = document.getElementById("user-select");
  state.currentUser = select.value;
  // 获取角色
  try {
    const users = await api("/api/fonds"); // dummy call to check
    // 从 localStorage 或直接查询
    const res = await fetch("/api/fonds", { headers: { "X-Username": state.currentUser } });
    // 简化：直接根据用户名推断角色
    const roleMap = { admin: "管理员", archivist: "档案员", reader: "查阅者" };
    state.currentRole = roleMap[state.currentUser] || "查阅者";
    document.getElementById("current-role").textContent = state.currentRole;
  } catch (e) {
    state.currentRole = "查阅者";
  }
  // 刷新当前视图
  switchView(state.currentView);
}

// ---------------------------------------------------------------------------
// 视图切换
// ---------------------------------------------------------------------------

function switchView(view) {
  state.currentView = view;
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === view);
  });
  const main = document.getElementById("main-content");
  main.innerHTML = `<div class="loading"></div>`;
  const renderers = {
    catalog: renderCatalog,
    boxing: renderBoxing,
    locations: renderLocations,
    inventory: renderInventory,
    borrowing: renderBorrowing,
    differences: renderDifferences,
  };
  renderers[view]().catch((err) => {
    main.innerHTML = `<div class="alert alert-danger">加载失败: ${esc(err.message)}</div>`;
  });
}

// ---------------------------------------------------------------------------
// 案卷编目视图
// ---------------------------------------------------------------------------

async function renderCatalog() {
  const main = document.getElementById("main-content");
  const [fonds, files] = await Promise.all([
    api("/api/fonds"),
    api("/api/files"),
  ]);
  state.fonds = fonds;
  state.files = files;

  const fondsOptions = fonds.map(
    (f) => `<option value="${f.id}">${esc(f.code)} — ${esc(f.name)}</option>`
  ).join("");

  main.innerHTML = `
    <div class="page-header">
      <h2>案卷编目</h2>
      <p class="subtitle">管理全宗、案卷、档号与密级。档号全局唯一，不可重复。</p>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>全宗列表</h3>
        <button class="btn btn-primary btn-sm" onclick="showCreateFondsModal()">+ 新建全宗</button>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr><th>全宗代码</th><th>全宗名称</th><th>创建时间</th></tr>
          </thead>
          <tbody>
            ${fonds.length ? fonds.map((f) => `
              <tr>
                <td>${esc(f.code)}</td>
                <td>${esc(f.name)}</td>
                <td>${formatDate(f.created_at)}</td>
              </tr>
            `).join("") : `<tr><td colspan="3" class="empty-state">暂无全宗数据</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>案卷列表</h3>
        <button class="btn btn-primary btn-sm" onclick="showCreateFileModal()">+ 新建案卷</button>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>档号</th><th>题名</th><th>年度</th><th>保管期限</th>
              <th>密级</th><th>全宗</th><th>状态</th>
            </tr>
          </thead>
          <tbody>
            ${files.length ? files.map((f) => `
              <tr>
                <td>${mono(f.archive_number)}</td>
                <td>${esc(f.title)}</td>
                <td>${f.year}</td>
                <td>${statusBadge(f.retention_period)}</td>
                <td>${statusBadge(f.security_level)}</td>
                <td>${esc(fonds.find((x) => x.id === f.fonds_id)?.code || "—")}</td>
                <td>${f.box_id ? statusBadge("已装盒") : statusBadge("未装盒")}</td>
              </tr>
            `).join("") : `<tr><td colspan="7" class="empty-state">暂立案卷数据</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function showCreateFondsModal() {
  openModal("新建全宗", `
    <div class="form-group">
      <label>全宗代码</label>
      <input type="text" class="form-control" id="fonds-code" placeholder="例如：A001">
    </div>
    <div class="form-group">
      <label>全宗名称</label>
      <input type="text" class="form-control" id="fonds-name" placeholder="例如：中共中央办公厅">
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitCreateFonds()">创建</button>
  `);
}

async function submitCreateFonds() {
  const code = document.getElementById("fonds-code").value.trim();
  const name = document.getElementById("fonds-name").value.trim();
  if (!code || !name) {
    showAlert("全宗代码和名称不能为空", "warning");
    return;
  }
  try {
    await api("/api/fonds", { method: "POST", body: JSON.stringify({ code, name }) });
    closeModal();
    showAlert("全宗创建成功", "success");
    switchView("catalog");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function showCreateFileModal() {
  const fonds = await api("/api/fonds");
  if (!fonds.length) {
    showAlert("请先创建全宗", "warning");
    return;
  }
  const fondsOptions = fonds.map(
    (f) => `<option value="${f.id}">${esc(f.code)} — ${esc(f.name)}</option>`
  ).join("");
  openModal("新建案卷", `
    <div class="form-row">
      <div class="form-group">
        <label>全宗</label>
        <select class="form-control" id="file-fonds">${fondsOptions}</select>
      </div>
      <div class="form-group">
        <label>档号</label>
        <input type="text" class="form-control" id="file-archive-number" placeholder="例如：A001-2024-001">
      </div>
    </div>
    <div class="form-group">
      <label>题名</label>
      <input type="text" class="form-control" id="file-title" placeholder="案卷题名">
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>年度</label>
        <input type="number" class="form-control" id="file-year" value="2024">
      </div>
      <div class="form-group">
        <label>保管期限</label>
        <select class="form-control" id="file-retention">
          <option value="永久">永久</option>
          <option value="30年">30年</option>
          <option value="10年">10年</option>
        </select>
      </div>
      <div class="form-group">
        <label>密级</label>
        <select class="form-control" id="file-security">
          <option value="公开">公开</option>
          <option value="内部">内部</option>
          <option value="秘密">秘密</option>
          <option value="机密">机密</option>
        </select>
      </div>
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitCreateFile()">创建</button>
  `);
}

async function submitCreateFile() {
  const fonds_id = parseInt(document.getElementById("file-fonds").value);
  const archive_number = document.getElementById("file-archive-number").value.trim();
  const title = document.getElementById("file-title").value.trim();
  const year = parseInt(document.getElementById("file-year").value);
  const retention_period = document.getElementById("file-retention").value;
  const security_level = document.getElementById("file-security").value;
  if (!archive_number || !title) {
    showAlert("档号和题名不能为空", "warning");
    return;
  }
  try {
    await api("/api/files", {
      method: "POST",
      body: JSON.stringify({ fonds_id, archive_number, title, year, retention_period, security_level }),
    });
    closeModal();
    showAlert("案卷创建成功", "success");
    switchView("catalog");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

// ---------------------------------------------------------------------------
// 装盒清单视图
// ---------------------------------------------------------------------------

async function renderBoxing() {
  const main = document.getElementById("main-content");
  const [boxes, files] = await Promise.all([
    api("/api/boxes"),
    api("/api/files?unboxed=1"),
  ]);
  state.boxes = boxes;

  // 按年度+保管期限分组未装盒案卷
  const groups = {};
  files.forEach((f) => {
    const key = `${f.year}-${f.retention_period}`;
    if (!groups[key]) groups[key] = { year: f.year, retention: f.retention_period, files: [] };
    groups[key].files.push(f);
  });

  const groupKeys = Object.keys(groups).sort();

  main.innerHTML = `
    <div class="page-header">
      <h2>装盒清单</h2>
      <p class="subtitle">按年度和保管期限生成装盒建议，盒内档号必须连续且不可重复。</p>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>装盒建议</h3>
        <button class="btn btn-primary btn-sm" onclick="showAutoBoxModal()">+ 自动装盒</button>
      </div>
      ${groupKeys.length ? groupKeys.map((key) => {
        const g = groups[key];
        return `
          <div class="suggestion-group">
            <div class="suggestion-group-header">
              <span class="suggestion-group-title">${g.year}年 · ${g.retention}（${g.files.length} 件未装盒）</span>
              <button class="btn btn-secondary btn-sm" onclick="suggestBoxing(${g.year}, '${g.retention}')">生成建议</button>
            </div>
            <div class="suggestion-files">
              ${g.files.map((f) => `<span class="suggestion-file" title="${esc(f.title)}">${esc(f.archive_number)}</span>`).join("")}
            </div>
          </div>
        `;
      }).join("") : `<div class="empty-state">暂无未装盒案卷</div>`}
    </div>

    <div class="card">
      <div class="card-header">
        <h3>已装盒列表</h3>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr><th>盒号</th><th>年度</th><th>保管期限</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            ${boxes.length ? boxes.map((b) => `
              <tr>
                <td>${mono(b.box_number)}</td>
                <td>${b.year}</td>
                <td>${statusBadge(b.retention_period)}</td>
                <td>${statusBadge(b.status)}</td>
                <td>
                  <button class="btn btn-secondary btn-sm" onclick="viewBoxDetail(${b.id})">查看</button>
                </td>
              </tr>
            `).join("") : `<tr><td colspan="5" class="empty-state">暂无装盒数据</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

async function suggestBoxing(year, retention) {
  try {
    const suggestions = await api(`/api/boxing/suggest?year=${year}&retention_period=${encodeURIComponent(retention)}`);
    if (!suggestions.length) {
      showAlert("暂无装盒建议", "info");
      return;
    }
    let html = suggestions.map((s) => `
      <div class="suggestion-group">
        <div class="suggestion-group-header">
          <span class="suggestion-group-title">建议盒 ${s.box_index}（${s.file_count} 件）</span>
          <button class="btn btn-primary btn-sm" onclick="applySuggestion(${s.year}, '${s.retention_period}', ${JSON.stringify(s.archive_numbers).replace(/"/g, '"')})">采用此建议</button>
        </div>
        <div class="suggestion-files">
          ${s.archive_numbers.map((n) => `<span class="suggestion-file">${esc(n)}</span>`).join("")}
        </div>
      </div>
    `).join("");
    openModal(`装盒建议 — ${year}年 · ${retention}`, html, `
      <button class="btn btn-secondary" onclick="closeModal()">关闭</button>
    `);
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function applySuggestion(year, retention, archiveNumbers) {
  try {
    // 获取未装盒案卷
    const files = await api("/api/files?unboxed=1");
    const selected = files.filter((f) => archiveNumbers.includes(f.archive_number));
    if (selected.length !== archiveNumbers.length) {
      showAlert("部分案卷不可用", "warning");
      return;
    }
    const boxNumber = `BOX-${year}-${retention.replace("年", "")}-${String(Date.now()).slice(-4)}`;
    await api("/api/boxes", {
      method: "POST",
      body: JSON.stringify({
        box_number: boxNumber,
        year,
        retention_period: retention,
        file_ids: selected.map((f) => f.id),
      }),
    });
    closeModal();
    showAlert(`装盒成功：${boxNumber}`, "success");
    switchView("boxing");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

function showAutoBoxModal() {
  openModal("自动装盒", `
    <div class="alert alert-info">
      系统将按年度和保管期限自动分组未装盒案卷，生成装盒建议。盒内档号必须连续，每盒最多 20 件。
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>年度</label>
        <input type="number" class="form-control" id="auto-year" value="2024">
      </div>
      <div class="form-group">
        <label>保管期限</label>
        <select class="form-control" id="auto-retention">
          <option value="永久">永久</option>
          <option value="30年">30年</option>
          <option value="10年">10年</option>
        </select>
      </div>
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="suggestBoxing(document.getElementById('auto-year').value, document.getElementById('auto-retention').value)">生成建议</button>
  `);
}

async function viewBoxDetail(boxId) {
  try {
    const box = await api(`/api/boxes/${boxId}`);
    const filesHtml = box.files.length ? box.files.map((f) => `
      <tr>
        <td>${mono(f.archive_number)}</td>
        <td>${esc(f.title)}</td>
        <td>${f.year}</td>
        <td>${statusBadge(f.retention_period)}</td>
        <td>${statusBadge(f.security_level)}</td>
      </tr>
    `).join("") : `<tr><td colspan="5" class="empty-state">空盒</td></tr>`;
    openModal(`盒详情 — ${box.box_number}`, `
      <div class="mb-2">
        <strong>盒号：</strong> ${mono(box.box_number)}<br>
        <strong>年度：</strong> ${box.year}
        <strong>保管期限：</strong> ${statusBadge(box.retention_period)}
        <strong>状态：</strong> ${statusBadge(box.status)}
      </div>
      <h4 class="mb-1">盒内案卷（${box.files.length} 件）</h4>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>档号</th><th>题名</th><th>年度</th><th>保管期限</th><th>密级</th></tr></thead>
          <tbody>${filesHtml}</tbody>
        </table>
      </div>
    `, `
      <button class="btn btn-secondary" onclick="closeModal()">关闭</button>
    `);
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

// ---------------------------------------------------------------------------
// 库位地图视图
// ---------------------------------------------------------------------------

async function renderLocations() {
  const main = document.getElementById("main-content");
  const [warehouses, locations] = await Promise.all([
    api("/api/warehouses"),
    api("/api/locations"),
  ]);
  state.warehouses = warehouses;

  const whOptions = warehouses.map(
    (w) => `<option value="${w.id}">${esc(w.code)} — ${esc(w.name)}</option>`
  ).join("");

  main.innerHTML = `
    <div class="page-header">
      <h2>库位地图</h2>
      <p class="subtitle">货架网格展示，档号等宽字体可复制。密级档案库位按岗位脱敏。</p>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>库房列表</h3>
        <button class="btn btn-primary btn-sm" onclick="showCreateWarehouseModal()">+ 新建库房</button>
      </div>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>库房代码</th><th>库房名称</th><th>操作</th></tr></thead>
          <tbody>
            ${warehouses.length ? warehouses.map((w) => `
              <tr>
                <td>${esc(w.code)}</td>
                <td>${esc(w.name)}</td>
                <td><button class="btn btn-secondary btn-sm" onclick="viewWarehouse(${w.id})">查看库位</button></td>
              </tr>
            `).join("") : `<tr><td colspan="3" class="empty-state">暂无库房数据</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>货架网格</h3>
        <div class="flex gap-2 items-center">
          <select class="form-control" id="location-wh-filter" style="width:auto;" onchange="filterLocations()">
            <option value="">全部库房</option>
            ${whOptions}
          </select>
          <button class="btn btn-primary btn-sm" onclick="showCreateShelfModal()">+ 新建货架</button>
        </div>
      </div>
      <div id="location-grid" class="warehouse-grid">
        ${renderLocationGrid(locations)}
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>入库操作</h3>
      </div>
      <div class="alert alert-info">
        选择一个在库状态的盒，绑定库房、货架和层位。调架需先生成移位单。
      </div>
      <button class="btn btn-primary" onclick="showEnterWarehouseModal()">入库绑定库位</button>
    </div>
  `;
}

function renderLocationGrid(locations) {
  if (!locations.length) {
    return `<div class="empty-state">暂无货架数据</div>`;
  }
  return locations.map((shelf) => `
    <div class="shelf-card">
      <div class="shelf-card-header">
        <span>${esc(shelf.code)}</span>
        <span class="text-muted" style="font-size:12px;font-weight:normal;">${shelf.layers_count} 层</span>
      </div>
      <div class="shelf-card-body">
        ${shelf.layers.map((l) => `
          <div class="layer-row">
            <span class="layer-label">L${l.layer}</span>
            ${l.box ? `
              <span class="layer-box occupied" title="${esc(l.box.box_number)}">${esc(l.box.box_number)}</span>
            ` : `
              <span class="layer-box empty">空</span>
            `}
          </div>
        `).join("")}
      </div>
    </div>
  `).join("");
}

async function filterLocations() {
  const whId = document.getElementById("location-wh-filter").value;
  const url = whId ? `/api/locations?warehouse_id=${whId}` : "/api/locations";
  const locations = await api(url);
  document.getElementById("location-grid").innerHTML = renderLocationGrid(locations);
}

async function viewWarehouse(whId) {
  document.getElementById("location-wh-filter").value = whId;
  await filterLocations();
}

function showCreateWarehouseModal() {
  openModal("新建库房", `
    <div class="form-group">
      <label>库房代码</label>
      <input type="text" class="form-control" id="wh-code" placeholder="例如：WH-A">
    </div>
    <div class="form-group">
      <label>库房名称</label>
      <input type="text" class="form-control" id="wh-name" placeholder="例如：第一库房">
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitCreateWarehouse()">创建</button>
  `);
}

async function submitCreateWarehouse() {
  const code = document.getElementById("wh-code").value.trim();
  const name = document.getElementById("wh-name").value.trim();
  if (!code || !name) {
    showAlert("库房代码和名称不能为空", "warning");
    return;
  }
  try {
    await api("/api/warehouses", { method: "POST", body: JSON.stringify({ code, name }) });
    closeModal();
    showAlert("库房创建成功", "success");
    switchView("locations");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function showCreateShelfModal() {
  const warehouses = await api("/api/warehouses");
  if (!warehouses.length) {
    showAlert("请先创建库房", "warning");
    return;
  }
  const whOptions = warehouses.map(
    (w) => `<option value="${w.id}">${esc(w.code)} — ${esc(w.name)}</option>`
  ).join("");
  openModal("新建货架", `
    <div class="form-group">
      <label>所属库房</label>
      <select class="form-control" id="shelf-wh">${whOptions}</select>
    </div>
    <div class="form-group">
      <label>货架代码</label>
      <input type="text" class="form-control" id="shelf-code" placeholder="例如：S01">
    </div>
    <div class="form-group">
      <label>层数</label>
      <input type="number" class="form-control" id="shelf-layers" value="5" min="1">
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitCreateShelf()">创建</button>
  `);
}

async function submitCreateShelf() {
  const warehouse_id = parseInt(document.getElementById("shelf-wh").value);
  const code = document.getElementById("shelf-code").value.trim();
  const layers_count = parseInt(document.getElementById("shelf-layers").value);
  if (!code) {
    showAlert("货架代码不能为空", "warning");
    return;
  }
  try {
    await api("/api/shelves", {
      method: "POST",
      body: JSON.stringify({ warehouse_id, code, layers_count }),
    });
    closeModal();
    showAlert("货架创建成功", "success");
    switchView("locations");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function showEnterWarehouseModal() {
  const [warehouses, boxes] = await Promise.all([
    api("/api/warehouses"),
    api("/api/boxes"),
  ]);
  const inStockBoxes = boxes.filter((b) => b.status === "在库");
  if (!inStockBoxes.length) {
    showAlert("没有在库状态的盒", "warning");
    return;
  }
  const boxOptions = inStockBoxes.map(
    (b) => `<option value="${b.id}">${esc(b.box_number)} (${b.year}年·${b.retention_period})</option>`
  ).join("");
  const whOptions = warehouses.map(
    (w) => `<option value="${w.id}">${esc(w.code)} — ${esc(w.name)}</option>`
  ).join("");
  openModal("入库绑定库位", `
    <div class="form-group">
      <label>选择盒</label>
      <select class="form-control" id="enter-box">${boxOptions}</select>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>库房</label>
        <select class="form-control" id="enter-wh" onchange="loadShelvesForEnter()">${whOptions}</select>
      </div>
      <div class="form-group">
        <label>货架</label>
        <select class="form-control" id="enter-shelf"></select>
      </div>
      <div class="form-group">
        <label>层位</label>
        <input type="number" class="form-control" id="enter-layer" value="1" min="1">
      </div>
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitEnterWarehouse()">确认入库</button>
  `);
  loadShelvesForEnter();
}

async function loadShelvesForEnter() {
  const whId = document.getElementById("enter-wh").value;
  const shelves = await api(`/api/shelves?warehouse_id=${whId}`);
  const options = shelves.map(
    (s) => `<option value="${s.id}">${esc(s.code)} (${s.layers_count}层)</option>`
  ).join("");
  document.getElementById("enter-shelf").innerHTML = options;
}

async function submitEnterWarehouse() {
  const box_id = parseInt(document.getElementById("enter-box").value);
  const warehouse_id = parseInt(document.getElementById("enter-wh").value);
  const shelf_id = parseInt(document.getElementById("enter-shelf").value);
  const layer = parseInt(document.getElementById("enter-layer").value);
  try {
    await api("/api/warehouse/enter", {
      method: "POST",
      body: JSON.stringify({ box_id, warehouse_id, shelf_id, layer }),
    });
    closeModal();
    showAlert("入库成功", "success");
    switchView("locations");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

// ---------------------------------------------------------------------------
// 盘点任务视图
// ---------------------------------------------------------------------------

async function renderInventory() {
  const main = document.getElementById("main-content");
  const tasks = await api("/api/inventory/tasks");
  state.inventoryTasks = tasks;

  main.innerHTML = `
    <div class="page-header">
      <h2>盘点任务</h2>
      <p class="subtitle">创建盘点任务，记录盘点结果。盘点结果确认后冻结，补录需注明依据。</p>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>盘点任务列表</h3>
        <button class="btn btn-primary btn-sm" onclick="showCreateInventoryTaskModal()">+ 新建盘点任务</button>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr><th>任务名称</th><th>状态</th><th>创建人</th><th>创建时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            ${tasks.length ? tasks.map((t) => `
              <tr>
                <td>${esc(t.name)}</td>
                <td>${statusBadge(t.status)}</td>
                <td>${esc(t.created_by)}</td>
                <td>${formatDate(t.created_at)}</td>
                <td>
                  <button class="btn btn-secondary btn-sm" onclick="viewInventoryTask(${t.id})">查看</button>
                  ${t.status === "待开始" ? `<button class="btn btn-primary btn-sm" onclick="startInventoryTask(${t.id})">开始</button>` : ""}
                  ${t.status === "进行中" ? `<button class="btn btn-primary btn-sm" onclick="showRecordResultModal(${t.id})">记录结果</button>` : ""}
                  ${t.status === "已完成" ? `<button class="btn btn-primary btn-sm" onclick="freezeInventoryTask(${t.id})">冻结</button>` : ""}
                </td>
              </tr>
            `).join("") : `<tr><td colspan="5" class="empty-state">暂无盘点任务</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function showCreateInventoryTaskModal() {
  openModal("新建盘点任务", `
    <div class="form-group">
      <label>任务名称</label>
      <input type="text" class="form-control" id="task-name" placeholder="例如：2024年度第一季度盘点">
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitCreateInventoryTask()">创建</button>
  `);
}

async function submitCreateInventoryTask() {
  const name = document.getElementById("task-name").value.trim();
  if (!name) {
    showAlert("任务名称不能为空", "warning");
    return;
  }
  try {
    await api("/api/inventory/tasks", { method: "POST", body: JSON.stringify({ name }) });
    closeModal();
    showAlert("盘点任务创建成功", "success");
    switchView("inventory");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function startInventoryTask(taskId) {
  try {
    await api(`/api/inventory/tasks/${taskId}/start`, { method: "POST" });
    showAlert("盘点任务已开始", "success");
    switchView("inventory");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function freezeInventoryTask(taskId) {
  try {
    await api(`/api/inventory/tasks/${taskId}/freeze`, { method: "POST" });
    showAlert("盘点任务已冻结，记录不可修改", "success");
    switchView("inventory");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function viewInventoryTask(taskId) {
  try {
    const [task, summary] = await Promise.all([
      api(`/api/inventory/tasks/${taskId}`),
      api(`/api/inventory/tasks/${taskId}/summary`),
    ]);
    const recordsHtml = task.records.length ? task.records.map((r) => `
      <tr>
        <td>${mono(r.box_id)}</td>
        <td>${statusBadge(r.result)}</td>
        <td>${r.actual_warehouse_id || "—"}</td>
        <td>${r.actual_shelf_id || "—"}</td>
        <td>${r.actual_layer || "—"}</td>
        <td>${esc(r.notes || "—")}</td>
      </tr>
    `).join("") : `<tr><td colspan="6" class="empty-state">暂无盘点记录</td></tr>`;

    const locationHtml = summary.by_location.length ? summary.by_location.map((loc) => `
      <div class="diff-location-group">
        <div class="diff-location-header">${esc(loc.location)}</div>
        <div class="diff-location-stats">
          <span class="badge badge-secondary">共 ${loc.total}</span>
          ${Object.entries(loc.results).map(([k, v]) =>
            `<span class="badge ${k === "在库" ? "badge-success" : "badge-danger"}">${k} ${v}</span>`
          ).join("")}
        </div>
      </div>
    `).join("") : `<div class="empty-state">暂无盘点数据</div>`;

    openModal(`盘点任务 — ${task.name}`, `
      <div class="mb-2">
        <strong>状态：</strong> ${statusBadge(task.status)}
        <strong>创建人：</strong> ${esc(task.created_by)}
        <strong>创建时间：</strong> ${formatDate(task.created_at)}
      </div>
      <div class="mb-2">
        <strong>总计录：</strong> ${summary.total_records}
        ${Object.entries(summary.result_counts).map(([k, v]) =>
          `<span class="badge ${k === "在库" ? "badge-success" : "badge-danger"}">${k} ${v}</span>`
        ).join(" ")}
      </div>
      <h4 class="mb-1">按库位聚合</h4>
      ${locationHtml}
      <h4 class="mb-1 mt-2">盘点记录明细</h4>
      <div class="table-wrapper">
        <table>
          <thead><tr><th>盒ID</th><th>结果</th><th>实际库房</th><th>实际货架</th><th>实际层位</th><th>备注</th></tr></thead>
          <tbody>${recordsHtml}</tbody>
        </table>
      </div>
    `, `
      <button class="btn btn-secondary" onclick="closeModal()">关闭</button>
      ${task.status === "已冻结" ? `<button class="btn btn-primary" onclick="showSupplementModal(${task.id})">补录</button>` : ""}
    `);
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function showRecordResultModal(taskId) {
  const boxes = await api("/api/boxes");
  const inStockBoxes = boxes.filter((b) => b.status === "在库" || b.status === "借出");
  if (!inStockBoxes.length) {
    showAlert("没有可盘点的盒", "warning");
    return;
  }
  const boxOptions = inStockBoxes.map(
    (b) => `<option value="${b.id}">${esc(b.box_number)} (${b.year}年)</option>`
  ).join("");
  openModal("记录盘点结果", `
    <div class="form-group">
      <label>选择盒</label>
      <select class="form-control" id="rec-box">${boxOptions}</select>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>盘点结果</label>
        <select class="form-control" id="rec-result">
          <option value="在库">在库</option>
          <option value="缺卷">缺卷</option>
          <option value="错盒">错盒</option>
          <option value="标签损坏">标签损坏</option>
          <option value="少件">少件</option>
        </select>
      </div>
    </div>
    <div class="form-group">
      <label>备注</label>
      <textarea class="form-control" id="rec-notes" rows="2"></textarea>
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitRecordResult(${taskId})">提交</button>
  `);
}

async function submitRecordResult(taskId) {
  const box_id = parseInt(document.getElementById("rec-box").value);
  const result = document.getElementById("rec-result").value;
  const notes = document.getElementById("rec-notes").value.trim();
  try {
    await api(`/api/inventory/tasks/${taskId}/records`, {
      method: "POST",
      body: JSON.stringify({ box_id, result, notes }),
    });
    closeModal();
    showAlert("盘点结果已记录", "success");
    switchView("inventory");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function showSupplementModal(taskId) {
  const boxes = await api("/api/boxes");
  const boxOptions = boxes.map(
    (b) => `<option value="${b.id}">${esc(b.box_number)}</option>`
  ).join("");
  openModal("补录盘点记录", `
    <div class="alert alert-warning">
      补录必须注明依据。补录记录将标记为"已补录"状态。
    </div>
    <div class="form-group">
      <label>选择盒</label>
      <select class="form-control" id="sup-box">${boxOptions}</select>
    </div>
    <div class="form-group">
      <label>盘点结果</label>
      <select class="form-control" id="sup-result">
        <option value="在库">在库</option>
        <option value="缺卷">缺卷</option>
        <option value="错盒">错盒</option>
        <option value="标签损坏">标签损坏</option>
        <option value="少件">少件</option>
      </select>
    </div>
    <div class="form-group">
      <label>依据（必填）</label>
      <textarea class="form-control" id="sup-basis" rows="3" placeholder="请注明补录依据，例如：经复核原始记录，该盒实际在库"></textarea>
    </div>
    <div class="form-group">
      <label>备注</label>
      <textarea class="form-control" id="sup-notes" rows="2"></textarea>
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitSupplement(${taskId})">提交补录</button>
  `);
}

async function submitSupplement(taskId) {
  const box_id = parseInt(document.getElementById("sup-box").value);
  const result = document.getElementById("sup-result").value;
  const basis = document.getElementById("sup-basis").value.trim();
  const notes = document.getElementById("sup-notes").value.trim();
  if (!basis) {
    showAlert("补录必须注明依据", "warning");
    return;
  }
  try {
    await api(`/api/inventory/tasks/${taskId}/supplement`, {
      method: "POST",
      body: JSON.stringify({ box_id, result, basis, notes }),
    });
    closeModal();
    showAlert("补录成功", "success");
    switchView("inventory");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

// ---------------------------------------------------------------------------
// 借阅审批视图 — 主列布局：审批状态和归还期限置于主列
// ---------------------------------------------------------------------------

async function renderBorrowing() {
  const main = document.getElementById("main-content");
  const [borrowings, boxes] = await Promise.all([
    api("/api/borrowings"),
    api("/api/boxes"),
  ]);
  state.borrowings = borrowings;

  const activeBorrowings = borrowings.filter((b) => ["待审批", "已批准", "超期"].includes(b.status));
  const pendingCount = borrowings.filter((b) => b.status === "待审批").length;
  const overdueCount = borrowings.filter((b) => b.status === "超期").length;

  main.innerHTML = `
    <div class="page-header">
      <h2>借阅审批</h2>
      <p class="subtitle">借阅状态与归还期限置于主列。同一盒同时只能一人借阅。</p>
    </div>

    <div class="borrowing-main-col">
      <!-- 主列：借阅列表 -->
      <div>
        <div class="card">
          <div class="card-header">
            <h3>借阅记录</h3>
            <button class="btn btn-primary btn-sm" onclick="showCreateBorrowingModal()">+ 新建借阅</button>
          </div>
          <div class="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>盒号</th><th>借阅人</th><th>审批状态</th>
                  <th>归还期限</th><th>借出时间</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                ${borrowings.length ? borrowings.map((b) => `
                  <tr>
                    <td>${mono(b.box_number)}</td>
                    <td>${esc(b.borrower)}</td>
                    <td>${statusBadge(b.status)}</td>
                    <td class="${b.status === "超期" ? "text-danger" : ""}">${formatDate(b.due_date)}</td>
                    <td>${formatDate(b.borrowed_at)}</td>
                    <td>
                      ${b.status === "待审批" ? `
                        <button class="btn btn-primary btn-sm" onclick="approveBorrowing(${b.id})">批准</button>
                        <button class="btn btn-danger btn-sm" onclick="rejectBorrowing(${b.id})">拒绝</button>
                      ` : ""}
                      ${b.status === "已批准" || b.status === "超期" ? `
                        <button class="btn btn-secondary btn-sm" onclick="returnBorrowing(${b.id})">归还</button>
                      ` : ""}
                    </td>
                  </tr>
                `).join("") : `<tr><td colspan="6" class="empty-state">暂无借阅记录</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- 侧列：状态面板 -->
      <div>
        <div class="borrowing-status-panel">
          <h4>借阅概览</h4>
          <div class="status-item">
            <span>待审批</span>
            <span class="badge badge-warning">${pendingCount}</span>
          </div>
          <div class="status-item">
            <span>借阅中</span>
            <span class="badge badge-success">${activeBorrowings.filter((b) => b.status === "已批准").length}</span>
          </div>
          <div class="status-item">
            <span>已超期</span>
            <span class="badge badge-danger">${overdueCount}</span>
          </div>
          <div class="status-item">
            <span>已归还</span>
            <span class="badge badge-secondary">${borrowings.filter((b) => b.status === "已归还").length}</span>
          </div>
        </div>
      </div>
    </div>
  `;
}

async function showCreateBorrowingModal() {
  const boxes = await api("/api/boxes");
  const availableBoxes = boxes.filter((b) => b.status === "在库");
  if (!availableBoxes.length) {
    showAlert("没有可借阅的盒", "warning");
    return;
  }
  const boxOptions = availableBoxes.map(
    (b) => `<option value="${b.id}">${esc(b.box_number)} (${b.year}年·${b.retention_period})</option>`
  ).join("");
  openModal("新建借阅", `
    <div class="form-group">
      <label>选择盒</label>
      <select class="form-control" id="br-box">${boxOptions}</select>
    </div>
    <div class="form-group">
      <label>借阅人</label>
      <input type="text" class="form-control" id="br-borrower" placeholder="借阅人姓名">
    </div>
    <div class="form-group">
      <label>归还期限</label>
      <input type="date" class="form-control" id="br-due-date">
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitCreateBorrowing()">提交申请</button>
  `);
}

async function submitCreateBorrowing() {
  const box_id = parseInt(document.getElementById("br-box").value);
  const borrower = document.getElementById("br-borrower").value.trim();
  const due_date = document.getElementById("br-due-date").value;
  if (!borrower || !due_date) {
    showAlert("借阅人和归还期限不能为空", "warning");
    return;
  }
  try {
    await api("/api/borrowings", {
      method: "POST",
      body: JSON.stringify({ box_id, borrower, due_date }),
    });
    closeModal();
    showAlert("借阅申请已提交", "success");
    switchView("borrowing");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function approveBorrowing(borrowingId) {
  try {
    await api(`/api/borrowings/${borrowingId}/approve`, { method: "POST" });
    showAlert("借阅已批准", "success");
    switchView("borrowing");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function rejectBorrowing(borrowingId) {
  try {
    await api(`/api/borrowings/${borrowingId}/reject`, { method: "POST", body: JSON.stringify({}) });
    showAlert("借阅已拒绝", "success");
    switchView("borrowing");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

async function returnBorrowing(borrowingId) {
  try {
    await api(`/api/borrowings/${borrowingId}/return`, { method: "POST" });
    showAlert("借阅已归还", "success");
    switchView("borrowing");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

// ---------------------------------------------------------------------------
// 差异处理视图
// ---------------------------------------------------------------------------

async function renderDifferences() {
  const main = document.getElementById("main-content");
  const differences = await api("/api/differences");
  state.differences = differences;

  // 按类型聚合
  const byType = {};
  differences.forEach((d) => {
    if (!byType[d.type]) byType[d.type] = [];
    byType[d.type].push(d);
  });

  const typeLabels = {
    "缺卷": "缺卷",
    "错盒": "错盒",
    "标签损坏": "标签损坏",
    "盘点少件": "盘点少件",
    "移位中断": "移位中断",
    "借阅超期": "借阅超期",
  };

  main.innerHTML = `
    <div class="page-header">
      <h2>差异处理</h2>
      <p class="subtitle">缺卷、错盒、标签损坏、盘点少件、移位中断、借阅超期六类差异。盘点差异按位置聚合。</p>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>差异概览</h3>
      </div>
      <div class="diff-location-stats" style="margin-bottom:16px;">
        ${Object.entries(byType).map(([type, items]) => `
          <span class="badge badge-danger">${typeLabels[type] || type} ${items.length}</span>
        `).join("")}
        ${!differences.length ? `<span class="text-muted">暂无差异记录</span>` : ""}
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>差异列表</h3>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>类型</th><th>盒号</th><th>描述</th>
              <th>状态</th><th>依据</th><th>创建时间</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            ${differences.length ? differences.map((d) => `
              <tr>
                <td>${statusBadge(d.type)}</td>
                <td>${d.box_number ? mono(d.box_number) : "—"}</td>
                <td style="max-width:300px;">${esc(d.description)}</td>
                <td>${statusBadge(d.status)}</td>
                <td>${d.basis ? esc(d.basis) : "—"}</td>
                <td>${formatDate(d.created_at)}</td>
                <td>
                  ${d.status === "待处理" ? `
                    <button class="btn btn-primary btn-sm" onclick="showResolveDifferenceModal(${d.id})">处理</button>
                  ` : "—"}
                </td>
              </tr>
            `).join("") : `<tr><td colspan="7" class="empty-state">暂无差异记录</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function showResolveDifferenceModal(diffId) {
  openModal("处理差异", `
    <div class="alert alert-info">
      处理差异时可注明依据。注明依据后状态将变为"已补录"，否则为"已处理"。
    </div>
    <div class="form-group">
      <label>处理依据（选填）</label>
      <textarea class="form-control" id="resolve-basis" rows="3" placeholder="请注明处理依据..."></textarea>
    </div>
  `, `
    <button class="btn btn-secondary" onclick="closeModal()">取消</button>
    <button class="btn btn-primary" onclick="submitResolveDifference(${diffId})">确认处理</button>
  `);
}

async function submitResolveDifference(diffId) {
  const basis = document.getElementById("resolve-basis").value.trim();
  try {
    await api(`/api/differences/${diffId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ basis }),
    });
    closeModal();
    showAlert("差异已处理", "success");
    switchView("differences");
  } catch (e) {
    showAlert(e.message, "danger");
  }
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

// 页面加载后初始化
document.addEventListener("DOMContentLoaded", () => {
  switchView("catalog");
});
