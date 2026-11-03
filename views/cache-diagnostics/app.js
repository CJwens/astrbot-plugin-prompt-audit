const bridge = window.AstrBotPluginView;
const byId = (id) => document.getElementById(id);

const numberFormat = new Intl.NumberFormat("zh-CN");
const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});
const pageSize = 50;
const scopePageSize = 20;
let currentPage = 1;
let currentScopePage = 1;
let scopeMode = "groups";
let selectedGroupHash = "";
let currentGroupDetail = null;
let loadInProgress = false;
let reloadRequested = false;

function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : dateFormat.format(date);
}

function formatTokens(value) {
  return numberFormat.format(Number(value) || 0);
}

function exportTimestamp() {
  const date = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  const offsetMinutes = -date.getTimezoneOffset();
  const offsetSign = offsetMinutes >= 0 ? "+" : "-";
  const absoluteOffset = Math.abs(offsetMinutes);
  const offset = `${offsetSign}${pad(Math.floor(absoluteOffset / 60))}${pad(
    absoluteOffset % 60
  )}`;
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate()
  )}T${pad(date.getHours())}-${pad(date.getMinutes())}-${pad(
    date.getSeconds()
  )}${offset}`;
}

function csvCell(value) {
  const hasNestedValues =
    value &&
    typeof value === "object" &&
    (Array.isArray(value)
      ? value.some((item) => item && typeof item === "object")
      : true);
  const text = hasNestedValues
    ? JSON.stringify(value)
    : Array.isArray(value)
      ? value.join(";")
      : String(value ?? "");
  return `"${text.replaceAll('"', '""')}"`;
}

function textCell(value, className = "") {
  const cell = document.createElement("td");
  cell.className = className;
  cell.textContent = value;
  return cell;
}

function displayIdentity(name, hash, prefix = "") {
  const cleanName = String(name || "").trim();
  const cleanHash = String(hash || "").trim();
  if (cleanName && cleanHash) return `${prefix}${cleanName} · ${cleanHash}`;
  if (cleanName) return `${prefix}${cleanName}`;
  return cleanHash ? `${prefix}${cleanHash}` : "—";
}

