# 发布与版本维护

## 当前状态

- 默认分支：`main`
- 当前版本：`0.17.0`
- 当前标签：`v0.17.0`
- 远程仓库：`https://github.com/CJwens/astrbot-plugin-prompt-audit.git`
- Windows GitHub 访问不稳定时，优先使用本机 Clash 的 HTTP/Mixed 端口；当前已验证端口为 `7897`

## 日常修改

1. 修改代码后先检查状态：

   ```bash
   git status
   ```

2. 运行与本次改动相关的测试和检查。
3. 更新 `metadata.yaml`、`CHANGELOG.md` 中的版本信息。
4. 提交并推送：

   ```bash
   git add .
   git commit -m "描述本次修改"
   git -c http.proxy=http://127.0.0.1:7897 \
     -c https.proxy=http://127.0.0.1:7897 \
     push origin main
   ```

## 发布新版本

版本号必须同时出现在 `metadata.yaml`、`CHANGELOG.md` 和 Git 标签中。

```bash
git tag -a vX.Y.Z -m "Prompt Audit X.Y.Z"
git -c http.proxy=http://127.0.0.1:7897 \
  -c https.proxy=http://127.0.0.1:7897 \
  push origin vX.Y.Z
```

正式发布时，生成带版本号的插件 ZIP 作为 GitHub Release 附件。ZIP 不提交到 Git 仓库；`.gitignore` 已排除 ZIP、数据库、缓存和 Python 临时文件。

## 发布前检查

- `metadata.yaml` 版本与目标标签一致。
- `CHANGELOG.md` 有对应版本记录。
- 不提交数据库、聊天记录、提示词原文、凭据、缓存文件或本地配置。
- 运行 Python 编译检查、相关测试和 `git diff --check`。
- 确认 `git status` 没有意外的大文件或敏感文件。
- 推送 `main` 后再推送对应的 `vX.Y.Z` 标签。

## 网络故障处理

如果直连 GitHub 失败，先确认 Clash 的 HTTP/Mixed 代理端口，再使用本次命令级代理。`9090` 是 Mihomo 控制器端口，不是 Git 代理端口。

```bash
git -c http.proxy=http://127.0.0.1:7897 \
  -c https.proxy=http://127.0.0.1:7897 \
  ls-remote origin
```

只有 `ls-remote` 成功后才继续推送。不要为了绕过网络问题关闭 TLS 证书校验。
