from __future__ import annotations

import sys

from ..api import routes as _routes

# 保持 `nova.app.main` 作为 Web 启动入口，同时让测试和旧调用拿到
# `nova.api.routes` 的同一个模块对象，避免全局运行态被复制成两份。
sys.modules[__name__] = _routes
