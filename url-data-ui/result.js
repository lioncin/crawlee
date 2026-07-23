// const backendUrl = '/crawlee-api/results/mysql';
const backendUrl = 'http://127.0.0.1:8765/results/mysql';
const aiAnalysisUrl = 'http://127.0.0.1:8765/analysis/lead-score';
const crmClueCreateUrl = 'https://crm.inlink-ai.com/admin-api/crm/clues/third-party/create';

const statusText = document.getElementById('statusText');
const resultTables = document.getElementById('resultTables');
const aiAnalyzeBtn = document.getElementById('aiAnalyzeBtn');
const syncToCrmBtn = document.getElementById('syncToCrmBtn');
const aiStatusText = document.getElementById('aiStatusText');
const aiResultBoard = document.getElementById('aiResultBoard');
const aiCompanySearch = document.getElementById('aiCompanySearch');
const aiGradeFilter = document.getElementById('aiGradeFilter');
const aiExportCsvBtn = document.getElementById('aiExportCsvBtn');
const aiFilterSummary = document.getElementById('aiFilterSummary');

const toast = document.createElement('div');
toast.className = 'copy-toast';
document.body.appendChild(toast);
let toastTimer = null;

const BASE_COLUMNS = ['date', 'title', 'audit_status'];
const GRADE_ORDER = ['A', 'B', 'C', 'D'];
const CRM_LEVEL_BY_GRADE = { A: 4, B: 3, C: 2, D: 1 };
let aiAnalysisRows = [];

const COLUMN_LABELS = {
    date: '日期',
    title: '标题',
    url: '链接',
    tags: '标签',
    issuer_full_name: '公司名称',
    board: '板块',
    audit_status: '状态',
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
    if (!statusText) {
        return;
    }
    statusText.textContent = text;
    statusText.className = `status ${type}`.trim();
}

