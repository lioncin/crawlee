  const backendUrl = '/crawlee-api/results/mysql';

const statusText = document.getElementById('statusText');
const resultTables = document.getElementById('resultTables');

const toast = document.createElement('div');
toast.className = 'copy-toast';
document.body.appendChild(toast);
let toastTimer = null;

const BASE_COLUMNS = ['date', 'title', 'url'];

const COLUMN_LABELS = {
  date: '日期',
  title: '标题',
  url: '链接',
  tags: '标签',
  issuer_full_name: '公司名称',
  board: '板块',
  audit_status: '审核状态',
  province: '省份',
  industry: '行业',
  sponsor: '保荐机构',
  law_firm: '律师事务所',
  accounting_firm: '会计师事务所',
  update_date: '更新日期',
  accept_date: '受理日期',
};

const TOKEN_LABELS = {
  issuer: '发行人',
  issue: '发行',
  company: '公司',
  name: '名称',
  full: '全称',
  short: '简称',
  english: '英文',
  board: '板块',
  audit: '审核',
  status: '状态',
  province: '省份',
  city: '城市',
  area: '地区',
  industry: '行业',
  sponsor: '保荐机构',
  law: '法律',
  firm: '事务所',
  accounting: '会计',
  accountant: '会计',
  update: '更新',
  updated: '更新',
  accept: '受理',
  accepted: '受理',
  date: '日期',
  time: '时间',
  url: '链接',
  link: '链接',
  tags: '标签',
  tag: '标签',
  code: '代码',
  stock: '股票',
  market: '市场',
  amount: '金额',
  capital: '资本',
  register: '注册',
  registered: '注册',
  address: '地址',
  phone: '电话',
  email: '邮箱',
  website: '网站',
  person: '人员',
  legal: '法人',
  representative: '代表',
  credit: '信用',
  rating: '评级',
  risk: '风险',
  reason: '原因',
  result: '结果',
  ocr: '识别文本',
  evidence: '依据',
  uncertain: '不确定项',
  info: '信息',
};

function englishKeyToChineseLabel(key) {
  const raw = String(key || '').trim();
  if (!raw) {
    return '';
  }

  if (COLUMN_LABELS[raw]) {
    return COLUMN_LABELS[raw];
  }

  const snake = raw
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[-\s]+/g, '_')
    .toLowerCase();

  const tokens = snake.split('_').filter(Boolean);
  if (tokens.length === 0) {
    return raw;
  }

  const translated = tokens.map((token) => TOKEN_LABELS[token] || token);
  const translatedCount = translated.filter((x, i) => x !== tokens[i]).length;

  if (translatedCount === 0) {
    return raw;
  }

  return translated.join('');
}

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

function extractCompanyInfo(item) {
  if (!item || typeof item !== 'object') {
    return null;
  }

  if (item.company_info && typeof item.company_info === 'object') {
    return item.company_info;
  }

  const extra = item.extra;
  if (!extra || typeof extra !== 'object') {
    return null;
  }

  const rec = extra.ai_recognition;
  if (rec && typeof rec === 'object' && rec.company_info && typeof rec.company_info === 'object') {
    return rec.company_info;
  }

  if (extra.ai_company_info && typeof extra.ai_company_info === 'object') {
    return extra.ai_company_info;
  }

  return null;
}

function getDynamicColumns(items) {
  const ordered = [];
  const seen = new Set();

  for (const item of Array.isArray(items) ? items : []) {
    const companyInfo = extractCompanyInfo(item);
    if (!companyInfo || typeof companyInfo !== 'object') {
      continue;
    }

    for (const key of Object.keys(companyInfo)) {
      if (!seen.has(key)) {
        seen.add(key);
        ordered.push(key);
      }
    }
  }

  return ordered;
}

function renderItemsTable(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return '<p class="empty">无 items 数据</p>';
  }

  const dynamicColumns = getDynamicColumns(items);
  const columns = [...BASE_COLUMNS, ...dynamicColumns];

  const header = columns.map((col) => `<th>${escapeHtml(englishKeyToChineseLabel(col))}</th>`).join('');
  const body = items
    .map((item) => {
      const companyInfo = extractCompanyInfo(item) || {};
      const cells = columns.map((col) => {
        let cellValue = '';

        if (BASE_COLUMNS.includes(col)) {
          cellValue = item?.[col] ?? '';
        } else {
          cellValue = companyInfo?.[col] ?? item?.[col] ?? '';
        }

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
