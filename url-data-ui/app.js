const backendUrl = 'http://127.0.0.1:8765/fetch';

const urlSelect = document.getElementById('urlSelect');
const customUrl = document.getElementById('customUrl');
const queryBtn = document.getElementById('queryBtn');
const currentUrl = document.getElementById('currentUrl');
const statusText = document.getElementById('statusText');
const resultTables = document.getElementById('resultTables');

const toast = document.createElement('div');
toast.className = 'copy-toast';
document.body.appendChild(toast);
let toastTimer = null;

const ITEM_COLUMNS = [
  'date',
  'title',
  'url',
  'issuer_full_name',
  'board',
  'audit_status',
  'province',
  'industry',
  'sponsor',
  'law_firm',
  'accounting_firm',
  'update_date',
  'accept_date',
];

const modalState = {
  images: [],
  nextId: 1,
};

const imageModal = document.createElement('div');
imageModal.className = 'image-modal';
imageModal.innerHTML = `
  <div class="image-dialog" role="dialog" aria-modal="true" aria-label="上传图片">
    <div class="image-dialog-head">
      <h3>上传图片</h3>
      <button type="button" class="image-close" id="imageCloseBtn">关闭</button>
    </div>
    <div class="image-dialog-body">
      <div class="image-tools">
        <label class="image-upload-btn" for="imageFileInput">选择图片</label>
        <input id="imageFileInput" type="file" accept="image/*" multiple hidden />
        <button type="button" class="image-clear-btn" id="imageClearBtn">清空</button>
      </div>
      <div id="pasteZone" class="paste-zone" tabindex="0">
        支持截图粘贴：按 Ctrl/Cmd + V，或选择多张图片上传。
      </div>
      <div id="imagePreviewGrid" class="image-preview-grid"></div>
    </div>
  </div>
`;
document.body.appendChild(imageModal);

const imageFileInput = imageModal.querySelector('#imageFileInput');
const imageCloseBtn = imageModal.querySelector('#imageCloseBtn');
const imageClearBtn = imageModal.querySelector('#imageClearBtn');
const pasteZone = imageModal.querySelector('#pasteZone');
const imagePreviewGrid = imageModal.querySelector('#imagePreviewGrid');

