/**
 * News Grasp Service Worker
 *
 * 戦略:
 *   - HTML (navigation): network-first → cache fallback → offline.html
 *     (digest が毎朝更新されるため stale 表示を避ける)
 *   - 静的アセット (CSS / JS / 画像 / フォント): stale-while-revalidate
 *   - 同一 origin (`/News-Grasp/` 配下) のみ intercept。
 *     Google Fonts など cross-origin は SW を素通しさせブラウザに任せる。
 *
 *  キャッシュ名に SW_VERSION を含めることで、デプロイ時に SW_VERSION を上げれば
 *  activate 時に古いキャッシュをまとめて削除できる。
 */

const SW_VERSION = '2026-07-02-category-hero-r6-2026-07-04-daily-recovery-r1-2026-07-11-incident-r1-2026-07-12-editor-recovery-r1-2026-07-13-daily-recovery-r1-2026-07-15-slo-report-r1-2026-07-16-pytest-basetemp-r1-2026-07-17-hero-line-report-r1-2026-07-18-hero-policy-report-r1-2026-07-19-daily-quality-repair-r1-2026-07-20-retry-r1-2026-07-21-digest-sync-r1-2026-07-22-structured-repair-r1-2026-07-23-search-audit-hero-r1-2026-07-24-rootfix-r1-2026-07-26-stale-lock-playlist-r1-2026-08-01-deepdive-quality-r1-2026-08-03-summary-headline-r2-2026-08-05-stop-point-rebuild-r1-2026-08-09-summary-drift-r1-2026-08-12-constitution-r1-2026-08-13-control-plane-r2-2026-08-15-recovery-slo-r1-2026-08-27-public-recovery-closeout-r1-2026-08-29-direct-publish-r2-2026-08-30-direct-mainline-r1-2026-08-31-direct-mainline-r1-2026-09-01-deepdive-quality-v2-r1-2026-09-01-direct-mainline-r1-2026-09-02-direct-mainline-r1-2026-09-02-final-public-r1-2026-09-02-direct-scope-r1-2026-09-02-final-public-r2-2026-09-03-direct-mainline-r1-2026-09-03-direct-mainline-r2';
const SCOPE_PREFIX = '/News-Grasp/';
const HTML_CACHE = `news-grasp-html-${SW_VERSION}`;
const ASSET_CACHE = `news-grasp-assets-${SW_VERSION}`;
const PRECACHE = `news-grasp-precache-${SW_VERSION}`;

// install 時に最小限を事前キャッシュ (オフラインで Home が見える保証)
const PRECACHE_URLS = [
  '/News-Grasp/',
  '/News-Grasp/manifest.webmanifest',
  '/News-Grasp/assets/site.css',
  '/News-Grasp/assets/icons/icon-192.png',
  '/News-Grasp/assets/icons/icon-512.png',
  '/News-Grasp/offline.html',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(PRECACHE);
      // 失敗ファイルがあっても install は通す (個別 fetch でログのみ)
      await Promise.all(
        PRECACHE_URLS.map(async (url) => {
          try {
            const res = await fetch(url, { cache: 'reload' });
            if (res.ok) await cache.put(url, res);
          } catch (e) {
            // ignore
          }
        }),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => k.startsWith('news-grasp-') && ![HTML_CACHE, ASSET_CACHE, PRECACHE].includes(k))
          .map((k) => caches.delete(k)),
      );
      await self.clients.claim();
    })(),
  );
});

function isHtmlNavigation(request) {
  return request.mode === 'navigate' || (request.method === 'GET' && request.headers.get('accept')?.includes('text/html'));
}

function isSameScope(url) {
  return url.origin === self.location.origin && url.pathname.startsWith(SCOPE_PREFIX);
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (!isSameScope(url)) return; // cross-origin は素通し

  if (isHtmlNavigation(request)) {
    event.respondWith(networkFirstHTML(request));
    return;
  }
  event.respondWith(staleWhileRevalidate(request));
});

async function networkFirstHTML(request) {
  const cache = await caches.open(HTML_CACHE);
  try {
    const fresh = await fetch(request);
    if (fresh.ok) cache.put(request, fresh.clone());
    return fresh;
  } catch (e) {
    const cached = await cache.match(request);
    if (cached) return cached;
    const precache = await caches.open(PRECACHE);
    const offline = await precache.match('/News-Grasp/offline.html');
    return offline || new Response('オフラインです', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(ASSET_CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((res) => {
      if (res && res.ok) cache.put(request, res.clone());
      return res;
    })
    .catch(() => null);
  return cached || network || new Response('', { status: 504 });
}

// ---------------------------------------------------------------------------
// Web Push: 毎朝の digest 更新を「読んでみて！」と通知する。
//   - 送信側は tools/send_push.py (pywebpush + VAPID) が data に
//     {title, body, url} を JSON で詰めて push する。
//   - tag を固定することで通知が積み上がらず、毎朝 1 件に置き換わる。
// ---------------------------------------------------------------------------
self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    // JSON でなければ本文テキストとして扱う
    payload = { body: event.data ? event.data.text() : '' };
  }

  const title = payload.title || 'News Grasp';
  const options = {
    body: payload.body || '本日のニュースダイジェストが更新されました。読んでみて！',
    icon: '/News-Grasp/assets/icons/icon-192.png',
    badge: '/News-Grasp/assets/icons/icon-192.png',
    lang: 'ja',
    tag: 'news-grasp-daily', // 同タグは置き換え → 毎朝 1 件に保つ
    renotify: true,
    data: { url: payload.url || '/News-Grasp/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl =
    (event.notification.data && event.notification.data.url) || '/News-Grasp/';

  event.waitUntil(
    (async () => {
      const all = await clients.matchAll({ type: 'window', includeUncontrolled: true });
      // 既に開いている News Grasp のタブ/PWA があればフォーカスして遷移
      for (const client of all) {
        if (client.url.includes(SCOPE_PREFIX) && 'focus' in client) {
          await client.focus();
          if ('navigate' in client) {
            try {
              await client.navigate(targetUrl);
            } catch (e) {
              // navigate 不可 (古いブラウザ等) は focus のみで許容
            }
          }
          return;
        }
      }
      if (clients.openWindow) await clients.openWindow(targetUrl);
    })(),
  );
});