function compactJson(value) {
  if (value == null || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function detailItem(label, value) {
  const item = document.createElement("div");
  item.className = "detail-item";
  const key = document.createElement("span");
  key.className = "detail-key";
  key.textContent = label;
  const content = document.createElement("strong");
  content.className = "detail-value";
  content.textContent = compactJson(value);
  item.append(key, content);
  return item;
}

function mutationDeltaSummary(delta = {}) {
  const labels = {
    text_initialized: "初始化文本",
    text_appended: "末尾追加",
    text_prepended: "开头插入",
    text_wrapped: "首尾包裹",
    text_trimmed: "删减文本",
    text_suffix_removed: "删除末尾",
    text_prefix_removed: "删除开头",
    text_extension_position_ambiguous: "扩展位置不确定",
    text_rewritten: "重写文本",
    items_initialized: "初始化列表",
    items_cleared: "清空列表",
    items_appended: "列表末尾新增",
    items_prepended: "列表开头插入",
    items_inserted_around_existing: "列表中间插入",
    items_removed: "移除列表项",
    items_reordered: "列表重排",
    items_rewritten: "重写列表",
    value_changed: "值变化",
  };
  return Object.entries(delta).map(([field, item]) => {
    const shape = labels[item.kind] || item.kind || "内容变化";
    const counts = [];
    if (item.added_prefix_chars) counts.push(`前置 +${item.added_prefix_chars} 字符`);
    if (item.added_suffix_chars) counts.push(`后置 +${item.added_suffix_chars} 字符`);
    if (item.removed_prefix_chars) counts.push(`前置 -${item.removed_prefix_chars} 字符`);
    if (item.removed_suffix_chars) counts.push(`后置 -${item.removed_suffix_chars} 字符`);
    if (item.added_items) counts.push(`新增 ${item.added_items} 项`);
    if (item.removed_items) counts.push(`移除 ${item.removed_items} 项`);
    if (item.before_chars != null && item.after_chars != null) {
      counts.push(`${item.before_chars}→${item.after_chars} 字符`);
    }
    if (item.before_items != null && item.after_items != null) {
      counts.push(`${item.before_items}→${item.after_items} 项`);
    }
    return `${field}: ${shape}${counts.length ? `（${counts.join("，")}）` : ""}`;
  }).join("；");
}

function eventDetails(event) {
  const data = event.details || {};
  const wrapper = document.createElement("div");
  wrapper.className = "event-expanded";
  const grid = document.createElement("div");
  grid.className = "detail-grid";
  const fields = [
    ["Provider", data.provider_id],
    ["模型", data.model],
    ["状态", data.status || event.kind],
    ["关联状态", data.association_status],
    ["关联来源", data.association_source],
    ["关联年龄", data.association_age_ms == null ? "" : `${data.association_age_ms} ms`],
    ["LLM request ID", data.llm_request_id],
    ["调用 ID", data.invocation_id],
    ["响应 ID", data.response_id],
    ["缓存输入", data.input_cached == null ? "" : formatTokens(data.input_cached)],
    ["未缓存输入", data.input_other == null ? "" : formatTokens(data.input_other)],
    ["总输入", data.input_total == null ? "" : formatTokens(data.input_total)],
    ["输出", data.output == null ? "" : formatTokens(data.output)],
    ["报告缓存比例", data.cache_ratio == null ? "" : `${(data.cache_ratio * 100).toFixed(2)}%`],
    ["同次变化", data.request_hook_changed || data.hook_changed],
    ["上一轮变化", data.previous_turn_changed],
    ["改动字段", data.changed],
    ["改动插件", data.request_hook_plugins || data.mutation_plugins],
    ["改动处理器", data.request_hook_handlers || data.mutation_handlers],
    ["改动数量", data.request_mutation_count],
    ["处理器执行数", data.request_handler_trace_count ?? (data.request_handler_trace || data.handler_trace || []).length],
    ["已注册钩子数", data.request_handler_inventory_count ?? (data.request_handler_inventory || data.handler_inventory || []).length],
    ["注册清单截断", data.request_handler_inventory_truncated],
    ["执行链截断", data.request_handler_trace_truncated],
    ["请求快照指纹", data.request_snapshot_fingerprint],
    ["群", displayIdentity(data.group_name, data.group_hash)],
    ["群名来源", data.group_name_source],
    ["成员", displayIdentity(data.user_name, data.user_hash)],
    ["成员名来源", data.user_name_source],
    ["平台指纹", data.platform_hash],
  ];
  for (const [label, value] of fields) {
    if (value !== undefined && value !== null && value !== "") {
      grid.append(detailItem(label, value));
    }
  }
  wrapper.append(grid);
  if (data.before || data.after) {
    const snapshots = document.createElement("div");
    snapshots.className = "snapshot-grid";
    for (const [label, snapshot] of [["变更前快照（仅指纹与长度）", data.before], ["变更后快照（仅指纹与长度）", data.after]]) {
      const section = document.createElement("section");
      const title = document.createElement("h4");
      title.textContent = label;
      section.append(title);
      for (const [key, value] of Object.entries(snapshot || {})) {
        section.append(detailItem(key, value));
      }
      snapshots.append(section);
    }
    wrapper.append(snapshots);
  }
  const chain =
    data.request_handler_trace ||
    data.handler_trace ||
    data.mutation_chain ||
    [];
  if (chain.length) {
    const title = document.createElement("h4");
    title.textContent = "请求处理器执行链（含未改动项）";
    const list = document.createElement("ul");
    list.className = "mutation-chain";
    for (const run of chain) {
      const item = document.createElement("li");
      const changes = mutationDeltaSummary(run.delta || {});
      const status = run.outcome === "raised"
        ? `处理异常${run.error_type ? ` (${run.error_type})` : ""}`
        : run.outcome === "stopped_propagation"
          ? `停止后续钩子${changes ? `；${changes}` : ""}`
          : run.outcome === "observation_unavailable"
            ? "已执行，但请求字段变化观察不可用"
        : (run.changed || []).length
          ? changes || (run.changed || []).join(", ")
          : "已执行，未改动被观察的请求字段";
      item.textContent = `#${run.sequence || "?"} ${run.plugin || "未知插件"} · ${run.handler || "未知处理器"} · ${status}`;
      list.append(item);
    }
    wrapper.append(title, list);
  }
  const inventory =
    data.request_handler_inventory ||
    data.handler_inventory ||
    [];
  if (inventory.length) {
    const title = document.createElement("h4");
    title.textContent = "已注册的 LLM 请求钩子（含停用项）";
    const list = document.createElement("ul");
    list.className = "mutation-chain hook-inventory";
    const runsByHandler = new Map(
      chain.map((run) => [run.handler_full_name, run])
    );
    const propagationStopped = chain.some(
      (run) => run.outcome === "stopped_propagation"
    );
    for (const hook of inventory) {
      const run = runsByHandler.get(hook.handler_full_name);
      let status;
      if (run) {
        const changes = mutationDeltaSummary(run.delta || {});
        status = `本次已执行 #${run.sequence}${
          run.outcome === "stopped_propagation" ? "，并停止后续钩子" : ""
        }${run.outcome === "raised" ? `，异常 ${run.error_type || "未知"}` : ""}${
          (run.changed || []).length ? `；${changes}` : "；未改动被观察字段"
        }`;
      } else if (hook.status === "plugin_inactive") {
        status = "插件当前停用";
      } else if (hook.status === "handler_disabled") {
        status = "处理器已禁用";
      } else if (hook.status === "plugin_unavailable") {
        status = "插件实例不可用";
      } else if (hook.status === "plugin_filtered") {
        status = "不在本次事件插件范围";
      } else if (propagationStopped) {
        status = "符合调用条件，但前序钩子停止传播，未观察到执行";
      } else {
        status = "符合调用条件，但本次未观察到执行";
      }
      const item = document.createElement("li");
      item.textContent = `${hook.plugin || "未知插件"} · ${
        hook.handler || "未知处理器"
      } · 优先级 ${hook.priority ?? 0} · ${status}`;
      list.append(item);
    }
    if (data.request_handler_inventory_truncated || data.handler_inventory_truncated) {
      const item = document.createElement("li");
      item.textContent = "注册清单已达显示上限，详情中有截断；CSV 标记了完整数量。";
      list.append(item);
    }
    if (data.request_handler_trace_truncated || data.handler_trace_truncated) {
      const item = document.createElement("li");
      item.textContent = "执行链已达显示上限，未展示部分不会被判断为未执行。";
      list.append(item);
    }
    wrapper.append(title, list);
  }
  if (
    data.provider_request_snapshot &&
    Object.keys(data.provider_request_snapshot).length
  ) {
    const title = document.createElement("h4");
    title.textContent = "进入 Provider 的请求参数指纹（不含原文）";
    const snapshot = document.createElement("div");
    snapshot.className = "detail-grid";
    for (const [key, value] of Object.entries(data.provider_request_snapshot)) {
      snapshot.append(detailItem(key, value));
    }
    wrapper.append(title, snapshot);
  }
  if (data.delta && event.kind === "mutation") {
    const title = document.createElement("h4");
    title.textContent = "字段变化形态（不含原文）";
    const list = document.createElement("ul");
    list.className = "mutation-chain";
    for (const [field, detail] of Object.entries(data.delta)) {
      const item = document.createElement("li");
      item.textContent = `${mutationDeltaSummary({ [field]: detail })} · 指纹 ${detail.before_hash || "—"} → ${detail.after_hash || "—"}`;
      list.append(item);
    }
    wrapper.append(title, list);
  }
  return wrapper;
}

function eventPresentation(event) {
  const data = event.details || {};
  if (event.kind === "usage") {
    const labels = {
      cached_tokens_reported: ["服务商报告缓存 token", "good"],
      zero_cached_tokens_reported: ["报告缓存 token 为 0", "neutral"],
      no_input_tokens_reported: ["缺少输入用量", "warn"],
      usage_unavailable: ["服务商未返回 usage", "warn"],
      final_response_missing: ["未观察到最终流响应", "warn"],
      provider_call_failed: ["Provider 调用失败", "warn"],
      usage_parse_failed: ["usage 格式无法解析", "warn"],
    };
    const [label, tone] = labels[data.status] || [data.status || "未知", "neutral"];
    let tokens = "没有足够的输入 usage 计算比例";
    if (data.input_total > 0) {
      tokens = `报告缓存 ${formatTokens(data.input_cached)} / ${formatTokens(data.input_total)} 输入 · ${(
        (data.cache_ratio || 0) * 100
      ).toFixed(1)}%`;
    } else if (data.status === "final_response_missing") {
      tokens = "本次未取得可确认完整性的最终响应；不纳入 usage 比例";
    } else if (data.status === "provider_call_failed") {
      tokens = `调用异常（${data.error_type || "类型未知"}）；不纳入 usage 比例`;
    }
    const provider = data.provider_id
      ? `${data.provider_id} · ${data.model || "未知模型"}`
      : "";
    const hookChanges = (data.request_hook_changed || []).join(", ");
    const requestChangeNote = hookChanges
      ? `同次请求改动：${hookChanges}`
      : "";
    const associationNote =
      data.association_status === "associated"
        ? `已关联 ${(
            data.request_hook_plugins || []
          ).join(", ") || "请求钩子"}`
        : "未关联请求钩子";
    const inventoryNote = data.request_handler_inventory_count
      ? `本请求注册钩子 ${data.request_handler_inventory_count} 个`
      : "";
    const cacheNote =
      data.input_total > 0 && data.input_cached === 0
        ? "报告缓存为 0，仅作排查线索"
        : "";
    return {
      label,
      tone,
      detail: [provider, tokens, cacheNote, requestChangeNote, associationNote, inventoryNote]
        .filter(Boolean)
        .join(" · "),
    };
  }
  if (event.kind === "mutation") {
    const changed = (data.changed || []).join(", ") || "请求字段";
    return {
      label: "插件修改请求",
      tone: "warn",
      detail: `${data.plugin || "未知插件"} · ${data.handler || "未知处理器"} · ${changed}`,
    };
  }
  const changed = (data.hook_changed || []).join(", ");
  const previous = (data.previous_turn_changed || []).join(", ");
  const inventoryCount = data.handler_inventory_count || 0;
  return {
    label: "请求指纹",
    tone: "neutral",
    detail: changed
      ? `本轮钩子变化：${changed}${inventoryCount ? ` · 注册钩子 ${inventoryCount} 个` : ""}`
      : previous
        ? `与上一轮不同：${previous}${inventoryCount ? ` · 注册钩子 ${inventoryCount} 个` : ""}`
        : `本轮未观察到字段变化${inventoryCount ? ` · 注册钩子 ${inventoryCount} 个` : ""}`,
  };
}

function renderEvents(events) {
  const body = byId("events");
  body.replaceChildren();
  if (!events.length) {
    const row = document.createElement("tr");
    const empty = textCell("还没有诊断记录。发送几条消息后这里会显示结果。", "empty");
    empty.colSpan = 4;
    row.append(empty);
    body.append(row);
    return;
  }

  for (const event of events) {
    const row = document.createElement("tr");
    row.append(textCell(formatTime(event.created_at), "time-cell"));

    const presentation = eventPresentation(event);
    const badgeCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `badge ${presentation.tone}`;
    badge.textContent = presentation.label;
    badgeCell.append(badge);
    row.append(badgeCell);
    const detailCell = document.createElement("td");
    detailCell.className = "detail-cell";
    const detailText = document.createElement("div");
    detailText.textContent = presentation.detail;
    const expand = document.createElement("button");
    expand.type = "button";
    expand.className = "inline-button";
    expand.textContent = "展开详情";
    detailCell.append(detailText, expand);
    const sessionCell = document.createElement("td");
    sessionCell.className = "hash-cell";
    sessionCell.textContent = displayIdentity(
      event.details?.group_name || event.details?.user_name,
      event.session_hash
    );
    row.append(detailCell, sessionCell);
    const expandedRow = document.createElement("tr");
    expandedRow.className = "expanded-row";
    expandedRow.hidden = true;
    const expandedCell = document.createElement("td");
    expandedCell.colSpan = 4;
    expandedCell.append(eventDetails(event));
    expandedRow.append(expandedCell);
    expand.addEventListener("click", () => {
      expandedRow.hidden = !expandedRow.hidden;
      expand.textContent = expandedRow.hidden ? "展开详情" : "收起详情";
    });
    body.append(row);
    body.append(expandedRow);
  }
}

function renderPagination(pagination) {
  const page = pagination?.page || 1;
  const totalPages = pagination?.total_pages || 0;
  const totalEvents = pagination?.total_events || 0;
  byId("page-label").textContent = totalPages
    ? `第 ${page} / ${totalPages} 页`
    : "暂无记录";
  byId("previous-page").disabled = !pagination?.has_previous;
  byId("next-page").disabled = !pagination?.has_next;
  byId("limit-note").textContent = totalEvents
    ? `共 ${numberFormat.format(totalEvents)} 条，当前每页 ${pageSize} 条`
    : "";
  currentPage = page;
}

function renderSummary(summary) {
  byId("usage-records").textContent = formatTokens(summary.usage_records);
  byId("cached-records").textContent = formatTokens(summary.cached_records);
  byId("zero-cached-records").textContent = formatTokens(
    summary.zero_cached_records
  );
  byId("hook-changed-usage-records").textContent = formatTokens(
    summary.hook_changed_usage_records
  );
  byId("missing-usage").textContent = formatTokens(summary.missing_usage_records);
  byId("missing-final-note").textContent =
    `${formatTokens(summary.missing_final_records)} 次缺最终响应；${formatTokens(summary.failed_records)} 次调用异常`;
  byId("mutations").textContent = formatTokens(summary.mutations);
  byId("association-status").textContent = formatTokens(
    summary.associated_usage_records
  );
  byId("association-note").textContent =
    `${formatTokens(summary.unassociated_usage_records)} 次未关联`;
  const body = byId("models");
  body.replaceChildren();
  if (!summary.models?.length) {
    const row = document.createElement("tr");
    const empty = textCell("暂无可统计的输入 usage", "empty");
    empty.colSpan = 10;
    row.append(empty);
    body.append(row);
    return;
  }
  for (const item of summary.models) {
    const row = document.createElement("tr");
    row.append(
      textCell(item.provider_id || "未知"),
      textCell(item.model || "未知"),
      textCell(formatTokens(item.input_cached)),
      textCell(formatTokens(item.input_other)),
      textCell(`${(item.reported_cache_ratio * 100).toFixed(1)}%`),
      textCell(formatTokens(item.calls)),
      textCell(formatTokens(item.cached_calls)),
      textCell(formatTokens(item.zero_cached_calls)),
      textCell(formatTokens(item.hook_changed_calls)),
      textCell(formatTokens(item.unassociated_calls)),
    );
    body.append(row);
  }
}

function renderScopeNavigation(navigation) {
  const groups = navigation?.groups || [];
  if (
    selectedGroupHash &&
    !groups.some((item) => item.scope_hash === selectedGroupHash)
  ) {
    selectedGroupHash = "";
  }
  const groupList = byId("group-list");
  groupList.replaceChildren();
  byId("group-count").textContent = `${numberFormat.format(
    navigation?.group_count || 0
  )} 个群`;
  if (!groups.length) {
    const empty = document.createElement("div");
    empty.className = "scope-empty";
    empty.textContent = "还没有可归属的群聊 usage";
    groupList.append(empty);
  } else {
    for (const item of groups) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `scope-item ${
        item.scope_hash === selectedGroupHash ? "active" : ""
      }`;
      const title = document.createElement("span");
      title.className = "scope-item-title";
      title.textContent = displayIdentity(item.group_name, item.scope_hash, "群 ");
      const meta = document.createElement("span");
      meta.className = "scope-item-meta";
      meta.textContent = `${formatTokens(item.input_total)} 输入 · ${formatTokens(item.calls)} 次调用`;
      button.append(title, meta);
      button.addEventListener("click", () => {
        selectedGroupHash = item.scope_hash;
        currentScopePage = 1;
        load();
      });
      groupList.append(button);
    }
  }
  byId("private-count").textContent = `${numberFormat.format(
    navigation?.private_user_count || 0
  )} 个用户`;
  byId("selected-group").textContent = selectedGroupHash
    ? `当前群：${displayIdentity(
        groups.find((item) => item.scope_hash === selectedGroupHash)?.group_name,
        selectedGroupHash
      )}`
    : "群汇总；选择左侧群查看成员明细";
  byId("group-detail-button").hidden = !selectedGroupHash || !currentGroupDetail;
}

function renderScopeUsage(rows, pagination) {
  const body = byId("scope-usage");
  body.replaceChildren();
  if (!rows?.length) {
    const row = document.createElement("tr");
    const empty = textCell("暂无可按群或个人归属的 token usage", "empty");
    empty.colSpan = 9;
    row.append(empty);
    body.append(row);
  } else {
    for (const item of rows) {
      const row = document.createElement("tr");
      row.append(
        textCell(
          item.level === "group"
            ? "群汇总"
            : item.level === "private_user"
              ? "私聊用户"
              : "群成员"
        ),
        textCell(
          displayIdentity(item.group_name, item.group_hash || item.scope_hash),
          "hash-cell"
        ),
        textCell(
          displayIdentity(item.user_name, item.actor_hash || item.user_hash),
          "hash-cell"
        ),
        textCell(`${item.provider_id || "未知"} · ${item.model || "未知"}`),
        textCell(formatTokens(item.input_cached)),
        textCell(formatTokens(item.input_other)),
        textCell(formatTokens(item.output)),
        textCell(formatTokens(item.calls)),
        textCell(
          item.reported_cache_ratio == null
            ? "—"
            : `${(item.reported_cache_ratio * 100).toFixed(1)}%`
        )
      );
      body.append(row);
    }
  }
  const page = pagination?.page || 1;
  const totalPages = pagination?.total_pages || 0;
  byId("scope-page-label").textContent = totalPages
    ? `第 ${page} / ${totalPages} 页`
    : "暂无记录";
  byId("scope-previous-page").disabled = !pagination?.has_previous;
  byId("scope-next-page").disabled = !pagination?.has_next;
  byId("scope-limit-note").textContent = pagination?.total_rows
    ? `共 ${numberFormat.format(pagination.total_rows)} 条，每页 ${scopePageSize} 条`
    : "";
  currentScopePage = page;
}

function renderPrivateUsage(rows, pagination) {
  const body = byId("private-list");
  body.replaceChildren();
  if (!rows?.length) {
    const row = document.createElement("tr");
    const empty = textCell("还没有可归属的私聊 usage", "empty");
    empty.colSpan = 7;
    row.append(empty);
    body.append(row);
  } else {
    for (const item of rows) {
      const row = document.createElement("tr");
      row.append(
        textCell(
          displayIdentity(item.user_name, item.user_hash || item.actor_hash),
          "hash-cell"
        ),
        textCell(`${item.provider_id || "未知"} · ${item.model || "未知"}`),
        textCell(formatTokens(item.input_cached)),
        textCell(formatTokens(item.input_other)),
        textCell(formatTokens(item.output)),
        textCell(formatTokens(item.calls)),
        textCell(
          item.reported_cache_ratio == null
            ? "—"
            : `${(item.reported_cache_ratio * 100).toFixed(1)}%`
        )
      );
      body.append(row);
    }
  }
  const page = pagination?.page || 1;
  const totalPages = pagination?.total_pages || 0;
  byId("private-page-label").textContent = totalPages
    ? `第 ${page} / ${totalPages} 页`
    : "暂无记录";
  byId("private-previous-page").disabled = !pagination?.has_previous;
  byId("private-next-page").disabled = !pagination?.has_next;
  byId("private-limit-note").textContent = pagination?.total_rows
    ? `共 ${numberFormat.format(pagination.total_rows)} 条，每页 ${scopePageSize} 条`
    : "";
  currentScopePage = page;
}

function renderGroupDetail(detail) {
  currentGroupDetail = detail || null;
  byId("group-detail-button").hidden = !currentGroupDetail;
  if (!detail) return;
  const title = detail.group_name
    ? `${detail.group_name} · ${detail.group_hash}`
    : detail.group_hash;
  byId("group-modal-title").textContent = title || "群详情";
  byId("group-modal-subtitle").textContent =
    "名称为最近一次观察到的展示名；哈希用于确认同一统计维度。";
  const body = byId("group-modal-body");
  body.replaceChildren();
  const summary = document.createElement("div");
  summary.className = "modal-summary";
  for (const [label, value] of [
    ["总输入", formatTokens(detail.input_total)],
    ["缓存输入", formatTokens(detail.input_cached)],
    ["未缓存输入", formatTokens(detail.input_other)],
    ["输出", formatTokens(detail.output)],
    ["调用", formatTokens(detail.calls)],
    ["报告缓存率", detail.reported_cache_ratio == null ? "—" : `${(detail.reported_cache_ratio * 100).toFixed(2)}%`],
    ["有缓存调用", formatTokens(detail.cached_calls)],
    ["报告为 0", formatTokens(detail.zero_cached_calls)],
    ["同次有改动", formatTokens(detail.hook_changed_calls)],
    ["未关联", formatTokens(detail.unassociated_calls)],
    ["模型数", detail.model_count],
    ["成员数", detail.member_count],
  ]) summary.append(detailItem(label, value));
  body.append(summary);

  const makeTable = (title, columns, rows) => {
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    heading.textContent = title;
    section.append(heading);
    if (!rows?.length) {
      const empty = document.createElement("p");
      empty.className = "modal-empty";
      empty.textContent = "暂无数据";
      section.append(empty);
      return section;
    }
    const wrap = document.createElement("div");
    wrap.className = "table-wrap modal-table";
    const table = document.createElement("table");
    const head = document.createElement("tr");
    for (const column of columns) head.append(textCell(column, "modal-th"));
    table.append(head);
    for (const row of rows) {
      const tr = document.createElement("tr");
      for (const value of row) tr.append(textCell(value));
      table.append(tr);
    }
    wrap.append(table);
    section.append(wrap);
    return section;
  };
  body.append(makeTable(
    "按模型",
    ["服务商 / 模型", "输入", "缓存", "未缓存", "输出", "调用", "缓存率"],
    (detail.models || []).map((item) => [
      `${item.provider_id} · ${item.model}`,
      formatTokens(item.input_total),
      formatTokens(item.input_cached),
      formatTokens(item.input_other),
      formatTokens(item.output),
      formatTokens(item.calls),
      item.reported_cache_ratio == null ? "—" : `${(item.reported_cache_ratio * 100).toFixed(2)}%`,
    ])
  ));
  body.append(makeTable(
    "按成员",
    ["成员", "输入", "缓存", "未缓存", "输出", "调用", "缓存率"],
    (detail.members || []).map((item) => [
      displayIdentity(item.user_name, item.user_hash),
      formatTokens(item.input_total),
      formatTokens(item.input_cached),
      formatTokens(item.input_other),
      formatTokens(item.output),
      formatTokens(item.calls),
      item.reported_cache_ratio == null ? "—" : `${(item.reported_cache_ratio * 100).toFixed(2)}%`,
    ])
  ));
}

function openGroupModal() {
  if (!currentGroupDetail) return;
  byId("group-modal").hidden = false;
}

function closeGroupModal() {
  byId("group-modal").hidden = true;
}

function setScopeMode(mode) {
  scopeMode = mode;
  currentScopePage = 1;
  byId("scope-tab-groups").classList.toggle("active", mode === "groups");
  byId("scope-tab-private").classList.toggle("active", mode === "private");
  byId("scope-tab-groups").setAttribute(
    "aria-selected",
    String(mode === "groups")
  );
  byId("scope-tab-private").setAttribute(
    "aria-selected",
    String(mode === "private")
  );
  byId("groups-view").hidden = mode !== "groups";
  byId("private-view").hidden = mode !== "private";
  load();
}

async function load() {
  if (loadInProgress) {
    reloadRequested = true;
    return;
  }
  loadInProgress = true;
  const requestedScopeMode = scopeMode;
  const requestedGroupHash = selectedGroupHash;
  const hours = Number(byId("range").value);
  byId("notice").textContent = "正在读取诊断数据…";
  byId("notice").className = "notice";
  byId("refresh").disabled = true;
  try {
    const result = await bridge.apiGet("dashboard-data", {
      hours,
      page: currentPage,
      page_size: pageSize,
      scope_view:
        requestedScopeMode === "private"
          ? "private"
          : requestedGroupHash
            ? "group_members"
            : "groups",
      scope_hash: requestedGroupHash,
      scope_page: currentScopePage,
      scope_page_size: scopePageSize,
    });
    renderSummary(result.summary);
    renderScopeNavigation(result.scope_navigation);
    renderGroupDetail(result.group_detail);
    if (requestedScopeMode === "groups") {
      renderScopeUsage(result.scope_usage, result.scope_pagination);
    } else {
      renderPrivateUsage(result.scope_usage, result.scope_pagination);
    }
    renderEvents(result.events);
    renderPagination(result.pagination);
    byId("updated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN")}`;
    const unattributed = Number(
      result.summary?.unattributed_usage_records || 0
    );
    byId("notice").textContent =
      `已读取数据。usage 仅代表服务商响应报告值，不等于缓存实际命中或最终账单；指纹变化只能作为排查线索。${
        unattributed
          ? `另有 ${formatTokens(unattributed)} 条 usage 未能安全归属到群或用户。`
          : ""
      }`;
  } catch (error) {
    byId("notice").textContent = `读取失败：${error.message || "请确认插件已启用并重载"}`;
    byId("notice").className = "notice error";
  } finally {
    byId("refresh").disabled = false;
    loadInProgress = false;
    if (reloadRequested) {
      reloadRequested = false;
      load();
    }
  }
}

async function exportCsv() {
  const hours = Number(byId("range").value);
  const button = byId("export");
  button.disabled = true;
  byId("notice").textContent = "正在生成 CSV…";
  byId("notice").className = "notice";
  try {
    const result = await bridge.apiGet("export-data", { hours });
    const lines = [
      result.columns.map(csvCell).join(","),
      ...result.rows.map((row) =>
        result.columns.map((column) => csvCell(row[column])).join(",")
      ),
    ];
    const blob = new Blob([`\ufeff${lines.join("\r\n")}`], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `prompt-audit-${hours}h-${exportTimestamp()}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    byId("notice").textContent =
      `已导出 ${numberFormat.format(result.rows.length)} 条诊断记录。文件不含提示词或聊天正文。`;
  } catch (error) {
    byId("notice").textContent = `导出失败：${error.message || "请稍后重试"}`;
    byId("notice").className = "notice error";
  } finally {
    button.disabled = false;
  }
}

