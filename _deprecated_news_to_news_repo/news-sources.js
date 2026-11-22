/* ============================================================
 * MACRO PULSE · 10源云端抓取模块（v452）
 * 全部从前端 news-live-client.js 逐字迁移，运行在 CF Worker（海外IP）。
 * Worker 无 CORS 限制 → 前端需要 jina/代理的源，这里直接 fetch。
 * 只有财联社需签名 md5(sha1(sorted_query))，内置紧凑 MD5 实现。
 * 输出统一结构 {id, ts, title, brief, src}（分级在 driveNewsCron 里做）。
 * ============================================================ */

const SRC_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36';

function clean(s) {
  return String(s == null ? '' : s)
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ').trim();
}
function stripHtml(s) { return String(s || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(); }
function hashStr(s) {
  let h = 0; const str = String(s || '');
  for (let i = 0; i < str.length; i++) { h = ((h << 5) - h + str.charCodeAt(i)) | 0; }
  return h;
}
function firstSentence(s, max) {
  const t = String(s || '').trim();
  const m = t.match(/^([^。！？!?；;\n]{1,N})/);
  let out = (m ? m[1] : t).trim();
  if (out.length > (max || 60)) out = out.slice(0, max || 60);
  return out;
}
function tsOf(v) {
  const s = String(v || '').trim();
  if (!s) return Date.now();
  if (/^\d+$/.test(s)) { const n = +s; return s.length > 11 ? n : n * 1000; }
  // v455【时间bug修复】金十 "2026-11-19 10:16:04" / 东财 showTime 都是北京时间字符串，
  // 无显式时区。CF Worker 运行时是 UTC，Date.parse 会当成 UTC → 显示时间多8小时
  // （昨夜出现"10:16"固定时间、未来时间701条的根因）。这里按北京时间解析：先当UTC解析再减8h。
  // 带显式时区的字符串（如 +0800）交给 Date.parse 自己处理，不减。
  const hasTZ = /([+-]\d{2}:?\d{2}|Z|UTC|GMT)$/i.test(s);
  const t = Date.parse(s.replace(/-/g, '/'));
  if (!isFinite(t)) return Date.now();
  return hasTZ ? t : t - 8 * 3600 * 1000;
}
function parseRssTime(s) {
  const m = /(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?\s*([+-]\d{4})?/.exec(String(s || ''));
  if (!m) return Date.now();
  const MON = { Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11 };
  const mon = MON[m[2]]; if (mon === undefined) return Date.now();
  const tz = m[7] ? m[7].replace(/^([+-]\d{2})(\d{2})$/, '$1:$2') : '+08:00';
  const t = Date.parse(m[3] + '-' + String(mon + 1).padStart(2, '0') + '-' + String(m[1]).padStart(2, '0')
    + 'T' + m[4] + ':' + m[5] + ':' + (m[6] || '00') + tz);
  return isFinite(t) ? t : Date.now();
}

async function fetchText(url, timeout, headers, method, body) {
  const ctl = new AbortController();
  const tm = setTimeout(() => ctl.abort(), timeout || 12000);
  try {
    const r = await fetch(url, { method: method || 'GET', signal: ctl.signal,
      headers: Object.assign({ 'User-Agent': SRC_UA, 'Accept': '*/*' }, headers || {}),
      body: body, cf: { cacheTtl: 0, cacheEverything: false } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.text();
  } finally { clearTimeout(tm); }
}
async function fetchJSON(url, timeout, headers, method, body) {
  const t = await fetchText(url, timeout, headers, method, body);
  const i = t.indexOf('{');
  return JSON.parse(i >= 0 ? t.slice(i) : t);
}

/* ---- 紧凑 MD5（财联社签名用；返回32位小写hex） ---- */
function md5Hex(input) {
  const s = unescape(encodeURIComponent(String(input)));
  const len = s.length;
  // v452修复：buffer 必须是 64 的整数倍且能容纳 len+1(0x80)+8(bitlen) 字节。
  // 原来 ((len+8)>>6)*64+56 在 len 接近块边界时少一个字节 → DataView offset 越界。
  const bufLen = (Math.floor((len + 8) / 64) + 1) * 64;
  const bytes = new Uint8Array(bufLen);
  for (let i = 0; i < len; i++) bytes[i] = s.charCodeAt(i) & 0xff;
  bytes[len] = 0x80;
  const bitLen = len * 8;
  const dv = new DataView(bytes.buffer);
  dv.setUint32(bytes.length - 8, bitLen >>> 0, true);
  dv.setUint32(bytes.length - 4, Math.floor(bitLen / 4294967296), true);
  const S = [7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21];
  const K = [];
  for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296) >>> 0;
  let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
  const rotl = (x, n) => ((x << n) | (x >>> (32 - n))) >>> 0;
  for (let off = 0; off < bytes.length; off += 64) {
    let A = a0, B = b0, C = c0, D = d0;
    for (let i = 0; i < 64; i++) {
      let F, g;
      if (i < 16) { F = (B & C) | (~B & D); g = i; }
      else if (i < 32) { F = (D & B) | (~D & C); g = (5 * i + 1) % 16; }
      else if (i < 48) { F = B ^ C ^ D; g = (3 * i + 5) % 16; }
      else { F = C ^ (B | ~D); g = (7 * i) % 16; }
      F = (F + A + K[i] + dv.getUint32(off + g * 4, true)) >>> 0;
      A = D; D = C; C = B; B = (B + rotl(F, S[i])) >>> 0;
    }
    a0 = (a0 + A) >>> 0; b0 = (b0 + B) >>> 0; c0 = (c0 + C) >>> 0; d0 = (d0 + D) >>> 0;
  }
  const hx = (n) => { let s2 = ''; for (let i = 0; i < 4; i++) { const b = (n >>> (i * 8)) & 0xff; s2 += (b < 16 ? '0' : '') + b.toString(16); } return s2; };
  return hx(a0) + hx(b0) + hx(c0) + hx(d0);
}
async function sha1Hex(s) {
  const buf = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(String(s)));
  return [...new Uint8Array(buf)].map(b => (b < 16 ? '0' : '') + b.toString(16)).join('');
}

/* ================= 10源抓取函数 ================= */

/* 1. 金十数据：flash_newest.js 里的 var newest=[...] */
async function fetchJin10() {
  const raw = await fetchText('https://www.jin10.com/flash_newest.js', 12000);
  const i = raw.indexOf('['), j = raw.lastIndexOf(']');
  if (i < 0 || j <= i) throw new Error('金十无 newest 数组');
  const arr = JSON.parse(raw.slice(i, j + 1));
  const out = [];
  for (const it of arr) {
    const d = it && it.data ? it.data : {};
    const content = clean(d.content || '');
    if (!content) continue;
    if (/^(重要新闻|个股新闻|热点新闻)\d*[.、．]/.test(content)) continue;
    let title = clean(d.title || ''), brief = content;
    if (!title) {
      const m = /^【(.+?)】\s*([\s\S]*)$/.exec(content);
      if (m) { title = clean(m[1]); brief = clean(m[2]) || content; }
      else title = firstSentence(content, 60);
    } else if (content.indexOf(title) === 0) brief = content;
    if (!title) continue;
    out.push({ id: 'j10-' + (it.id || Math.abs(hashStr(title))), ts: tsOf(it.time),
      title, brief, src: '金十数据' });
  }
  if (!out.length) throw new Error('金十无有效条目');
  return out;
}

/* 2. 华尔街见闻：两频道并行（Worker 无CORS，直接fetch） */
async function fetchWSCN() {
  const chans = ['global-channel', 'a-stock-channel'];
  const packs = await Promise.all(chans.map(async ch => {
    try {
      const d = await fetchJSON('https://api-one.wallstcn.com/apiv1/content/lives?channel=' + ch + '&client=pc&limit=50', 10000);
      return (d && d.data && d.data.items) || [];
    } catch (e) { return []; }
  }));
  const seen = new Set(), items = [];
  packs.forEach(list => list.forEach(it => {
    const k = String((it && it.id) || '');
    if (k && seen.has(k)) return;
    if (k) seen.add(k);
    items.push(it);
  }));
  if (!items.length) throw new Error('华尔街见闻无条目');
  items.sort((a, b) => (Number(b.display_time) || 0) - (Number(a.display_time) || 0));
  return items.map(it => {
    const text = clean(it.content_text || it.content || '');
    let title = clean(it.title || ''), brief = text;
    if (!title) { title = firstSentence(text, 60); brief = text; }
    else if (text.indexOf(title) === 0) brief = text;
    if (!title) return null;
    return { id: 'ws-' + it.id, ts: it.display_time ? it.display_time * 1000 : Date.now(),
      title, brief, src: '华尔街见闻' };
  }).filter(Boolean);
}

/* 3a. 财联社电报：签名 + 翻页（Worker 直接 fetch api3.cls.cn） */
function clsSignedUrl(lastTime, rn) {
  const p = { app: 'CailianpressWeb', category: '', os: 'web', refresh_type: '1',
    rn: String(rn || 50), sv: '7.7.5', last_time: String(lastTime || Math.floor(Date.now() / 1000)) };
  const q = Object.keys(p).sort().map(k => k + '=' + p[k]).join('&');
  return q; // sign 是异步算的，见下
}
async function fetchCls() {
  const now = Math.floor(Date.now() / 1000);
  const floor = now - 12 * 3600;
  const out = [], seen = new Set();
  let last = now, pages = 0;
  for (; pages < 6; pages++) {
    const p = { app: 'CailianpressWeb', category: '', os: 'web', refresh_type: '1',
      rn: '50', sv: '7.7.5', last_time: String(last) };
    const q = Object.keys(p).sort().map(k => k + '=' + p[k]).join('&');
    const sign = md5Hex(await sha1Hex(q));
    let data;
    try { data = await fetchJSON('https://api3.cls.cn/v1/roll/get_roll_list?' + q + '&sign=' + sign, 15000); }
    catch (e) { if (!out.length) throw e; break; }
    if (data.errno && data.errno !== 0) { if (!out.length) throw new Error('财联社 errno=' + data.errno); break; }
    const list = (data.data && data.data.roll_data) || [];
    if (!list.length) break;
    let oldest = last;
    for (const it of list) {
      const ct = Number(it.ctime || 0);
      if (ct && ct < oldest) oldest = ct;
      const id = 'cls-' + String(it.id || Math.abs(hashStr(it.content || '')));
      if (seen.has(id)) continue;
      seen.add(id);
      const content = clean(it.content || '');
      if (!content) continue;
      let title = clean(it.title || ''), brief = content;
      if (!title) {
        const m = /^【(.+?)】\s*([\s\S]*)$/.exec(content);
        if (m) { title = clean(m[1]); brief = clean(m[2]); }
        else title = firstSentence(content, 60);
      }
      if (!title) continue;
      out.push({ id, ts: ct ? ct * 1000 : Date.now(), title, brief, src: '财联社' });
    }
    if (!oldest || oldest <= floor) break;
    last = oldest - 1;
  }
  if (!out.length) throw new Error('财联社无有效条目');
  return out;
}

/* 3b. 财联社深度+要闻：两个 cid 并行 */
async function fetchClsDepth() {
  const JOBS = [{ cid: 1000, name: '财联社深度', pre: 'clsd-' }, { cid: 1001, name: '财联社要闻', pre: 'clsn-' }];
  const floor = Date.now() - 24 * 3600 * 1000;
  const packs = await Promise.all(JOBS.map(async j => {
    try {
      const p = { app: 'CailianpressWeb', os: 'web', sv: '7.7.5' };
      const q = Object.keys(p).sort().map(k => k + '=' + p[k]).join('&');
      const sign = md5Hex(await sha1Hex(q));
      const d = await fetchJSON('https://api3.cls.cn/v3/depth/home/assembled/' + j.cid + '?' + q + '&sign=' + sign, 15000);
      return { j, list: (d.data && d.data.depth_list) || [] };
    } catch (e) { return { j, list: [] }; }
  }));
  const out = [];
  for (const { j, list } of packs) {
    for (const it of list) {
      if (Number(it.is_ad) === 1 || (it.ad && Number(it.ad.id) > 0)) continue;
      const title = clean(it.title || '');
      if (!title) continue;
      const ct = Number(it.ctime || 0) * 1000;
      if (ct && ct < floor) continue;
      if (/^(一图看懂|【?早报】?|港股早报|券商晨会|债市公告|债市早参|投资避雷针|投资日历|行业动态|盘前必读|复盘)/.test(title)) continue;
      out.push({ id: j.pre + String(it.id || Math.abs(hashStr(title))), ts: ct || Date.now(),
        title, brief: clean(it.brief || ''), src: j.name });
    }
  }
  if (!out.length) throw new Error('财联社深度无条目');
  return out;
}

/* 4. 格隆汇：m.gelonghui.com/live SSR HTML 解析（Worker 直接fetch，不需要 jina） */
function parseGlhTime(txt) {
  const s = String(txt || '').trim(), now = Date.now();
  if (!s || /刚刚/.test(s)) return now;
  let m = /(\d+)\s*分钟前/.exec(s); if (m) return now - Number(m[1]) * 60000;
  m = /(\d+)\s*小时前/.exec(s); if (m) return now - Number(m[1]) * 3600000;
  if (/昨天/.test(s)) return now - 24 * 3600 * 1000;
  m = /(\d{1,2}):(\d{2})/.exec(s);
  if (m) { const d = new Date(); d.setHours(+m[1], +m[2], 0, 0); let t = d.getTime(); if (t > now + 30 * 60e3) t -= 24 * 3600e3; return t; }
  return now;
}
async function fetchGelonghui() {
  const h = await fetchText('https://m.gelonghui.com/live', 18000,
    { 'x-respond-with': 'html' });
  if (h.indexOf('live-card') < 0) throw new Error('格隆汇未返回SSR页面');
  const chunks = h.split(/<div[^>]*class="[^"]*\blive-card\b/);
  const out = [], seen = new Set(), floor = Date.now() - 24 * 3600e3;
  for (let i = 1; i < chunks.length; i++) {
    const c = chunks[i];
    const tm = /class="[^"]*\stime"[^>]*>([^<]*)</.exec(c);
    const ti = /class="[^"]*\stitle[^"]*"[^>]*>([^<]*)</.exec(c);
    const ct = /class="[^"]*\bcontent\b[^"]*"[^>]*>([\s\S]*?)<\/div>/.exec(c);
    const title = clean(stripHtml(ti ? ti[1] : ''));
    if (!title) continue;
    const brief = clean(stripHtml(ct ? ct[1] : '')) || title;
    const ts = parseGlhTime(tm ? tm[1] : '');
    if (ts < floor) continue;
    const id = 'glh-' + Math.abs(hashStr(title + '|' + ts));
    if (seen.has(id)) continue;
    seen.add(id);
    out.push({ id, ts, title, brief, src: '格隆汇' });
  }
  if (!out.length) throw new Error('格隆汇无有效条目');
  return out;
}

