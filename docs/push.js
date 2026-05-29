/* News Grasp — Web Push 購読クライアント（セルフサービス）
 *
 * ユーザーは「通知を受け取る」を押すだけで購読が完結する:
 *   通知許可 → PushManager.subscribe → 購読情報を Worker に自動 POST。
 * 手動コピーや管理人への連絡は不要。もう一度押すと購読解除。
 *
 * 保存先は Cloudflare Worker (+ KV)。毎朝の Runner (tools/send_push.py) が
 * Worker から購読一覧を取得して送信する。
 */
(function () {
  'use strict';

  // tools/gen_vapid_keys.py が出力した application server key（公開鍵 / base64url）。
  var VAPID_PUBLIC_KEY =
    'BHY4OrsGBB4fh8yGdvMj5Kz_huxdh0kchjRoPnC15oZ6oe3QYWlrQgl3_HYUhO3OjDd3ATvJZgR-TXk16bPfZ0E';

  // 購読保存先 Worker の URL。`cd worker && npx wrangler deploy` で表示された
  // *.workers.dev の URL に差し替える（末尾スラッシュ無し）。
  var WORKER_URL = 'https://news-grasp-push.YOUR-SUBDOMAIN.workers.dev';

  function workerConfigured() {
    return WORKER_URL.indexOf('YOUR-SUBDOMAIN') === -1;
  }

  function urlBase64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var rawData = atob(base64);
    var output = new Uint8Array(rawData.length);
    for (var i = 0; i < rawData.length; ++i) output[i] = rawData.charCodeAt(i);
    return output;
  }

  function setStatus(msg) {
    var el = document.getElementById('push-status');
    if (el) el.textContent = msg;
  }

  function setButtonLabel(subscribed) {
    var btn = document.getElementById('push-subscribe-btn');
    if (btn) btn.textContent = subscribed ? '更新通知をオフにする' : 'スマホに更新通知を受け取る';
  }

  function supported() {
    return 'serviceWorker' in navigator && 'PushManager' in window;
  }

  async function postToWorker(path, body) {
    var res = await fetch(WORKER_URL + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error('worker responded ' + res.status);
    return res.json();
  }

  async function doSubscribe() {
    var permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      setStatus('通知が許可されませんでした。ブラウザの設定から許可し直してください。');
      return;
    }
    var reg = await navigator.serviceWorker.ready;
    var sub =
      (await reg.pushManager.getSubscription()) ||
      (await reg.pushManager.subscribe({
        userVisibleOnly: true, // Web Push 仕様上 true 必須
        applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
      }));
    await postToWorker('/subscribe', sub.toJSON());
    setButtonLabel(true);
    setStatus('通知をオンにしました。毎朝の更新をお知らせします。');
  }

  async function doUnsubscribe() {
    var reg = await navigator.serviceWorker.ready;
    var sub = await reg.pushManager.getSubscription();
    if (sub) {
      var endpoint = sub.endpoint;
      await sub.unsubscribe();
      try {
        await postToWorker('/unsubscribe', { endpoint: endpoint });
      } catch (e) {
        // Worker 側削除に失敗しても次回送信時に 410 で自動掃除されるため握りつぶす
      }
    }
    setButtonLabel(false);
    setStatus('通知をオフにしました。');
  }

  async function onClick() {
    if (!supported()) {
      setStatus('この端末/ブラウザは Web Push に対応していません。');
      return;
    }
    if (!workerConfigured()) {
      setStatus('通知の準備中です（管理人のセットアップ待ち）。しばらくお待ちください。');
      return;
    }
    try {
      var reg = await navigator.serviceWorker.ready;
      var existing = await reg.pushManager.getSubscription();
      if (existing) {
        await doUnsubscribe();
      } else {
        await doSubscribe();
      }
    } catch (err) {
      console.error('[News Grasp push]', err);
      setStatus('処理に失敗しました: ' + (err && err.message ? err.message : err));
    }
  }

  document.addEventListener('DOMContentLoaded', async function () {
    var btn = document.getElementById('push-subscribe-btn');
    if (btn) btn.addEventListener('click', onClick);

    if (!supported()) {
      setStatus('この端末/ブラウザは Web Push に対応していません。');
      return;
    }

    // iOS Safari は「ホーム画面に追加」した standalone PWA でのみ Push 可能。
    var standalone =
      window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true;
    var isiOS = /iP(hone|ad|od)/.test(navigator.userAgent);
    if (isiOS && !standalone) {
      setStatus('iPhone/iPad では「ホーム画面に追加」してから開き直し、このボタンを押すと通知を受け取れます。');
    }

    // 既に購読済みならボタンを「オフにする」に。
    try {
      var reg = await navigator.serviceWorker.ready;
      var sub = await reg.pushManager.getSubscription();
      setButtonLabel(!!sub);
      if (sub) setStatus('通知はオンです。もう一度押すとオフにできます。');
    } catch (e) {
      /* SW 未登録などは無視 */
    }
  });

  window.NewsGraspPush = { subscribe: doSubscribe, unsubscribe: doUnsubscribe };
})();
