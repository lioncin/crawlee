import { randomUUID } from 'node:crypto';
import dns from 'node:dns/promises';
import { createServer } from 'node:http';
import net from 'node:net';
import { CheerioCrawler, log } from 'crawlee';

const PORT = Number(process.env.PORT ?? 3000);
const REQUEST_TIMEOUT_MS = 30_000;
const CACHE_TTL_MS = Number(process.env.CACHE_TTL_MS ?? 60_000);
const MAX_IN_FLIGHT = Number(process.env.MAX_IN_FLIGHT ?? 5);
const pendingResponses = new Map();
const responseCache = new Map();
let inFlightCount = 0;

const crawler = new CheerioCrawler({
    keepAlive: true,
    requestHandlerTimeoutSecs: 20,
    maxRequestRetries: 1,
    maxConcurrency: MAX_IN_FLIGHT,
    async requestHandler({ request, $, response }) {
        const pending = pendingResponses.get(request.uniqueKey);
        if (!pending) return;

        const title = $('title').first().text().trim();
        const text = $('body').text().replace(/\s+/g, ' ').trim().slice(0, 1000);
        const payload = {
            ok: true,
            url: request.loadedUrl ?? request.url,
            statusCode: response?.statusCode ?? null,
            title,
            excerpt: text,
            cached: false,
        };

        clearTimeout(pending.timeout);
        pendingResponses.delete(request.uniqueKey);
        inFlightCount = Math.max(0, inFlightCount - 1);
        responseCache.set(pending.cacheKey, {
            expiresAt: Date.now() + CACHE_TTL_MS,
            payload,
        });
        sendJson(pending.res, 200, payload);
    },
    async failedRequestHandler({ request, error }) {
        const pending = pendingResponses.get(request.uniqueKey);
        if (!pending) return;

        clearTimeout(pending.timeout);
        pendingResponses.delete(request.uniqueKey);
        inFlightCount = Math.max(0, inFlightCount - 1);
        sendJson(pending.res, 502, {
            ok: false,
            url: request.url,
            error: error.message,
        });
    },
});

const server = createServer(async (req, res) => {
    const urlObj = new URL(req.url ?? '/', `http://${req.headers.host ?? `localhost:${PORT}`}`);

    if (req.method !== 'POST' || urlObj.pathname !== '/fetch') {
        sendJson(res, 404, { ok: false, error: 'Use POST /fetch' });
        return;
    }

    let body;
    try {
        body = await readJsonBody(req);
    } catch {
        sendJson(res, 400, { ok: false, error: 'Body must be valid JSON' });
        return;
    }

    const rawUrl = body?.url;
    if (typeof rawUrl !== 'string') {
        sendJson(res, 400, { ok: false, error: 'Missing "url" string field' });
        return;
    }

    const normalizedUrl = normalizeHttpUrl(rawUrl);
    if (!normalizedUrl) {
        sendJson(res, 400, { ok: false, error: 'Only http/https URLs are allowed' });
        return;
    }

    sweepExpiredCache();
    const cached = responseCache.get(normalizedUrl);
    if (cached && cached.expiresAt > Date.now()) {
        sendJson(res, 200, { ...cached.payload, cached: true });
        return;
    }

    if (inFlightCount >= MAX_IN_FLIGHT) {
        sendJson(res, 429, { ok: false, error: 'Server is busy, please retry later' });
        return;
    }

    const allowed = await validateOutboundTarget(normalizedUrl);
    if (!allowed.ok) {
        sendJson(res, 403, { ok: false, error: allowed.reason });
        return;
    }

    const uniqueKey = randomUUID();
    const timeout = setTimeout(() => {
        const pending = pendingResponses.get(uniqueKey);
        if (!pending) return;
        pendingResponses.delete(uniqueKey);
        inFlightCount = Math.max(0, inFlightCount - 1);
        sendJson(pending.res, 504, { ok: false, error: 'Fetch timed out' });
    }, REQUEST_TIMEOUT_MS);

    inFlightCount += 1;
    pendingResponses.set(uniqueKey, { res, timeout, cacheKey: normalizedUrl });
    log.info(`Queued ${normalizedUrl}`);

    try {
        await crawler.addRequests([{ url: normalizedUrl, uniqueKey }]);
    } catch (error) {
        clearTimeout(timeout);
        pendingResponses.delete(uniqueKey);
        inFlightCount = Math.max(0, inFlightCount - 1);
        sendJson(res, 500, { ok: false, error: String(error) });
    }
});

server.listen(PORT, () => {
    log.info(`API server listening on http://localhost:${PORT}`);
});

await crawler.run();

function sendJson(res, status, data) {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(data));
}

function normalizeHttpUrl(value) {
    try {
        const parsed = new URL(value);
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
        return parsed.toString();
    } catch {
        return null;
    }
}

