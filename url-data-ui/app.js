const backendUrl = 'http://127.0.0.1:8765/fetch';

const urlSelect = document.getElementById('urlSelect');
const customUrl = document.getElementById('customUrl');
const queryBtn = document.getElementById('queryBtn');
const currentUrl = document.getElementById('currentUrl');
const statusText = document.getElementById('statusText');
const resultTables = document.getElementById('resultTables');

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