function setAiStatus(text, type = '') {
    if (!aiStatusText) {
        return;
    }
    aiStatusText.textContent = text;
    aiStatusText.className = `status ${type}`.trim();
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
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function normalizeGrade(rawGrade) {
    const grade = String(rawGrade || '').trim().toUpperCase();
    return GRADE_ORDER.includes(grade) ? grade : 'D';
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

    const columns = BASE_COLUMNS;

    const header = columns.map((col) => `<th>${escapeHtml(englishKeyToChineseLabel(col))}</th>`).join('');
    const body = items
        .map((item) => {
            const companyInfo = extractCompanyInfo(item) || {};
            const cells = columns
                .map((col) => {
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
                })
                .join('');
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
    if (!resultTables) {
        return;
    }

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
    if (!resultTables) {
        return;
    }

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

function flattenAiGroups(data) {
    const groups = data?.groups && typeof data.groups === 'object' ? data.groups : {};
    const rows = [];

    const pickFirstNonEmpty = (source, keys) => {
        for (const key of keys) {
            const value = source?.[key];
            if (value !== undefined && value !== null && String(value).trim() !== '') {
                return String(value).trim();
            }
        }
        return '';
    };

    const pickFromBusinessData = (item, keys) => {
        const businessData = item?.company_info?.business_data;
        if (!businessData || typeof businessData !== 'object') {
            return '';
        }
        return pickFirstNonEmpty(businessData, keys);
    };

    const normalizeCertificates = (item) => {
        const certs = item?.company_info?.certificates;
        if (Array.isArray(certs)) {
            return certs.filter((x) => x && typeof x === 'object');
        }

        const fallbackCerts = item?.extra?.ai_company_info?.certificates;
        if (Array.isArray(fallbackCerts)) {
            return fallbackCerts.filter((x) => x && typeof x === 'object');
        }

        return [];
    };

    const formatCertificateNames = (certificates) => {
        return certificates
            .map((cert) =>
                pickFirstNonEmpty(cert, ['certification_project', 'certificate_name', 'name', 'certificate_no']),
            )
            .filter(Boolean)
            .join('；');
    };

    const formatCertificateIndustries = (certificates) => {
        return certificates
            .map((cert) => pickFirstNonEmpty(cert, ['industry', 'certification_scope', 'scope']))
            .filter(Boolean)
            .join('；');
    };

    for (const grade of GRADE_ORDER) {
        const items = Array.isArray(groups[grade]) ? groups[grade] : [];
        for (const item of items) {
            const certificates = normalizeCertificates(item);
            rows.push({
                grade,
                company_name: String(item?.company_name ?? '').trim(),
                employee_count:
                    pickFromBusinessData(item, ['employees', 'employees_text']) ||
                    pickFirstNonEmpty(item, ['employee_count', 'staff_count', 'employee_num', 'staff_num']),
                operating_revenue:
                    pickFromBusinessData(item, ['revenue_text']) ||
                    pickFirstNonEmpty(item, ['operating_revenue', 'revenue', 'annual_revenue', 'business_revenue']),
                insured_count:
                    pickFromBusinessData(item, ['insured_persons']) ||
                    pickFirstNonEmpty(item, ['insured_count', 'insured_num', 'social_security_count']),
                certificate_names:
                    pickFirstNonEmpty(item, ['certificate_names']) || formatCertificateNames(certificates),
                certificate_industries:
                    pickFirstNonEmpty(item, ['certificate_industries']) ||
                    formatCertificateIndustries(certificates),
                reason: String(item?.reason ?? '').trim(),
                title: String(item?.title ?? '').trim(),
                item_date: String(item?.item_date ?? '').trim(),
            });
        }
    }

    return rows;
}

function getFilteredAiRows() {
    const keyword = String(aiCompanySearch?.value ?? '').trim().toLowerCase();
    const gradeFilter = String(aiGradeFilter?.value || 'ALL').toUpperCase();

    return aiAnalysisRows.filter((row) => {
        if (gradeFilter !== 'ALL' && normalizeGrade(row.grade) !== gradeFilter) {
            return false;
        }

        if (!keyword) {
            return true;
        }

        return String(row.company_name || '').toLowerCase().includes(keyword);
    });
}

function renderAiRows(rows) {
    if (!aiResultBoard) {
        return;
    }

    if (aiFilterSummary) {
        aiFilterSummary.textContent = `共 ${aiAnalysisRows.length} 家，当前 ${rows.length} 家`;
    }

    if (!rows.length) {
        aiResultBoard.innerHTML = '<p class="empty">当前筛选条件下暂无结果</p>';
        return;
    }

    const body = rows
        .map(
            (row) => `
      <tr>
        <td>${escapeHtml(row.company_name)}</td>
        <td>${escapeHtml(row.employee_count)}</td>
        <td>${escapeHtml(row.operating_revenue)}</td>
        <td>${escapeHtml(row.insured_count)}</td>
        <td>${escapeHtml(row.certificate_names)}</td>
        <td>${escapeHtml(row.certificate_industries)}</td>
        <td>${escapeHtml(row.reason)}</td>
      </tr>
    `,
        )
        .join('');

    aiResultBoard.innerHTML = `
      <div class="table-wrap ai-analysis-table-wrap">
        <table class="ai-analysis-table">
          <thead>
            <tr>
              <th>公司名字</th>
              <th>员工人数</th>
              <th>营业收入</th>
              <th>参保人数</th>
              <th>资质证书名称</th>
              <th>资质证书行业</th>
              <th>评级理由</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    `;
}

function rerenderAiByFilters() {
    if (!aiResultBoard) {
        return;
    }

    if (aiAnalysisRows.length === 0) {
        aiResultBoard.innerHTML = '<p class="empty">点击上方 AI分析 按钮后显示结果</p>';
        if (aiFilterSummary) {
            aiFilterSummary.textContent = '';
        }
        return;
    }

    renderAiRows(getFilteredAiRows());
}

function renderAiAnalysis(data) {
    aiAnalysisRows = flattenAiGroups(data);
    rerenderAiByFilters();
}

function csvEscape(value) {
    const text = String(value ?? '');
    if (text.includes(',') || text.includes('"') || text.includes('\n')) {
        return `"${text.replaceAll('"', '""')}"`;
    }
    return text;
}

function exportAiCsv() {
    if (aiAnalysisRows.length === 0) {
        showCopyToast('暂无可导出的AI结果');
        return;
    }

    const rows = getFilteredAiRows();
    if (!rows.length) {
        showCopyToast('筛选后无可导出数据');
        return;
    }

    const header = ['等级', '公司名', '员工人数', '营业收入', '参保人数', '资质证书名称', '资质证书行业', '评级理由'];
    const lines = [header.map(csvEscape).join(',')];

    for (const row of rows) {
        lines.push(
            [
                normalizeGrade(row.grade),
                row.company_name,
                row.employee_count,
                row.operating_revenue,
                row.insured_count,
                row.certificate_names,
                row.certificate_industries,
                row.reason,
            ]
                .map(csvEscape)
                .join(','),
        );
    }

    const csvContent = `\uFEFF${lines.join('\n')}`;
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);

    const now = new Date();
    const pad = (x) => String(x).padStart(2, '0');
    const fileName = `ai_analysis_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.csv`;

    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);

    showCopyToast(`已导出 ${rows.length} 条数据`);
}

function buildCrmClue(row) {
    const remarkParts = [];
    if (row.certificate_industries) {
        remarkParts.push(`资质证书行业：${row.certificate_industries}`);
    }
    if (row.reason) {
        remarkParts.push(`AI评级理由：${row.reason}`);
    }

    return {
        name: row.company_name,
        level: CRM_LEVEL_BY_GRADE[normalizeGrade(row.grade)],
        tags: row.employee_count || undefined,
        source: 4,
        remark: remarkParts.join('；') || undefined,
        thirdPartySource: 'AI分析',
    };
}

async function syncAiResultsToCrm() {
    if (!syncToCrmBtn) {
        return;
    }

    const rows = getFilteredAiRows().filter((row) => row.company_name);
    if (!rows.length) {
        showCopyToast('暂无可同步的AI结果');
        return;
    }

    syncToCrmBtn.disabled = true;
    let succeeded = 0;
    let failed = 0;

    try {
        for (let index = 0; index < rows.length; index += 1) {
            setAiStatus(`同步CRM中（${index + 1}/${rows.length}）...`);

            try {
                const response = await fetch(crmClueCreateUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'tenant-id': '1',
                    },
                    body: JSON.stringify(buildCrmClue(rows[index])),
                });

                if (!response.ok) {
                    throw new Error(`请求失败 ${response.status}`);
                }

                succeeded += 1;
            } catch (error) {
                console.error('CRM同步失败', rows[index].company_name, error);
                failed += 1;
            }
        }

        if (failed === 0) {
            setAiStatus(`已同步 ${succeeded} 条到CRM`, 'ok');
        } else {
            setAiStatus(`同步完成：成功 ${succeeded} 条，失败 ${failed} 条`, 'err');
        }
    } finally {
        syncToCrmBtn.disabled = false;
    }
}

