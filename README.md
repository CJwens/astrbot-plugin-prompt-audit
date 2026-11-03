# LLM 缓存与请求指纹诊断插件

该插件结合两类信号排查缓存：

- 在 `on_llm_request` 阶段记录请求指纹，并包装已注册处理器，定位哪个插件处理器改动了 system prompt、上下文、工具等字段。
- 运行时包装已注册 Provider 的 `text_chat` / `text_chat_stream`，分别记录每次调用最终响应里的缓存输入 token、未缓存输入 token、输出 token 和服务商报告比例，覆盖工具循环里的多次模型调用。
- 在 AstrBot WebUI 插件详情页提供「缓存诊断面板」，显示统计卡片与诊断时间线；数据存入插件自己的 SQLite 数据库，最多保留 30 天或 10,000 条事件（先达到的限制生效）。
- 诊断时间线按页读取，默认每页 50 条，避免事件积累后一次性撑爆页面。
- 面板支持按当前时间范围导出 CSV；导出只包含哈希、请求元数据、token 用量和插件字段名，不包含提示词、聊天正文或工具参数。
- 面板增加按群汇总、群成员和私聊用户统计；稳定统计维度仍使用平台、群和用户哈希，同时展示最近一次观察到的群名和成员名，不保存原始 ID。
- 统计区域采用“群聊 / 私聊”双页签；群聊先显示群汇总，选择群后查看成员，并可打开群详情查看模型与成员拆分；私聊用户单独分页。
- 诊断时间线支持逐行展开二级详情；详情只展示指纹、长度、token、关联元数据、名称来源和插件/处理器名称，不展示提示词、工具定义或聊天正文。
- 模型统计额外显示“有缓存调用、报告 0 调用、同次有改动、未关联”四类排查信号，避免把报告值直接解释成最终缓存结论。
- 统计面板每 30 秒自动刷新；群列表最多展示前 100 个群，明细按页加载。

日志不记录提示词、聊天内容或工具参数原文。逐插件请求钩子诊断仍使用 AstrBot 内部处理器注册表，不是稳定的公共扩展 API；Provider 观察通过插件运行时包装完成，不需要修改或单独部署 AstrBot 核心。

## 安装与使用

将插件文件夹放入 `data/plugins/`、启用插件并重启 AstrBot，无需修改或自行构建 AstrBot 核心。打开插件详情页中的「缓存诊断面板」查看 usage 和变更时间线；也可在日志中查 `LLM request hook mutation`、`LLM request fingerprint` 和 `LLM cache usage`。确认插件版本为 `0.14.1`，启动日志应显示诊断已启用。

## 如何解读

- `LLM request hook mutation ... plugin=... handler=... changed=system`：精确指出刚执行并改变 system prompt 的插件处理器；`before`、`after` 只含指纹与数量。
- 没出现 `LLM request hook mutation`：这轮钩子没有修改被观察的请求字段；不能排除钩子以外的核心构建、Provider 适配或中转商侧变化。
- `previous_turn_changed=system,extra`：相较同一会话上一条被观察到的请求，这些字段变化；首次观测显示 `first_seen`。
- `tools`、`contexts`、`prompt`、`extra` 同理。哈希变化证明内容不同，不代表必然缓存未命中。
- `request_id` 可用于与同一请求的 AstrBot trace 日志对齐。
- `status=cached_tokens_reported`：响应 usage 报告了大于零的缓存输入 token；`cache_ratio` 是该 Provider 调用内 `input_cached / (input_cached + input_other)`。
- `同次请求改动：system,tools,extra`：这次 Provider 调用与同一条请求钩子链关联时，观察到这些字段在钩子阶段发生过改变；它是定位线索，不等于该调用一定没有命中缓存。
- `association_status=associated`：Provider usage 已绑定到短期请求关联记录；`association_source=session_or_conversation_registry` 表示通过会话或对话哈希关联，适用于 Agent 工具循环创建的异步任务。
- `association_status=unassociated`：没有足够的安全元数据建立关联，不应把附近的插件改动归因给本次调用。
- 关联记录最多保留 180 秒，仅保存哈希、名称、字段名、插件/处理器名称和 token 数字；关联年龄越大，越应谨慎解释。
- `status=zero_cached_tokens_reported`：响应 usage 存在且报告输入 token，但缓存 token 为零；这只是响应报告值，不证明服务端缓存绝对未命中。
- `status=usage_unavailable`：响应未提供 usage；不能据此判断是否命中缓存。`no_input_tokens_reported` 表示 usage 对象存在但没有报告有效输入 token。
- `status=final_response_missing`：流式调用结束或中断时没有观察到最终非分块响应；分块 usage 不用于完成调用的统计。
- `status=provider_call_failed`：Provider 调用抛出异常；错误类型会记录，但异常内容不会保存。
- 面板按服务商和模型分别统计；不汇总不同模型的比例，避免混合不同计费或 usage 口径。以中转服务商账单确认实际计费。
- 按群/个人统计同样按服务商和模型拆分。`群汇总` 与 `群成员` 是两个独立视角，不能把两者相加；同一条群聊调用会同时出现在一个群汇总和一个成员汇总中。
- `群汇总` 表示整个群的 token usage，`群成员` 表示触发该请求的发送者，`私聊用户` 表示私聊发送者。无法从事件安全取得身份时计入未归属记录，不会猜测归属。
- 名称优先读取当前消息事件；群消息缺少群名或成员昵称时，插件会按群调用 AstrBot 的 `get_group()` 做一次限时缓存的补全，并在详情/CSV 中记录名称来源。
- `scope=provider_call`：每条记录对应一次由 AstrBot 调用 Provider 的 `text_chat` 或 `text_chat_stream`；`invocation_id` 用于区分具体调用。

## 能力边界

逐插件包装仅追踪启动时已注册的请求处理器；需重启 AstrBot 安装/更新后生效，插件停用或卸载时会恢复原始处理器。usage 观察通过插件运行时包装 `Provider` 实例实现：启动时扫描现有实例，并在每次 LLM 请求开始前再次扫描，以覆盖常规配置重载后新增的实例。它依赖 AstrBot 内部的 `provider_insts`、`text_chat` 和 `text_chat_stream` 接口，不是稳定的公共扩展 API；如果 AstrBot 更新改变这些接口，应先检查兼容性。请求钩子之前发生、且绕过已包装实例的调用不在保证范围内。同一会话的上轮指纹只保存在插件内存中，最多保留 512 个会话；重启或重载后从 `first_seen` 重新开始。

缓存诊断依据是每次 Provider 调用返回给 AstrBot 的 usage 字段，并非对缓存机制本身的直接探测。此观测覆盖 AstrBot 进程内经过 Provider 实例 `text_chat` / `text_chat_stream` 的调用，包括 Agent 工具循环与内部辅助请求；不保证区分 Provider 方法内部的网络重试，也不能覆盖绕过这些实例的外部请求。Provider 或中转商可能不返回、转换或以不同口径统计缓存 token；记录不等同于真实计费。若响应 usage 未报告缓存 token，插件只能显示“未报告”，不能推断服务端绝对没有命中。最终以中转服务商账单为准。

诊断面板接口由 AstrBot 插件页面 bridge 转发，并沿用 WebUI 身份认证。事件数据库保存时间、请求 ID、会话哈希、路由维度哈希、最近一次观察到的展示名称、指纹哈希/长度、插件处理器名称和 usage 数字；不保存对话、提示词、完整工具定义、原始群号、原始用户号或密钥。展示名称可能随平台变化；哈希只用于同一插件实例内的相对统计，不能从导出文件还原原始身份。
