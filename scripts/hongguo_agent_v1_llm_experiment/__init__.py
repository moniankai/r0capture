"""红果短剧下载 Agent 包。

结构:
  service.py  - HongguoService 单例,持有 Frida session / ADB / Capture 状态
  tools.py    - tool schema + 对 service 方法的 JSON 化包装(供 Claude tool_use)
  vision.py   - Claude Vision 截图校验(剧名/集数)
  prompts.py  - system prompt / few-shot
  agent.py    - anthropic SDK tool_use 主循环
"""
