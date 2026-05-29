/* News Grasp — Web Push 購読クライアント
 *
 * 「通知を受け取る」ボタンから呼ばれ、
 *   通知許可 → PushManager.subscribe → 購読情報(JSON) を画面に表示する。
 *
 * News Grasp はサーバーを持たない静的サイトのため、購読情報を自動で
 * 送れない。生成された JSON を管理人に渡し、管理人がローカルの
 *   data/push_subscriptions.secret.json
 * に追記する運用（手動登録）。詳細は README「Web Push 通知」を参照。
 */
(function () {
  'use strict';

  // tools/gen_vapid_keys.py が出力した application server key（公開鍵 / base64url）。
  // 鍵を作り直したら必ずこの 1 行を差し替える（古い購読は全て無効化される）。
  var VAPID_PUBLIC_KEY =
    'BHY4OrsGBB4fh8yGdvMj5Kz_huxdh0kchjRoPnC15oZ6oe3QYWlrQgl3_HYUhO3OjDd3ATvJZgR-TXk16bPfZ0E';

  // base64url 文字列 → Uint8Array（applicationServerKey が要求する形式）
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

  function showSubscription(subJson) {
    var box = document.getElementById('push-sub-json');
    if (box) {
      box.value = JSON.stringify(subJson);
      box.hidden = false;
      box.focus();
      box.select();
    }
    setStatus('購読しました。下の文字列をコピーして管理人にお渡しください。');
  }

  async function subscribe() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      setStatus('この端末/ブラウザは Web Push に対応していません。');
      return;
    }
    try {
      var permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        setStatus('通知が許可されませんでした。ブラウザの設定から許可し直してください。');
        return;
      }
      var reg = await navigator.serviceWorker.ready;
      var existing = await reg.pushManager.getSubscription();
      var sub =
        existing ||
        (await reg.pushManager.subscribe({
          userVisibleOnly: true, // Web Push 仕様上 true 必須（必ず可視通知を出す）
          applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
        }));
      showSubscription(sub.toJSON());
    } catch (err) {
      console.error('[News Grasp push]', err);
      setStatus('購読に失敗しました: ' + (err && err.message ? err.message : err));
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('push-subscribe-btn');
    if (btn) btn.addEventListener('click', subscribe);

    // iOS Safari は「ホーム画面に追加」した standalone PWA でのみ Push 可能。
    // 通常タブで押しても失敗するので、先に注意書きを出す。
    var standalone =
      window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true;
    var isiOS = /iP(hone|ad|od)/.test(navigator.userAgent);
    if (isiOS && !standalone) {
      setStatus('iPhone/iPad では「ホーム画面に追加」してから開き直し、このボタンを押すと通知を受け取れます。');
    }
  });

  // 手動でコンソールからも叩けるよう公開
  window.NewsGraspPush = { subscribe: subscribe };
})();
