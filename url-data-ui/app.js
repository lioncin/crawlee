// const backendUrl = '/crawlee-api/results/mysql';
// const uploadBackendUrl = '/crawlee-api/upload/image';
// const recognizeBackendUrl = '/crawlee-api/issuer/recognize';

const backendUrl = 'http://127.0.0.1:8765/results/mysql';
const fetchBackendUrl = 'http://127.0.0.1:8765/fetch';
const uploadBackendUrl = 'http://127.0.0.1:8765/upload/image';
const recognizeBackendUrl = 'http://127.0.0.1:8765/issuer/recognize';

const startQueryBtn = document.getElementById('startQueryBtn');
const queryBtn = document.getElementById('queryBtn');
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

const COMPANY_INFO_COLUMNS = [
    'company_name',
    'issuer_full_name',
    'contact_name',
    'phone',
    'email',
    'employee_count',
    'operating_revenue',
    'insured_count',
    'certificates',
    'reason',
    'matched_image_urls',
    'notice_url',
];

const modalState = {
    images: [],
    nextId: 1,
    uploading: false,
    activeContext: null,
};

let currentResultsData = null;

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
        <button type="button" class="image-submit-btn" id="imageSubmitBtn">上传并识别</button>
        <button type="button" class="image-clear-btn" id="imageClearBtn">清空</button>
      </div>
      <div id="pasteZone" class="paste-zone" tabindex="0">
        支持截图粘贴：按 Ctrl/Cmd + V，或选择多张图片上传。
      </div>
      <p id="imageUploadStatus" class="image-upload-status">未上传</p>
      <div id="imagePreviewGrid" class="image-preview-grid"></div>
    </div>
  </div>