async function validateOutboundTarget(urlValue) {
    const parsed = new URL(urlValue);
    const hostname = parsed.hostname;

    if (isBlockedHostname(hostname)) {
        return { ok: false, reason: 'Blocked hostname' };
    }

    // Host is already an IP literal, check directly without DNS lookup.
    if (net.isIP(hostname) && isPrivateOrUnsafeIp(hostname)) {
        return { ok: false, reason: 'Blocked private or local IP address' };
    }

    let records;
    try {
        records = await dns.lookup(hostname, { all: true, verbatim: true });
    } catch {
        return { ok: false, reason: 'Hostname resolution failed' };
    }

    if (!records.length) {
        return { ok: false, reason: 'Hostname resolution returned no addresses' };
    }

    for (const record of records) {
        if (isPrivateOrUnsafeIp(record.address)) {
            return { ok: false, reason: 'Blocked private or local target address' };
        }
    }

    return { ok: true };
}

function isBlockedHostname(hostname) {
    const host = hostname.toLowerCase();
    if (host === 'localhost' || host.endsWith('.localhost')) return true;
    if (host.endsWith('.local') || host.endsWith('.internal')) return true;
    if (!host.includes('.')) return true;
    return false;
}

function isPrivateOrUnsafeIp(ip) {
    const version = net.isIP(ip);
    if (!version) return true;
    if (version === 4) return isPrivateIpv4(ip);
    return isPrivateIpv6(ip);
}

function isPrivateIpv4(ip) {
    const parts = ip.split('.').map(Number);
    if (parts.length !== 4 || parts.some((x) => !Number.isInteger(x) || x < 0 || x > 255)) return true;

    const int = ((parts[0] << 24) >>> 0) + (parts[1] << 16) + (parts[2] << 8) + parts[3];
    const inRange = (start, end) => int >= start && int <= end;

    if (inRange(ipv4ToInt('0.0.0.0'), ipv4ToInt('0.255.255.255'))) return true;
    if (inRange(ipv4ToInt('10.0.0.0'), ipv4ToInt('10.255.255.255'))) return true;
    if (inRange(ipv4ToInt('100.64.0.0'), ipv4ToInt('100.127.255.255'))) return true;
    if (inRange(ipv4ToInt('127.0.0.0'), ipv4ToInt('127.255.255.255'))) return true;
    if (inRange(ipv4ToInt('169.254.0.0'), ipv4ToInt('169.254.255.255'))) return true;
    if (inRange(ipv4ToInt('172.16.0.0'), ipv4ToInt('172.31.255.255'))) return true;
    if (inRange(ipv4ToInt('192.168.0.0'), ipv4ToInt('192.168.255.255'))) return true;
    if (inRange(ipv4ToInt('198.18.0.0'), ipv4ToInt('198.19.255.255'))) return true;
    if (inRange(ipv4ToInt('224.0.0.0'), ipv4ToInt('255.255.255.255'))) return true;
    return false;
}

function ipv4ToInt(ip) {
    const [a, b, c, d] = ip.split('.').map(Number);
    return ((a << 24) >>> 0) + (b << 16) + (c << 8) + d;
}

function isPrivateIpv6(ip) {
    const normalized = ip.toLowerCase().split('%')[0];
    if (normalized === '::1' || normalized === '::') return true;

    if (normalized.startsWith('::ffff:')) {
        const mapped = normalized.slice('::ffff:'.length);
        if (net.isIP(mapped) === 4) return isPrivateIpv4(mapped);
    }

    const expanded = expandIpv6(normalized);
    if (!expanded) return true;

    const first = expanded[0];
    const second = expanded[1];

    if ((first & 0xfe) === 0xfc) return true; // fc00::/7 (unique local)
    if (first === 0xfe && (second & 0xc0) === 0x80) return true; // fe80::/10 (link-local)
    if (first === 0xff) return true; // multicast
    return false;
}

function expandIpv6(ip) {
    const hasCompression = ip.includes('::');
    const [leftPart = '', rightPart = ''] = ip.split('::');
    const left = leftPart ? leftPart.split(':') : [];
    const right = rightPart ? rightPart.split(':') : [];

    if (hasCompression) {
        const missing = 8 - (left.length + right.length);
        if (missing < 0) return null;
        const full = [...left, ...Array(missing).fill('0'), ...right].map((x) => x || '0');
        if (full.length !== 8) return null;
        return full.map((x) => parseInt(x, 16)).flatMap((n) => [n >> 8, n & 0xff]);
    }

    if (left.length !== 8) return null;
    return left.map((x) => parseInt(x, 16)).flatMap((n) => [n >> 8, n & 0xff]);
}

function sweepExpiredCache() {
    const now = Date.now();
    for (const [key, value] of responseCache.entries()) {
        if (value.expiresAt <= now) {
            responseCache.delete(key);
        }
    }
}

function readJsonBody(req) {
    return new Promise((resolve, reject) => {
        let raw = '';
        req.on('data', (chunk) => {
            raw += chunk;
            if (raw.length > 1_000_000) {
                reject(new Error('Body too large'));
                req.destroy();
            }
        });
        req.on('end', () => {
            try {
                resolve(raw ? JSON.parse(raw) : {});
            } catch (error) {
                reject(error);
            }
        });
        req.on('error', reject);
    });
}