/* 5. 同花顺：CORS全开 JSON */
async function fetchTHS() {
  const d = await fetchJSON('https://news.10jqka.com.cn/tapp/news/push/stock/?page=1&tag=&track=website&pagesize=50', 10000);
  const list = ((d.data || {}).list) || [];
  return list.map(it => {
    const title = clean(it.title || ''), digest = clean(it.digest || '');
    if (!title) return null;
    const t = Number(it.ctime || it.rtime || 0) * 1000;
    return { id: 'ths-' + (it.id || Math.abs(hashStr(title))), ts: t || Date.now(),
      title, brief: digest.indexOf(title) === 0 ? digest.slice(title.length).trim() : (digest || title),
      src: '同花顺' };
  }).filter(Boolean);
}

/* 6. 东方财富 7×24（东财源——CF海外IP，符合用户"CF跑批直连东财没问题"） */
async function fetchEastmoney() {
  const url = 'https://np-listapi.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724&fastColumn=102&sortEnd=&pageSize=50&req_trace=' + Date.now() + '00';
  const d = await fetchJSON(url, 10000);
  const list = (d.data && d.data.fastNewsList) || [];
  return list.map(it => {
    let title = clean(it.title || '');
    const summary = clean(it.summary || '');
    if (!title && summary) title = firstSentence(summary, 60);
    if (!title) return null;
    return { id: 'em-' + (it.code || Math.abs(hashStr(title))), ts: tsOf(it.showTime),
      title, brief: summary || title, src: '东方财富' };
  }).filter(Boolean);
}