async function runAiAnalysis() {
    if (!aiAnalyzeBtn || !aiResultBoard) {
        return;
    }

    aiAnalyzeBtn.disabled = true;
    setAiStatus('分析中...');
    aiResultBoard.innerHTML = '<p class="empty">AI 正在分批分析公司数据，请稍候...</p>';

    try {
        const response = await fetch(aiAnalysisUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
        });

        const text = await response.text();
        let parsed = {};

        try {
            parsed = text ? JSON.parse(text) : {};
        } catch {
            throw new Error('后端返回的不是有效 JSON');
        }

        if (!response.ok) {
            const detail = parsed?.detail ? `：${parsed.detail}` : '';
            throw new Error(`请求失败 ${response.status}${detail}`);
        }

        renderAiAnalysis(parsed);
        setAiStatus('分析完成', 'ok');
    } catch (error) {
        aiResultBoard.innerHTML = `<p class="empty">AI分析失败：${escapeHtml(error.message)}</p>`;
        setAiStatus('分析失败', 'err');
    } finally {
        aiAnalyzeBtn.disabled = false;
    }
}

function bindAiAnalyzeEvent() {
    if (!aiAnalyzeBtn) {
        return;
    }

    aiAnalyzeBtn.addEventListener('click', () => {
        runAiAnalysis();
    });
}

function bindSyncToCrmEvent() {
    if (!syncToCrmBtn) {
        return;
    }

    syncToCrmBtn.addEventListener('click', () => {
        syncAiResultsToCrm();
    });
}

function bindAiFilterEvents() {
    if (aiCompanySearch) {
        aiCompanySearch.addEventListener('input', () => {
            rerenderAiByFilters();
        });
    }

    if (aiGradeFilter) {
        aiGradeFilter.addEventListener('change', () => {
            rerenderAiByFilters();
        });
    }

    if (aiExportCsvBtn) {
        aiExportCsvBtn.addEventListener('click', () => {
            exportAiCsv();
        });
    }
}

async function loadResults() {
    if (!resultTables) {
        return;
    }

    setStatus('');
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
        setStatus(response.ok ? '' : `失败 ${response.status}`, response.ok ? '' : 'err');
    } catch (error) {
        resultTables.innerHTML = `<p class="empty">读取失败：${escapeHtml(error.message)}</p>`;
        setStatus('请求异常', 'err');
    }
}

async function loadSavedAiAnalysis() {
    try {
        const response = await fetch(aiAnalysisUrl, { method: "GET" });
        if (!response.ok) {
            return;
        }

        const text = await response.text();
        let parsed = {};
        try {
            parsed = text ? JSON.parse(text) : {};
        } catch {
            return;
        }

        const total = Number(parsed?.summary?.total || 0);
        if (total > 0) {
            renderAiAnalysis(parsed);
            setAiStatus("已加载历史结果", "ok");
        }
    } catch {
        // Keep UI quiet on first-load historical query failures.
    }
}

async function initPage() {
    bindAiAnalyzeEvent();
    bindSyncToCrmEvent();
    bindAiFilterEvents();
    await loadSavedAiAnalysis();
    await loadResults();
}

initPage();
