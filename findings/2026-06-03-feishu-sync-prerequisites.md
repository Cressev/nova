# 飞书同步前置条件

## 发现时间

2026-06-03

## 结论

旧结论已废弃。用户已确认使用 `lark-cli`，且当前环境中 `lark-cli` 已安装并登录，user 和 bot 身份均 ready。飞书云盘目录创建、飞书新版文档（docx）创建和 bot 私信通知已经完成。

## 已验证状态

- `lark-cli auth status` 显示 bot ready。
- `lark-cli auth status` 显示 user ready。
- user open_id：`ou_640bf82ee799f0889dcd373c3ebcb5c2`。
- 已创建飞书云盘根目录：`Nova`。
- 已创建飞书云盘目录：`Nova/产品研发文档集/预研文档集`、`Nova/产品研发文档集/v1.0`、`Nova/产品研发文档集/v1.1`。
- 已将已有正文的本地 Markdown 草稿创建为飞书新版文档（docx）。
- 已删除此前误上传的 6 个 Drive Markdown 普通文件。
- 已通过 bot 私信通知用户初次同步，消息 ID：`om_x100b6ecad9a4a4a4b48a1ffb9fc887b`。
- 已通过 bot 私信通知用户 docx 更正，消息 ID：`om_x100b6ecae7b3b0bcb3c567bb7616f96`。

## 当前飞书位置

- `Nova`：`https://jcnu7fvwv6c8.feishu.cn/drive/folder/CfHOfw192lym5zdllA6ctINMnPg`
- `产品研发文档集`：`https://jcnu7fvwv6c8.feishu.cn/drive/folder/HZznf3LjmlZ2iYdgPr8cWe4Hncf`
- `预研文档集`：`https://jcnu7fvwv6c8.feishu.cn/drive/folder/NpWnfAzjOlS3wddLnLTcauRynRe`
- `v1.0`：`https://jcnu7fvwv6c8.feishu.cn/drive/folder/Nl8ZfmFOOlfNpLdyKtWcr0UYncc`
- `v1.1`：`https://jcnu7fvwv6c8.feishu.cn/drive/folder/KgsffygDSl3ijudmsnscShKxnhf`

## 飞书新版文档

- 产品研发文档集 README：`https://jcnu7fvwv6c8.feishu.cn/docx/XkUVdBFQzojDbWxbXRTcrYcMn6f`
- 预研文档集 README：`https://jcnu7fvwv6c8.feishu.cn/docx/X7sudxRfdop3pzxzon4c0OilnLf`
- v1.0 README：`https://jcnu7fvwv6c8.feishu.cn/docx/E6jndpHE6oKiCZxcLJoc6uqTnTh`
- v1.1 README：`https://jcnu7fvwv6c8.feishu.cn/docx/JULQdy0m7oendJxhIj3cZI93nxh`
- 20260603_需求文档：`https://jcnu7fvwv6c8.feishu.cn/docx/YybZd1eunoIzrZxDOLEcCx8Rnhb`
- 20260603_v1.0技术文档：`https://jcnu7fvwv6c8.feishu.cn/docx/CHVSdG6JKolNKRxJGSvceGewn2d`

## 后续执行方式

后续产研文档同步优先使用：

1. `lark-cli drive +create-folder --as user` 创建目录。
2. `lark-cli docs +create --api-version v2 --as user` 创建飞书新版文档。
3. `lark-cli docs +update --api-version v2 --as user` 更新已有飞书新版文档。
4. `lark-cli im +messages-send --as bot --user-id <open_id>` 发送完成通知。

注意：`drive +search` 当前缺少 `search:docs:read` scope；如果需要先查重再创建目录，应先按 `lark-cli` 提示为 user 补充该 scope。