await bridge.ready();
byId("refresh").addEventListener("click", load);
byId("export").addEventListener("click", exportCsv);
byId("scope-tab-groups").addEventListener("click", () =>
  setScopeMode("groups")
);
byId("private-previous-page").addEventListener("click", () => {
  if (currentScopePage > 1) {
    currentScopePage -= 1;
    load();
  }
});
byId("private-next-page").addEventListener("click", () => {
  currentScopePage += 1;
  load();
});
byId("scope-tab-private").addEventListener("click", () =>
  setScopeMode("private")
);
byId("group-detail-button").addEventListener("click", openGroupModal);
byId("close-group-modal").addEventListener("click", closeGroupModal);
document.querySelector("[data-close-modal]").addEventListener("click", closeGroupModal);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeGroupModal();
});
byId("previous-page").addEventListener("click", () => {
  if (currentPage > 1) {
    currentPage -= 1;
    load();
  }
});
byId("next-page").addEventListener("click", () => {
  currentPage += 1;
  load();
});
byId("scope-previous-page").addEventListener("click", () => {
  if (currentScopePage > 1) {
    currentScopePage -= 1;
    load();
  }
});
byId("scope-next-page").addEventListener("click", () => {
  currentScopePage += 1;
  load();
});
byId("range").addEventListener("change", () => {
  currentPage = 1;
  currentScopePage = 1;
  load();
});
bridge.onContext(() => {
  document.documentElement.dataset.theme =
    bridge.getContext()?.isDark ? "dark" : "light";
});
load();
window.setInterval(() => {
  if (document.visibilityState === "visible") load();
}, 30_000);
