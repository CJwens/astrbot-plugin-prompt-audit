# LLM 缓存与请求指纹诊断插件

该插件结合两类信号排查缓存：

- 在 `on_llm_request` 阶段记录请求指纹，并包装已注册处理器；逐个记录实际执行的插件/处理器，包括未改动请求字段的处理器。
- 每次请求另附 `on_llm_request` 注册清单：包含插件停用、处理器禁用及当前事件过滤掉的处理器；将“已注册但本次未执行”与“执行但未改动”分开显示。
- 对 system prompt 和 prompt 标记开头插入、末尾追加、首尾包裹、删减或重写；对上下文、工具、额外内容、图片和音频标记列表前插、后加、移除或重排。详情只含变化类型、数量/字符长度和指纹，不保存提示词或列表项目原文。
- 每次 Provider 调用记录进入 Provider 方法时的请求字段指纹、长度/数量，以及配置/调用级 HTTP header 名称和非敏感值指纹；认证类 header 值只标记为已隐藏，不记录或哈希。
- 运行时包装已注册 Provider 的 `text_chat` / `text_chat_stream`，分别记录每次调用最终响应里的缓存输入 token、未缓存输入 token、输出 token 和服务商报告比例，覆盖工具循环里的多次模型调用。
- 在 AstrBot WebUI 插件详情页提供「缓存诊断面板」，显示统计卡片与诊断时间线；数据存入插件自己的 SQLite 数据库，最多保留 30 天或 10,000 条事件（先达到的限制生效）。
- 诊断时间线按页读取，默认每页 50 条，避免事件积累后一次性撑爆页面。
- 面板支持按当前时间范围导出 CSV；导出只包含哈希、请求元数据、token 用量和插件字段名，不包含提示词、聊天正文或工具参数。
- 面板增加按群汇总、群成员和私聊用户统计；稳定统计维度仍使用平台、群和用户哈希，同时展示最近一次观察到的群名和成员名，不保存原始 ID。
- 统计区域采用“群聊 / 私聊”双页签；群聊先显示群汇总，选择群后查看成员，并可打开群详情查看模型与成员拆分；私聊用户单独分页。
- 诊断时间线支持逐行展开二级详情；请求处理器执行链展示插件/处理器调用顺序、是否改动字段、字段变化形态及长度/数量，并与 Provider usage 关联。详情只展示指纹、长度、token、关联元数据、名称来源和插件/处理器名称，不展示提示词、工具定义或聊天正文。
- 模型统计额外显示“有缓存调用、报告 0 调用、同次有改动、未关联”四类排查信号，避免把报告值直接解释成最终缓存结论。
- 统计面板每 30 秒自动刷新；群列表最多展示前 100 个群，明细按页加载。

日志不记录提示词、聊天内容或工具参数原文。逐插件请求钩子诊断仍使用 AstrBot 内部处理器注册表，不是稳定的公共扩展 API；Provider 观察通过插件运行时包装完成，不需要修改或单独部署 AstrBot 核心。

## 安装与使用

将插件文件夹放入 `data/plugins/`、启用插件并重启 AstrBot，无需修改或自行构建 AstrBot 核心。打开插件详情页中的「缓存诊断面板」查看 usage 和变更时间线；请求行展开后查看注册钩子清单，Provider usage 行展开后查看进入 Provider 的参数指纹。也可在日志中查 `LLM request hook mutation`、`LLM request fingerprint` 和 `LLM cache usage`。确认插件版本为 `0.15.0`，启动日志应显示诊断已启用。

## 如何解读

- `LLM request hook mutation ... plugin=... handler=... changed=system`：精确指出刚执行并改变 system prompt 的插件处理器；`before`、`after` 只含指纹与数量。
- `delta.system.kind=text_prepended`：处理器在 system prompt 前端插入内容；`text_appended` 表示在末尾追加。日志同时包含字符变化数和前后指纹，不包含新增文本。
- 请求详情中的“处理器执行链”同时包含无变化处理器，可区分“处理器执行但没改字段”与“本次请求没有观察到该处理器”。
- 请求详情中的“已注册钩子”清单会显示当前停用/禁用/过滤状态；符合调用条件但没有执行记录时，只能判断为“本次未观察到执行”，并结合前序处理器是否停止传播排查，不能据此认定插件修改了提示词。
- Provider usage 详情中的请求参数指纹是在 AstrBot 调用 Provider 方法入口采集的；system、prompt、上下文、工具、媒体、工具结果、调用选项均只记录指纹及长度/数量。header 仅记录名称和非敏感值指纹，认证类值不记录。
- `contexts/tools/extra/images/audios` 的列表变化只记录新增/移除数量和结构变化方向，不记录列表条目、工具定义或媒体地址。
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

逐插件包装追踪 AstrBot 注册的 `on_llm_request` 处理器，并在每次请求时重新扫描，以覆盖常规插件重载；注册清单包含停用/禁用/过滤状态，执行链仅记录实际进入的处理器。它不覆盖其他事件类型中对 Provider/请求的间接改动、钩子之前在核心请求构建阶段发生的变化、绕过该事件的插件路径，或 Provider 方法内部序列化之后的最终 HTTP body/wire headers。Provider 参数和 header 指纹是在调用 Provider 方法入口采集的，不保证等同于 Provider 最终发送的数据；不同 Provider 适配器可能自行转换字段或额外注入 header。变化方向、字段哈希和 header 指纹只是定位证据，不等同于服务商最终 token 前缀或缓存键。认证类 header 值不会记录或哈希。该功能需重启 AstrBot 安装/更新后生效，插件停用或卸载时会恢复原始处理器。usage 观察依赖 AstrBot 内部的 `provider_insts`、`text_chat` 和 `text_chat_stream` 接口，不是稳定的公共扩展 API；如果 AstrBot 更新改变这些接口，应先检查兼容性。同一会话的上轮指纹只保存在插件内存中，最多保留 512 个会话；重启或重载后从 `first_seen` 重新开始。

缓存诊断依据是每次 Provider 调用返回给 AstrBot 的 usage 字段，并非对缓存机制本身的直接探测。此观测覆盖 AstrBot 进程内经过 Provider 实例 `text_chat` / `text_chat_stream` 的调用，包括 Agent 工具循环与内部辅助请求；不保证区分 Provider 方法内部的网络重试，也不能覆盖绕过这些实例的外部请求。Provider 或中转商可能不返回、转换或以不同口径统计缓存 token；记录不等同于真实计费。若响应 usage 未报告缓存 token，插件只能显示“未报告”，不能推断服务端绝对没有命中。最终以中转服务商账单为准。

诊断面板接口由 AstrBot 插件页面 bridge 转发，并沿用 WebUI 身份认证。事件数据库保存时间、请求 ID、会话哈希、路由维度哈希、最近一次观察到的展示名称、指纹哈希/长度、插件处理器名称和 usage 数字；不保存对话、提示词、完整工具定义、原始群号、原始用户号或密钥。展示名称可能随平台变化；哈希只用于同一插件实例内的相对统计，不能从导出文件还原原始身份。
