/* 翻页时钟 · Service Worker
 *
 * 作用：让「添加到主屏幕」后的时钟在**离线**时也能打开（飞机上、无 Wi-Fi 的桌面时钟）。
 * 策略：预缓存核心资源 + 运行时缓存回退；只处理同源 GET。
 *      本机宿主的控制接口（/__state、/__config 等）一律不拦截、不缓存。
 *
 * 注意：改了 index.html 等资源后，把下面的 CACHE_VERSION 加一，用户下次打开即自动更新。
 */

var CACHE_VERSION = 'flipclock-v1';

var PRECACHE = [
  './',
  './index.html',
  './manifest.webmanifest',
  './apple-touch-icon.png',
  './icon-192.png',
  './icon-512.png',
  './icon-maskable-512.png'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE_VERSION).then(function (cache) {
      // 单个资源失败不应让整个安装失败
      return Promise.all(PRECACHE.map(function (url) {
        return cache.add(new Request(url, { cache: 'reload' })).catch(function () {});
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        return k === CACHE_VERSION ? null : caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  var req = event.request;

  // 只处理同源 GET
  if (req.method !== 'GET') return;
  var url;
  try { url = new URL(req.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return;

  // 本机宿主的控制接口与配置：绝不缓存（否则会拿到过期的运行状态）
  if (url.pathname.indexOf('__') === 0 || url.pathname.indexOf('/__') >= 0) return;

  // 导航请求：网络优先，失败回退缓存（保证在线时总能拿到最新页面）
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE_VERSION).then(function (c) { c.put('./index.html', copy); });
        return res;
      }).catch(function () {
        return caches.match('./index.html').then(function (hit) {
          return hit || caches.match('./');
        });
      })
    );
    return;
  }

  // 其他静态资源：缓存优先，未命中再回源并写入缓存
  event.respondWith(
    caches.match(req).then(function (hit) {
      if (hit) return hit;
      return fetch(req).then(function (res) {
        if (res && res.status === 200 && res.type === 'basic') {
          var copy = res.clone();
          caches.open(CACHE_VERSION).then(function (c) { c.put(req, copy); });
        }
        return res;
      }).catch(function () { return hit; });
    })
  );
});