`;
document.body.appendChild(imageModal);

const imageFileInput = imageModal.querySelector('#imageFileInput');
const imageSubmitBtn = imageModal.querySelector('#imageSubmitBtn');
const imageCloseBtn = imageModal.querySelector('#imageCloseBtn');
const imageClearBtn = imageModal.querySelector('#imageClearBtn');
const pasteZone = imageModal.querySelector('#pasteZone');
const imageUploadStatus = imageModal.querySelector('#imageUploadStatus');
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

function encodeAttr(value) {
    return encodeURIComponent(String(value ?? ''));
}

function decodeAttr(value) {
    try {
        return decodeURIComponent(String(value || ''));
    } catch {
        return String(value || '');
    }
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

function setUploadUiState(uploading, message = '', type = '') {
    modalState.uploading = uploading;
    imageSubmitBtn.disabled = uploading;
    imageSubmitBtn.textContent = uploading ? '处理中...' : '上传并识别';
    if (message) {
        imageUploadStatus.textContent = message;
        imageUploadStatus.className = `image-upload-status ${type}`.trim();
    }
}

function buildIssuerCell(cellValue, sourceUrl, noticeUrl) {
    const safeText = escapeHtml(cellValue);
    const copyAttr = encodeAttr(cellValue);
    const sourceAttr = encodeAttr(sourceUrl);
    const noticeAttr = encodeAttr(noticeUrl);
    return `<td><span class="issuer-cell"><span>${safeText}</span><button type="button" class="copy-issuer" data-copy="${copyAttr}" data-issuer="${copyAttr}" data-source-url="${sourceAttr}" data-notice-url="${noticeAttr}">[复制]</button></span></td>`;
}

function renderItemsTable(items, sourceUrl) {
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
                    return buildIssuerCell(cellValue, sourceUrl, item?.url || '');
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

function hasCompanyInfo(item) {
    const info = item?.company_info;
    return info && typeof info === 'object' && Object.keys(info).length > 0;
}

function normalizeCompanyInfoValue(value) {
    if (Array.isArray(value)) {
        return value.map((x) => String(x ?? '')).filter(Boolean).join('；');
    }
    if (value && typeof value === 'object') {
        return JSON.stringify(value, null, 0);
    }
    return value ?? '';
}

function renderCompanyInfoTable(items) {
    if (!Array.isArray(items) || items.length === 0) {
        return '<p class="empty">无已识别 company_info 数据</p>';
    }

    const aiItems = items.filter((item) => hasCompanyInfo(item));
    if (aiItems.length === 0) {
        return '<p class="empty">无已识别 company_info 数据</p>';
    }

    const header = COMPANY_INFO_COLUMNS.map((col) => `<th>${escapeHtml(col)}</th>`).join('');
    const body = aiItems
        .map((item) => {
            const info = item.company_info || {};
            const cells = COMPANY_INFO_COLUMNS.map((col) => {
                if (col === 'notice_url') {
                    const noticeUrl = info.notice_url || item?.url || '';
                    if (!noticeUrl) {
                        return '<td></td>';
                    }
                    const safeUrl = escapeHtml(noticeUrl);
                    return `<td><a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${safeUrl}</a></td>`;
                }

                const raw = normalizeCompanyInfoValue(info[col]);
                return `<td>${escapeHtml(raw)}</td>`;
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
    setUploadUiState(false, '未上传');
    setTimeout(() => pasteZone.focus(), 30);
}

function closeImageModal() {
    imageModal.classList.remove('show');
}

function bindCopyEvents() {
    resultTables.querySelectorAll('.copy-issuer').forEach((button) => {
        button.addEventListener('click', async () => {
            const text = decodeAttr(button.getAttribute('data-copy') || '');
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

                modalState.activeContext = {
                    issuerName: decodeAttr(button.getAttribute('data-issuer') || ''),
                    sourceUrl: decodeAttr(button.getAttribute('data-source-url') || ''),
                    noticeUrl: decodeAttr(button.getAttribute('data-notice-url') || ''),
                };

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

async function recognizeUploadedImages(imageUrls) {
    const ctx = modalState.activeContext;
    if (!ctx || !ctx.noticeUrl) {
        throw new Error('未找到当前行上下文，无法识别');
    }

    const response = await fetch(recognizeBackendUrl, {
        method: 'POST',
        headers: {
            'content-type': 'application/json',
        },
        body: JSON.stringify({
            source_url: ctx.sourceUrl || '',
            notice_url: ctx.noticeUrl,
            issuer_name: ctx.issuerName || '',
            image_urls: imageUrls,
            timeout_seconds: 180,
        }),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data?.detail || `识别失败（${response.status}）`);
    }

    return data;
}

function applyCurrentRowPatch(context, patch) {
    if (!currentResultsData || !patch || typeof patch !== 'object') {
        return false;
    }

    let updated = false;
    for (const [sourceUrl, detail] of Object.entries(currentResultsData)) {
        if (!detail || typeof detail !== 'object' || !Array.isArray(detail.rows)) {
            continue;
        }

        if (context.sourceUrl && sourceUrl !== context.sourceUrl) {
            continue;
        }

        for (const item of detail.rows) {
            if (!item || typeof item !== 'object') {
                continue;
            }
            if (item.url !== context.noticeUrl) {
                continue;
            }
            Object.assign(item, patch);
            updated = true;
            break;
        }

        if (updated) {
            break;
        }
    }

    return updated;
}

async function uploadImages() {
    if (modalState.uploading) {
        return;
    }
    if (modalState.images.length === 0) {
        setUploadUiState(false, '请先选择或粘贴图片', 'err');
        return;
    }

    const formData = new FormData();
    for (const row of modalState.images) {
        formData.append('files', row.file, row.name || 'image.png');
    }

    setUploadUiState(true, `上传中：${modalState.images.length} 张图片...`);
    try {
        const uploadResponse = await fetch(uploadBackendUrl, {
            method: 'POST',
            body: formData,
        });
        const uploadData = await uploadResponse.json().catch(() => ({}));
        if (!uploadResponse.ok) {
            throw new Error(uploadData?.detail || `上传失败（${uploadResponse.status}）`);
        }

        const uploaded = Array.isArray(uploadData?.uploaded) ? uploadData.uploaded : [];
        const imageUrls = uploaded.map((x) => x?.url).filter(Boolean);
        if (imageUrls.length === 0) {
            throw new Error('上传成功但未返回图片 URL');
        }

        setUploadUiState(true, '上传成功，正在调用识别...');
        const recognitionData = await recognizeUploadedImages(imageUrls);
        const patch = recognitionData?.item_patch || {};
        const applied = applyCurrentRowPatch(modalState.activeContext || {}, patch);
        if (applied) {
            renderResults(currentResultsData);
        }

        closeImageModal();
        setUploadUiState(false, `识别并保存成功：${imageUrls.length} 张`, 'ok');
        showCopyToast('识别结果已保存并刷新当前行');
    } catch (error) {
        setUploadUiState(false, `处理失败：${error.message || '未知错误'}`, 'err');
    }
}

function renderResults(data) {
    resultTables.innerHTML = '';
    currentResultsData = data;

    if (!data || typeof data !== 'object') {
        resultTables.innerHTML = '<p class="empty">返回结果为空或格式不正确</p>';
        return;
    }

    const entries = Object.entries(data);
    if (entries.length === 0) {
        resultTables.innerHTML = '<p class="empty">返回结果为空</p>';
        return;
    }

    const tableHeader = `
      <thead>
        <tr>
          <th>issuer_full_name</th>
        </tr>
      </thead>
    `;

    const tableBody = entries
        .map(([sourceUrl, detail]) => {
            const statusCode = detail?.status_code ?? '';
            const title = detail?.title ?? '';
            const htmlLength = detail?.html_length ?? '';
            const rows = Array.isArray(detail?.rows) ? detail.rows : [];

            if (rows.length === 0) {
                return '';
            }

            return rows
                .map((item, index) => {
                    const issuerName = item?.issuer_full_name ?? '';
                    const noticeUrl = item?.url ?? '';
                    return `
            <tr>
              ${buildIssuerCell(issuerName, sourceUrl, noticeUrl)}
            </tr>
          `;
                })
                .join('');
        })
        .join('');

    if (!tableBody.trim()) {
        resultTables.innerHTML = '<p class="empty">返回结果为空</p>';
        return;
    }

    const fragments = `
      <div class="table-wrap">
        <table>
          ${tableHeader}
          <tbody>${tableBody}</tbody>
        </table>
      </div>
    `;

    resultTables.innerHTML = fragments;
    bindCopyEvents();
}

async function runQuery() {
    setStatus('查询中...');
    resultTables.innerHTML = '<p class="empty">正在从数据库加载已保存结果...</p>';

    try {
        const response = await fetch(backendUrl);

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

async function startQuery() {
    setStatus('查询中...');
    resultTables.innerHTML = '<p class="empty">正在请求后端开始抓取...</p>';

    try {
        const response = await fetch(fetchBackendUrl, {
            method: 'POST',
            headers: {
                'content-type': 'application/json',
            },
            body: JSON.stringify({
                url: '*',
            }),
        });

        const text = await response.text();
        let parsed;

        try {
            parsed = text ? JSON.parse(text) : {};
        } catch {
            throw new Error('后端返回的不是有效 JSON');
        }

        if (!response.ok) {
            throw new Error(parsed?.detail || `开始查询失败（${response.status}）`);
        }

        renderResults(parsed);
        setStatus(`查询成功 ${response.status}`, 'ok');
    } catch (error) {
        resultTables.innerHTML = `<p class="empty">请求失败：${escapeHtml(error.message)}</p>`;
        setStatus('查询失败', 'err');
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
    setUploadUiState(false, '已清空待上传图片');
});

imageSubmitBtn.addEventListener('click', uploadImages);

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

startQueryBtn.addEventListener('click', startQuery);
queryBtn.addEventListener('click', runQuery);
renderImagePreview();
runQuery();
