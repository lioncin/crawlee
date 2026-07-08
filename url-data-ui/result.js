const backendUrl = 'http://127.0.0.1:8765/results/mysql';

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

function setStatus(text, type = '') {
  statusText.textContent = text;
  statusText.className = `status ${type}`.trim();
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

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
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
    resultTables.innerHTML = '<p class="empty">暂无数据</p>';
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

async function loadResults() {
  setStatus('加载中...');
  resultTables.innerHTML = '<p class="empty">正在从 MySQL 读取数据...</p>';

  const params = new URLSearchParams();
  params.set('limit', '20');

  const url = `${backendUrl}?${params.toString()}`;

  try {
    const response = await fetch(url, { method: 'GET' });
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
    resultTables.innerHTML = `<p class="empty">读取失败：${escapeHtml(error.message)}</p>`;
    setStatus('请求异常', 'err');
  }
}

loadResults();
