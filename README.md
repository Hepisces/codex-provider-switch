# Codex Provider Switch

用于 macOS 的 Codex 本地对话 provider 双向切换工具。支持官方 `openai` 与已配置的第三方 provider 之间反复切换，可处理全部对话、指定对话，或只修改新对话的默认 provider。

切换前先预览，确认后才写入。工具不提供删除对话功能；缺失文件、切换失败和撤销冲突均不会触发删除对话或清理归档。写入前保留完整备份。

## 为什么需要这个工具

Codex 本地配置的默认 provider 与历史对话保存的 provider 是两处状态。更改默认配置后，历史对话的会话文件和 SQLite 索引仍可能保留创建时的 provider。

本工具同步调整三个位置：

| 位置 | 修改内容 |
| --- | --- |
| `$CODEX_HOME/config.toml` | 顶层 `model_provider`；可选修改默认 `model` |
| 所选对话 JSONL 的首条 `session_meta` | `payload.model_provider` |
| 本地 `state_*.sqlite` 的 `threads` 表 | 所选记录的 `model_provider` |

对话 ID、标题和首条记录以后的消息正文保留。首条元数据记录会重新序列化，其他字段值保留。工具不修改登录令牌、第三方密钥或 `auth.json`。

这是依据 Codex 本地存储结构实现的迁移工具。已在模拟数据上验证文件与索引修改；真实客户端恢复对话后的网络路由和订阅扣量需要使用者切换后确认。

## 环境与准备

- macOS；双击入口使用系统自带的 zsh。
- Python 3.11 或更新版本，仅使用标准库。启动器自动寻找当前用户已有的 Codex Python 运行时、PATH 中的 Python 和常见安装位置。
- 本地已有 Codex 配置和唯一的 `state_*.sqlite`。实现依据 codex-cli 0.157.1 的存储结构，遇到其他结构时可能停止。
- 第三方 provider 已在用户配置的 `[model_providers.<id>]` 中定义。
- 切换到官方订阅用量前，Codex 已使用 ChatGPT 账号登录。切换 provider 本身不执行登录。

`openai` 是内置官方 provider ID。`vapi`、`custom` 只是第三方 ID 示例，请使用自己配置中的实际 ID；菜单会显示可选 ID。

所有命令默认处理环境变量 `CODEX_HOME` 指定的目录，未设置时使用 `~/.codex`。也可以通过放在子命令前的 `--home` 显式指定目录。

## 获取与启动

```sh
git clone https://github.com/Hepisces/codex-provider-switch.git
cd codex-provider-switch
./Codex-Provider-Switch.command status
```

也可以下载仓库 ZIP、解压，再双击 `Codex-Provider-Switch.command`。启动器和 Python 源码必须放在同一个文件夹内。如果下载后启动器没有执行权限，在该文件夹打开终端运行：

```sh
chmod +x Codex-Provider-Switch.command
```

状态检查会显示 Codex 目录、默认 provider、默认模型、已配置的 provider ID，以及索引中各 provider 的对话数量，不输出密钥或消息正文。

## 完整流程：第三方切换到官方

1. 先在 Finder 中打开工具文件夹，保留这个窗口。
2. 完全退出 Codex 桌面版、Codex CLI，以及 VS Code 中运行的 Codex。工具会检查相关进程，但不会自动关闭应用。预览和 `status` 可以在 Codex 运行时使用，实际写入及撤销要求退出。
3. 双击 `Codex-Provider-Switch.command`，在主菜单输入 `1` 并回车，选择官方 `openai`。
4. 选择范围：输入 `1` 处理全部索引对话，输入 `2` 后填写完整对话 ID，或输入 `3` 只更改默认配置。指定对话也会同时更新默认 provider。
5. 回答是否跳过会话文件缺失的记录。只有输入 `y` 才会跳过；其他输入保持严格检查。跳过时对应的索引记录仍然保留，provider 保持原值。
6. 查看预览中的目标 provider、匹配对话数、待修改索引数和文件数。匹配数包含可能被跳过的记录；缺失记录会单独列出。
7. 输入大写 `SWITCH` 执行。未输入此字符串不会写入。程序会再次检查 Codex 是否退出，然后准备备份、更新文件与索引。
8. 保存完成提示中的备份目录，重新打开 Codex，进入原有对话继续使用。
9. 检查对应对话能否正常回复，以及官方用量页面是否体现预期使用。若模型不可用，在 Codex 中选择该 provider 支持的模型。

常见交互顺序：

```text
选择：1 官方 / 2 第三方 / 3 撤销 / Enter 退出：1
范围：1 全部对话 / 2 指定对话 / 3 仅默认配置：1
是否跳过会话文件缺失的记录？……：y
目标 provider: openai；匹配对话: …；索引修改: …；文件修改: …
输入 SWITCH 执行（会先检查 Codex 是否已退出）：SWITCH
```

选择“仅默认配置”只影响默认 provider，不迁移旧对话。需要解决旧对话仍保存第三方 provider 的问题时，应选择全部或指定对话。

## 完整流程：官方切回第三方

1. 再次完全退出所有 Codex 客户端，打开启动器。
2. 主菜单输入 `2`。
3. 输入配置中已经存在的第三方 provider ID，例如 `vapi`；输入的是 ID，不是显示名称、服务地址或 API key。
4. 选择范围，确认缺失文件处理方式，检查预览。
5. 输入 `SWITCH` 执行，完成后重新打开 Codex。

这两个方向可以反复执行。切回第三方直接设置目标 provider，不依赖上次备份。不同 provider 支持的模型可能不同，工具保留原有对话模型选择；命令行 `--model` 只修改默认模型。