function setStatus(text, type = '') {
  statusText.textContent = text;
  statusText.className = `status ${type}`.trim();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function showCopyToast(message) {
  toast.textContent = message;
  toast.classList.add('show');

  if (toastTimer) {
    clearTimeout(toastTimer);
  }

  toastTimer = setTimeout(() => {
    toast.classList.remove('show');
  }, 1500);
}

function getRequestUrl() {
  if (urlSelect.value === 'custom') {
    const value = customUrl.value.trim();
    if (!value) {
      throw new Error('请选择 URL 或输入自定义 URL');
    }
    return value;
  }
  return urlSelect.value;
}

function buildIssuerCell(cellValue) {
  const safeText = escapeHtml(cellValue);
  return `<td><span class="issuer-cell"><span>${safeText}</span><button type="button" class="copy-issuer" data-copy="${safeText}">[复制]</button></span></td>`;
}

function renderItemsTable(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return '<p class="empty">无 items 数据</p>';
  }

  const header = ITEM_COLUMNS.map((col) => `<th>${escapeHtml(col)}</th>`).join('');
  const body = items
    .map((item) => {
      const cells = ITEM_COLUMNS.map((col) => {
        const cellValue = item?.[col] ?? '';
        if (col === 'url' && cellValue) {
          const safeUrl = escapeHtml(cellValue);
          return `<td><a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${safeUrl}</a></td>`;
        }
        if (col === 'issuer_full_name' && cellValue) {
          return buildIssuerCell(cellValue);
        }
        return `<td>${escapeHtml(cellValue)}</td>`;
      }).join('');
      return `<tr>${cells}</tr>`;
    })
    .join('');

  return `
    <div class="table-wrap">
      <table>
        <thead><tr>${header}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

function revokeAllImages() {
  for (const img of modalState.images) {
    URL.revokeObjectURL(img.url);
  }
  modalState.images = [];
}

function renderImagePreview() {
  if (modalState.images.length === 0) {
    imagePreviewGrid.innerHTML = '<p class="empty">暂无图片</p>';
    return;
  }

  const cards = modalState.images
    .map((img) => {
      const safeName = escapeHtml(img.name || 'image');
      const safeUrl = escapeHtml(img.url);
      return `
        <article class="image-card" data-id="${img.id}">
          <img src="${safeUrl}" alt="${safeName}" />
          <div class="image-card-foot">
            <span title="${safeName}">${safeName}</span>
            <button type="button" class="image-remove-btn" data-id="${img.id}">删除</button>
          </div>
        </article>
      `;
    })
    .join('');

  imagePreviewGrid.innerHTML = cards;

  imagePreviewGrid.querySelectorAll('.image-remove-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = Number(btn.getAttribute('data-id'));
      const idx = modalState.images.findIndex((x) => x.id === id);
      if (idx >= 0) {
        URL.revokeObjectURL(modalState.images[idx].url);
        modalState.images.splice(idx, 1);
        renderImagePreview();
      }
    });
  });
}

function addImageFiles(fileList) {
  const files = Array.from(fileList || []).filter((f) => f && String(f.type || '').startsWith('image/'));
  if (files.length === 0) {
    return;
  }

  for (const file of files) {
    modalState.images.push({
      id: modalState.nextId++,
      file,
      name: file.name || `image-${modalState.nextId}`,
      url: URL.createObjectURL(file),
    });
  }

  renderImagePreview();
}

function openImageModal() {
  revokeAllImages();
  renderImagePreview();
  imageModal.classList.add('show');
  imageFileInput.value = '';
  setTimeout(() => pasteZone.focus(), 30);
}

function closeImageModal() {
  imageModal.classList.remove('show');
}

function bindCopyEvents() {
  resultTables.querySelectorAll('.copy-issuer').forEach((button) => {
    button.addEventListener('click', async () => {
      const text = button.getAttribute('data-copy') || '';
      if (!text) {
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        showCopyToast(`已复制 ${text}`);
        const old = button.textContent;
        button.textContent = '[已复制]';
        setTimeout(() => {
          button.textContent = old;
        }, 1000);
        openImageModal();
      } catch {
        button.textContent = '[复制失败]';
        setTimeout(() => {
          button.textContent = '[复制]';
        }, 1000);
      }
    });
  });
}

function renderResults(data) {
  resultTables.innerHTML = '';

  if (!data || typeof data !== 'object') {
    resultTables.innerHTML = '<p class="empty">返回结果为空或格式不正确</p>';
    return;
  }

  const entries = Object.entries(data);
  if (entries.length === 0) {
    resultTables.innerHTML = '<p class="empty">返回结果为空</p>';
    return;
  }

  const fragments = entries.map(([sourceUrl, detail]) => {
    const statusCode = detail?.status_code ?? '';
    const title = detail?.title ?? '';
    const htmlLength = detail?.html_length ?? '';
    const rowCount = Array.isArray(detail?.items) ? detail.items.length : 0;

    return `
      <article class="result-card">
        <h3>${escapeHtml(sourceUrl)}</h3>
        <p class="meta-line">status_code: ${escapeHtml(statusCode)} | title: ${escapeHtml(title)} | html_length: ${escapeHtml(htmlLength)} | items: ${escapeHtml(rowCount)}</p>
        ${renderItemsTable(detail?.items)}
      </article>
    `;
  });

  resultTables.innerHTML = fragments.join('');
  bindCopyEvents();
}

async function runQuery() {
  let requestUrl = '';
  try {
    requestUrl = getRequestUrl();
  } catch (error) {
    currentUrl.textContent = '参数无效';
    setStatus(error.message, 'err');
    return;
  }

  currentUrl.textContent = requestUrl;
  setStatus('查询中...');
  resultTables.innerHTML = '<p class="empty">正在请求后端...</p>';

  try {
    const response = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
      },
      body: JSON.stringify({ url: requestUrl }),
    });

    const text = await response.text();
    let parsed;

    try {
      parsed = text ? JSON.parse(text) : {};
    } catch {
      throw new Error('后端返回的不是有效 JSON');
    }

    renderResults(parsed);
    setStatus(response.ok ? `成功 ${response.status}` : `失败 ${response.status}`, response.ok ? 'ok' : 'err');
  } catch (error) {
    resultTables.innerHTML = `<p class="empty">请求失败：${escapeHtml(error.message)}</p>`;
    setStatus('请求异常', 'err');
  }
}

imageCloseBtn.addEventListener('click', closeImageModal);
imageModal.addEventListener('click', (event) => {
  if (event.target === imageModal) {
    closeImageModal();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && imageModal.classList.contains('show')) {
    closeImageModal();
  }
});

imageFileInput.addEventListener('change', () => {
  addImageFiles(imageFileInput.files);
  imageFileInput.value = '';
});

imageClearBtn.addEventListener('click', () => {
  revokeAllImages();
  renderImagePreview();
});

pasteZone.addEventListener('paste', (event) => {
  const items = Array.from(event.clipboardData?.items || []);
  const files = items
    .filter((item) => String(item.type || '').startsWith('image/'))
    .map((item) => item.getAsFile())
    .filter(Boolean);

  if (files.length > 0) {
    event.preventDefault();
    addImageFiles(files);
  }
});

urlSelect.addEventListener('change', () => {
  const isCustom = urlSelect.value === 'custom';
  customUrl.disabled = !isCustom;
  if (isCustom) {
    customUrl.focus();
  }
});

queryBtn.addEventListener('click', runQuery);
customUrl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    runQuery();
  }
});

currentUrl.textContent = urlSelect.value;
renderImagePreview();
