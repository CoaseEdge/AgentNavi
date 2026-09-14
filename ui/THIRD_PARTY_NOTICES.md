# UI third-party notices

AgentNavi 的 MCP App 构建使用以下依赖；它们不会给基础 Python 安装增加运行时依赖。

- `@modelcontextprotocol/ext-apps` 2.0.0 — MIT License
- `@modelcontextprotocol/client` 2.0.0 — MIT License（production bundle peer dependency）
- `@modelcontextprotocol/core` 2.0.0 — MIT License（production bundle peer dependency）
- `zod` 4.6.5 — MIT License（production bundle peer dependency）
- `pkce-challenge` 5.0.1 — MIT License（production bundle dependency）
- `@standard-schema/spec` 1.1.0 — MIT License（production bundle dependency）
- `vite` 8.3.0 — MIT License
- `vite-plugin-singlefile` 2.3.3 — MIT License
- `typescript` 7.0.2 — Apache License 2.0
- `linkedom` 0.18.13 — ISC License（仅测试）

production bundle 的完整许可证和版权声明随 wheel 保存在
`agentnavi/mcp/resources/THIRD_PARTY_NOTICES.txt`。版本与传递依赖以
`package-lock.json` 为准。

`@modelcontextprotocol/ext-apps` 没有独立的 jitless browser 构建。其依赖
Zod 4.6.5 的生成代码包含一个 `Function(\`\`)` CSP capability probe；本 App
在 bridge 初始化前显式设置 `z.config({ jitless: true })`，同时保持 HTML CSP
不含 `unsafe-eval`、`allowUnsafeEval: false`，并由安全测试锁定唯一已审计片段
的数量和 SHA-256。任何新增 `eval`、`new Function` 或额外 `Function` 调用都会
使构建检查失败。