/* 7. 新浪财经 7×24（JSONP → 直接取JSON部分） */
async function fetchSina() {
  const raw = await fetchText('https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=30&zhibo_id=152&tag_id=0&dire=f&dpc=1', 10000,
    { Referer: 'https://finance.sina.com.cn/7x24/' });
  // 兼容三种返回：JSONP(callback({...})) / 裸JSON / var xxx={...};
  // 有时外层还被包成JSON字符串（\"新华社\"变\\u65b0...），先剥一层。
  let body = raw.trim();
  const i = body.indexOf('('), j = body.lastIndexOf(')');
  if (i >= 0 && j > i) body = body.slice(i + 1, j);
  let d;
  try { d = JSON.parse(body); }
  catch (e1) {
    // 前面有 var 赋值 / 后面有分号 → 截取第一个 { 到最后一个 }
    const k1 = body.indexOf('{'), k2 = body.lastIndexOf('}');
    const inner = k1 >= 0 && k2 > k1 ? body.slice(k1, k2 + 1) : body;
    d = JSON.parse(inner);   // 若是被包成字符串的情况，这里会解析成对象或字符串
    if (typeof d === 'string') d = JSON.parse(d);
  }
  const feed = (((d.result || {}).data || {}).feed) || {};
  return (feed.list || []).map(it => {
    const text = clean(it.rich_text || '');
    if (!text) return null;
    let title = '', brief = text;
    const m = /^【(.+?)】\s*([\s\S]*)$/.exec(text);
    if (m) { title = clean(m[1]); brief = clean(m[2]); }
    if (!title) title = firstSentence(text, 60);
    return { id: 'sn-' + (it.id || Math.abs(hashStr(title))), ts: tsOf(it.create_time),
      title, brief, src: '新浪财经' };
  }).filter(Boolean);
}