“全部对话”会把本地索引中所有可处理对话统一切换到目标 provider，包含归档。如果需要保留不同对话各自的 provider，使用指定对话模式，或在命令行通过 `--from` 筛选来源。

## 命令行用法

以下命令在工具文件夹中执行。`switch` 默认只预览，添加 `--apply` 才会写入。

```sh
# 查看默认配置和索引统计
./Codex-Provider-Switch.command status

# 预览：仅迁移当前索引中 provider 为 vapi 的对话
./Codex-Provider-Switch.command switch openai --all --from vapi

# 如果已确认缺失记录可以跳过，预览后再执行
./Codex-Provider-Switch.command switch openai --all --from vapi --skip-missing
./Codex-Provider-Switch.command switch openai --all --from vapi --skip-missing --apply

# 反向切换全部可处理对话到 vapi
./Codex-Provider-Switch.command switch vapi --all --skip-missing --apply

# 仅修改默认 provider，旧对话不迁移
./Codex-Provider-Switch.command switch openai --apply

# 指定一个或多个对话；将占位内容换成真实完整 ID
./Codex-Provider-Switch.command switch openai --thread THREAD_ID_1 --thread THREAD_ID_2 --apply

# 指定另一个 Codex 目录；--home 放在子命令之前
./Codex-Provider-Switch.command --home /path/to/codex-home status
```

已有 Python 3.11+ 时，也可以直接运行源码：

```sh
python3 codex_provider_switch.py status
python3 codex_provider_switch.py switch openai --all
python3 codex_provider_switch.py --help
python3 codex_provider_switch.py switch --help
```

## 备份、失败处理与撤销

每次有实际变更的写入操作都会创建独立备份目录：

```text
$CODEX_HOME/provider-switch-backups/日期时间-随机值/
├── state.snapshot.sqlite   # SQLite backup API 生成的完整数据库快照
├── manifest.json           # 路径、原 provider、文件哈希及操作状态
├── 0.original              # 某个待修改文件的完整原内容
├── 0.new                   # 对应的准备写入内容
└── …
```

备份目录按当前用户访问权限保存，不会自动清理。配置和会话备份可能含密钥、私人对话与路径，请保留在本机，勿提交仓库或上传公开问题报告。仓库 `.gitignore` 已排除常见配置、对话和备份文件。

执行流程为：检查输入与会话结构 → 检查 Codex 进程 → 生成数据库快照和完整文件备份 → 写入恢复清单 → 在 SQLite 事务中更新索引 → 原子替换文件并校验 → 提交事务。检测到切换写入异常时，会回滚 SQLite 事务，并恢复本次已经替换的文件。恢复材料始终留在备份目录。

正常完成后如需撤销，在菜单选择 `3`，粘贴某次操作的备份目录；也可以运行：

```sh
./Codex-Provider-Switch.command restore ~/.codex/provider-switch-backups/实际备份目录名
```

自动撤销只恢复清单中的文件与对应索引字段，不整库覆盖。若对话或配置在切换后已发生变化，撤销会停止，保留新内容和备份；此时切回另一 provider 可使用正常双向切换流程。如果确实需要合并旧备份与新内容，应先检查两份文件后单独处理。

进程被强制终止或磁盘故障时，自动回滚可能无法完成。重新启动客户端前，保留操作输出和整个备份目录，先检查清单与原文件；不要直接用旧数据库快照覆盖已有新记录的数据库。

## 常见提示

| 提示或现象 | 含义与处理 |
| --- | --- |
| 仍检测到 Codex 进程 | 退出桌面版、CLI 和编辑器扩展中的 Codex，再运行写入操作 |
| 会话文件缺失 | 索引指向的文件当前不存在；默认停止，也可显式跳过，索引记录保留 |
| 未知 provider | 输入的 ID 未在 `[model_providers.<id>]` 中配置 |
| 启用了 profile | 当前工具拒绝自动处理，需先核对 profile 后再操作 |
| 官方地址覆盖项 | 检测到 `openai_base_url` 或 `chatgpt_base_url`，先确认目标地址 |
| 多个或没有 `state_*.sqlite` | 当前存储结构不满足工具要求，停止操作，不要删除数据库来绕过检查 |
| 会话 ID 不匹配、首记录未识别或路径越界 | 数据与工具预期不一致，先检查文件与索引 |
| 撤销提示文件已变化 | 后续修改受到保护；保留备份，可通过正常切换流程改变 provider |
| 同时使用 `--all` 与 `--thread` | 两种范围互斥，请选择一种 |

“全部对话”的处理范围是本地 SQLite 索引中的记录，不扫描修改未被索引的孤立 JSONL 文件。工具不会修复缺失对话文件，也不会下载历史记录。

## 测试

```sh
python3 -m unittest -v test_switcher
zsh -n Codex-Provider-Switch.command
```

8 项测试全部使用临时目录中的模拟数据，不写入真实 `~/.codex`：

- 预览不修改文件或索引。
- 切换和撤销恢复原始文件，保留数据库快照。
- 双向切换保留正文、对话 ID 和标题。
- 撤销拒绝覆盖切换后的新内容。
- 缺失文件默认停止，显式跳过时保留索引。
- 正向写入失败回滚。
- 反向写入失败回滚并保留对话。
- 检测到运行中的 Codex 时拒绝写入。

## 官方文档

[OpenAI 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)说明 `model_provider` 默认使用 `openai`；[Codex App Server 文档](https://learn.chatgpt.com/docs/app-server)介绍对话的 `modelProvider` 与恢复接口。本项目的直接文件迁移方案需结合具体客户端版本使用。
