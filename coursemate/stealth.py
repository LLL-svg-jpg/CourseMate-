"""浏览器自动化特征抹除。

Playwright 启动的 Chromium 会暴露若干可被 JS 探测的痕迹，最典型的是
navigator.webdriver === true。课程平台的风控普遍会读这些字段。

这里手写精简版而非引入 playwright-stealth 包：依赖更少、可审计、
且只覆盖真正需要的字段——过度伪装本身也是一种可被识别的特征。
"""

STEALTH_JS = r"""
(() => {
  // 1. navigator.webdriver：最直接的自动化标志
  Object.defineProperty(Navigator.prototype, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });

  // 2. 真实浏览器至少有若干插件，无头/自动化环境常常为空
  if (navigator.plugins.length === 0) {
    Object.defineProperty(Navigator.prototype, 'plugins', {
      get: () => [1, 2, 3, 4, 5].map(i => ({ name: `Plugin ${i}` })),
      configurable: true,
    });
  }

  // 3. 语言列表与中文课程平台预期保持一致
  Object.defineProperty(Navigator.prototype, 'languages', {
    get: () => ['zh-CN', 'zh', 'en'],
    configurable: true,
  });

  // 4. Chrome 运行时对象：非 Chrome 内核伪装时缺失
  if (!window.chrome) {
    window.chrome = { runtime: {} };
  }

  // 5. 权限查询：自动化环境下 notifications 会返回异常状态
  const origQuery = window.navigator.permissions?.query;
  if (origQuery) {
    window.navigator.permissions.query = (params) =>
      params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(params);
  }

  // 6. WebGL 厂商信息：SwiftShader 是软件渲染的明显特征
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';          // UNMASKED_VENDOR_WEBGL
    if (p === 37446) return 'Intel Iris OpenGL';   // UNMASKED_RENDERER_WEBGL
    return getParameter.call(this, p);
  };
})();
"""

# 页面可见性伪装：很多平台在 tab 失焦/隐藏时暂停计时。
# 注意这只对抗"窗口被遮挡"的误判，不改变真实播放行为。
VISIBILITY_JS = r"""
(() => {
  Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });
  Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true });
  document.addEventListener('visibilitychange', (e) => e.stopImmediatePropagation(), true);
  window.addEventListener('blur', (e) => e.stopImmediatePropagation(), true);
})();
"""