/* 8. 口罩哥KZG：老虎社区 API 直连（Worker 直接POST） */
async function fetchKZG() {
  const KZG_UID = '172401613997661';
  const hex = (s) => { const v = hashStr(s) >>> 0; return v.toString(16).padStart(8, '0'); };
  const dev = 'web-' + hex('mp-' + KZG_UID) + '-' + hex(String(Date.now())) + '-' + hex(String(Math.random()));
  const url = 'https://community-service.laohu8.com/v5/tweet/user/list?lang=zh_CN&lang_content=all&region=CHN&deviceId=' + dev
    + '&appVer=4.52.0&appName=laohu8&vendor=web&platform=web&edition=full';
  const j = await fetchJSON(url, 12000, { 'Content-Type': 'application/json' }, 'POST',
    JSON.stringify({ pageCount: 1, pageSize: 12, authorId: KZG_UID }));
  if (!j || j.success !== true) throw new Error('KZG接口失败');
  const list = (j.data && j.data.list) || [];
  return list.map(t => {
    const title = clean(t.title || '');
    if (!title) return null;
    const body = clean(String(t.htmlText || '').replace(/<[^>]+>/g, ' '));
    return { id: 'kzg-' + String(t.id || Math.abs(hashStr(title))), ts: Number(t.gmtCreate || 0) || Date.now(),
      title, brief: body || title, src: '口罩哥KZG' };
  }).filter(Boolean);
}

