/**
 * News Grasp — Push 購読ストア (Cloudflare Worker + KV)
 *
 * 静的サイト (GitHub Pages) には書き込み口が無いため、ブラウザが生成した
 * PushSubscription を保存する極小の受け口をこの Worker が担う。これにより
 * ユーザーは「通知を許可」を押すだけで購読が完結する（手動コピー不要）。
 *
 * エンドポイント:
 *   POST /subscribe    body = PushSubscription JSON          → KV に保存 (公開)
 *   POST /unsubscribe  body = {"endpoint": "..."}            → KV から削除 (公開)
 *   GET  /list?token=  token が LIST_TOKEN と一致したときのみ → 全購読を JSON 配列で返す
 *
 * 秘密情報はこの Worker には VAPID 鍵を置かない。送信は Runner ローカルの
 * tools/send_push.py が /list で一覧を取得して行う。Worker が持つ秘密は
 * 受信者リストを守る LIST_TOKEN のみ。
 *
 * バインディング: KV namespace `SUBS` / secret `LIST_TOKEN`
 */

// 購読 UI を載せる本番オリジン（CORS 許可先）
const ALLOWED_ORIGIN = 'https://hidepon-umg.github.io';
const KEY_PREFIX = 'sub:';

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
}

function jsonResponse(obj, status, extraHeaders) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...corsHeaders(), ...extraHeaders, 'Content-Type': 'application/json' },
  });
}

// KV キー = endpoint の SHA-256 hex。endpoint は長く（512B 制限）・記号を含むため。
async function endpointKey(endpoint) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint));
  const hex = [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
  return KEY_PREFIX + hex;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders() });
    }

    // 購読登録（ユーザーの「許可」押下で自動 POST される）
    if (url.pathname === '/subscribe' && request.method === 'POST') {
      let sub;
      try {
        sub = await request.json();
      } catch {
        return jsonResponse({ error: 'invalid json' }, 400);
      }
      // endpoint は push サービスの https URL のみ受理（任意文字列の混入を防ぐ）。
      // 既知ホストの許可リストは正規端末を誤って弾く恐れがあるため敢えて使わない。
      if (!sub || typeof sub.endpoint !== 'string' || !sub.endpoint.startsWith('https://')) {
        return jsonResponse({ error: 'invalid subscription' }, 400);
      }
      const serialized = JSON.stringify(sub);
      // 異常に大きいペイロードは弾く（KV 肥大・いたずら対策。正規購読は ~1KB 未満）
      if (serialized.length > 2048) {
        return jsonResponse({ error: 'subscription too large' }, 413);
      }
      await env.SUBS.put(await endpointKey(sub.endpoint), serialized);
      return jsonResponse({ ok: true }, 200);
    }

    // 購読解除（ユーザーが通知オフ / Runner が失効購読を掃除）
    if (url.pathname === '/unsubscribe' && request.method === 'POST') {
      let body;
      try {
        body = await request.json();
      } catch {
        return jsonResponse({ error: 'invalid json' }, 400);
      }
      if (!body || typeof body.endpoint !== 'string') {
        return jsonResponse({ error: 'no endpoint' }, 400);
      }
      await env.SUBS.delete(await endpointKey(body.endpoint));
      return jsonResponse({ ok: true }, 200);
    }

    // 全購読の取得（受信者リスト = 要保護。Runner だけがトークンで叩く）
    if (url.pathname === '/list' && request.method === 'GET') {
      const token = url.searchParams.get('token');
      if (!env.LIST_TOKEN || token !== env.LIST_TOKEN) {
        return jsonResponse({ error: 'unauthorized' }, 401);
      }
      const subs = [];
      let cursor;
      // KV は 1000 件/ページ。カーソルで全ページ走査する。
      for (;;) {
        const res = await env.SUBS.list({ prefix: KEY_PREFIX, cursor });
        for (const k of res.keys) {
          const v = await env.SUBS.get(k.name);
          if (v) subs.push(JSON.parse(v));
        }
        if (res.list_complete) break;
        cursor = res.cursor;
      }
      return jsonResponse(subs, 200);
    }

    return jsonResponse({ error: 'not found' }, 404);
  },
};
