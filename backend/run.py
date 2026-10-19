"""启动脚本。

Windows 下 MCP stdio 需要 ProactorEventLoop；Uvicorn 的 reload supervisor 会
切换为 SelectorEventLoop，后者不能创建 asyncio 子进程。因此 Windows 禁用热重载。
"""

import os
import uvicorn
from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    
    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=os.name != "nt",
        log_level=settings.log_level.lower()
    )