/* 9. 川普言论：trumpstruth.org/feed RSS（Worker 直接fetch，无需 jina） */
async function fetchTrump() {
  const xml = await fetchText('https://www.trumpstruth.org/feed', 18000);
  const cut = Math.max(xml.indexOf('<?xml'), xml.indexOf('<rss'));
  const raw = cut > 0 ? xml.slice(cut) : xml;
  const items = [...raw.matchAll(/<item>([\s\S]*?)<\/item>/gi)].map(m => m[1]);
  if (!items.length) throw new Error('川普RSS无条目');
  const q = (block, tag) => {
    const m = new RegExp('<' + tag + '[^>]*>([\\s\\S]*?)<\\/' + tag + '>', 'i').exec(block);
    return m ? clean(m[1].replace(/<!\[CDATA\[|\]\]>/g, '')) : '';
  };
  const out = [];
  for (const block of items) {
    let body = stripHtml(q(block, 'description') || q(block, 'title')) || q(block, 'title');
    body = String(body).replace(/View image|View video|\s+/g, ' ').trim();
    if (!body || body.length < 8 || /^Image post from/i.test(body)) continue;
    const link = q(block, 'link');
    out.push({ id: 'trump-' + Math.abs(hashStr(link || body)), ts: parseRssTime(q(block, 'pubDate')),
      title: body.length > 90 ? body.slice(0, 90) + '…' : body, brief: body, src: '川普言论' });
    if (out.length >= 12) break;
  }
  if (!out.length) throw new Error('川普RSS解析后为空');
  return out;
}

/* 10源抓取清单（逐一核对，10/10） */
const CLOUD_SOURCES = [
  ['金十数据', fetchJin10],
  ['华尔街见闻', fetchWSCN],
  ['财联社', fetchCls],
  ['财联社深度', fetchClsDepth],
  ['格隆汇', fetchGelonghui],
  ['同花顺', fetchTHS],
  ['东方财富', fetchEastmoney],
  ['口罩哥KZG', fetchKZG],
  ['川普言论', fetchTrump],
  ['新浪财经', fetchSina],
];

async function fetchAllCloudSources() {
  const results = await Promise.allSettled(CLOUD_SOURCES.map(([name, fn]) =>
    Promise.race([fn(), new Promise((_, rej) => setTimeout(() => rej(new Error('源超时20s')), 20000))])));
  const all = [], detail = [];
  results.forEach((r, i) => {
    const name = CLOUD_SOURCES[i][0];
    if (r.status === 'fulfilled' && Array.isArray(r.value)) {
      all.push(...r.value);
      detail.push(name + ':' + r.value.length);
    } else {
      detail.push(name + ':FAIL(' + String((r.reason && r.reason.message) || r.reason).slice(0, 50) + ')');
    }
  });
  return { items: all, detail, okCount: detail.filter(d => !d.includes('FAIL')).length };
}
