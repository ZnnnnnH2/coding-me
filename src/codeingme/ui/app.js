const STATE_ORDER = [
        "intake",
        "contract_generation",
        "test_red",
        "implementation_loop",
        "graph_sync",
        "cascade_update",
        "verification",
        "rollback",
        "done",
      ];

      const stateRail = document.getElementById("state-rail");
      const presetSelect = document.getElementById("preset-select");
      const loadPresetButton = document.getElementById("load-preset");
      const clearEditorsButton = document.getElementById("clear-editors");
      const startRunButton = document.getElementById("start-run");
      const refreshHistoryButton = document.getElementById("refresh-history");
      const fileLoader = document.getElementById("file-loader");
      const statusStrip = document.getElementById("status-strip");
      const historyList = document.getElementById("history-list");
      const eventFeed = document.getElementById("event-feed");
      const metricGrid = document.getElementById("metric-grid");
      const agentList = document.getElementById("agent-list");
      const agentDetail = document.getElementById("agent-detail");
      const workbenchTabs = document.getElementById("workbench-tabs");
      const drawerBackdrop = document.getElementById("drawer-backdrop");
      const specDrawer = document.getElementById("spec-drawer");
      const eventDrawer = document.getElementById("event-drawer");
      const openSpecDrawerButton = document.getElementById("open-spec-drawer");
      const openSpecInlineButton = document.getElementById("open-spec-inline");
      const closeSpecDrawerButton = document.getElementById("close-spec-drawer");
      const toggleEventDrawerButton = document.getElementById("toggle-event-drawer");
      const closeEventDrawerButton = document.getElementById("close-event-drawer");
      const cascadePanel = document.getElementById("cascade-panel");
      const graphPanel = document.getElementById("graph-panel");
      const artifactPanel = document.getElementById("artifact-panel");
      const fileList = document.getElementById("file-list");
      const fileMeta = document.getElementById("file-meta");
      const fileViewer = document.getElementById("file-viewer");
      const redLog = document.getElementById("red-log");
      const verificationLog = document.getElementById("verification-log");
      const heroState = document.getElementById("hero-state");
      const heroStatus = document.getElementById("hero-status");
      const heroEvents = document.getElementById("hero-events");
      const heroFiles = document.getElementById("hero-files");
      const AGENT_ORDER = ["architect", "qa", "backend", "devops"];

      const editors = {
        openapi: document.getElementById("openapi-input"),
        schema: document.getElementById("schema-input"),
        rules: document.getElementById("rules-input"),
        user_story: document.getElementById("story-input"),
      };

      let currentRunId = null;
      let currentSnapshot = null;
      let pollHandle = null;
      let selectedFilePath = null;
      let selectedAgentRole = null;
      let activeWorkbenchPanel = "agent";
      let isSpecDrawerOpen = false;
      let isEventDrawerOpen = false;

      function formatStateLabel(value) {
        return {
          intake: "接收",
          contract_generation: "合同生成",
          test_red: "红测",
          implementation_loop: "实现循环",
          graph_sync: "图谱同步",
          cascade_update: "级联更新",
          verification: "验证",
          rollback: "回滚",
          done: "完成",
        }[value] || value;
      }

      function formatStatusLabel(value) {
        return {
          idle: "空闲",
          queued: "排队中",
          running: "运行中",
          succeeded: "成功",
          failed: "失败",
          completed: "完成",
          active: "进行中",
          started: "开始",
          expected_failure: "预期失败",
          unexpected_pass: "意外通过",
        }[value] || value;
      }

      function escapeHtml(value) {
        return String(value)
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }

      function collectVisitedStates(snapshot) {
        const visited = new Set(["intake"]);
        const resultStates = snapshot?.result?.states || [];
        const events = snapshot?.events || [];
        resultStates.forEach((state) => {
          if (state) visited.add(state);
        });
        events.forEach((event) => {
          if (event.state) visited.add(event.state);
        });
        if (snapshot?.current_state) {
          visited.add(snapshot.current_state);
        }
        return visited;
      }

      function collectStatePath(snapshot) {
        const ordered = [];
        const seen = new Set();
        const push = (value) => {
          if (!value || seen.has(value)) return;
          seen.add(value);
          ordered.push(value);
        };
        push("intake");
        (snapshot?.result?.states || []).forEach(push);
        (snapshot?.events || [])
          .filter((event) => event.stage === "state")
          .forEach((event) => push(event.state));
        push(snapshot?.current_state);
        return ordered;
      }

      function renderStateRail(activeState, snapshot) {
        const visited = collectVisitedStates(snapshot);
        stateRail.innerHTML = STATE_ORDER.map((state) => {
          const classes = ["state-pill"];
          if (visited.has(state) && state !== activeState) {
            classes.push(state === "rollback" ? "recovery" : "done");
          }
          if (state === activeState) {
            classes.push("active");
            if (state === "rollback") {
              classes.push("recovery");
            }
          }
          return `<div class="${classes.join(" ")}">${formatStateLabel(state)}</div>`;
        }).join("");
      }

      function setStatus(text, status = "idle") {
        statusStrip.textContent = text;
        statusStrip.dataset.status = status;
      }

      function renderWorkbenchTabs() {
        Array.from(workbenchTabs.querySelectorAll("[data-panel]")).forEach((button) => {
          button.classList.toggle("is-active", button.dataset.panel === activeWorkbenchPanel);
        });
        Array.from(document.querySelectorAll(".workbench-panel")).forEach((panel) => {
          panel.classList.toggle("is-active", panel.id === `panel-${activeWorkbenchPanel}`);
        });
      }

      function renderDrawers() {
        drawerBackdrop.classList.toggle("is-open", isSpecDrawerOpen || isEventDrawerOpen);
        specDrawer.classList.toggle("is-open", isSpecDrawerOpen);
        eventDrawer.classList.toggle("is-open", isEventDrawerOpen);
        openSpecDrawerButton.textContent = isSpecDrawerOpen ? "收起" : "展开";
        toggleEventDrawerButton.textContent = isEventDrawerOpen ? "收起事件流" : "实时事件流";
      }

      function closeAllDrawers() {
        isSpecDrawerOpen = false;
        isEventDrawerOpen = false;
        renderDrawers();
      }

      function openSpecDrawer() {
        isSpecDrawerOpen = true;
        isEventDrawerOpen = false;
        renderDrawers();
      }

      function toggleEventDrawer() {
        isEventDrawerOpen = !isEventDrawerOpen;
        if (isEventDrawerOpen) {
          isSpecDrawerOpen = false;
        }
        renderDrawers();
      }

      function collectBundleFiles() {
        const files = {};
        if (editors.openapi.value.trim()) files["openapi.yaml"] = editors.openapi.value;
        if (editors.schema.value.trim()) files["schema.sql"] = editors.schema.value;
        if (editors.rules.value.trim()) files["business_rules.yaml"] = editors.rules.value;
        if (editors.user_story.value.trim()) files["user_story.md"] = editors.user_story.value;
        return files;
      }

      function resetEditors() {
        Object.values(editors).forEach((element) => {
          element.value = "";
        });
      }

      function applyPresetFiles(files) {
        resetEditors();
        for (const [name, content] of Object.entries(files)) {
          if (name.startsWith("openapi.")) editors.openapi.value = content;
          if (name === "schema.sql") editors.schema.value = content;
          if (name.includes("rules")) editors.rules.value = content;
          if (name === "user_story.md" || name === "README.md") editors.user_story.value = content;
        }
      }

      async function fetchJson(url, options = {}) {
        const response = await fetch(url, {
          headers: { "Content-Type": "application/json" },
          ...options,
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({ detail: response.statusText }));
          throw new Error(payload.detail || response.statusText);
        }
        return response.json();
      }

      function currentFileRecord() {
        return (currentSnapshot?.files || []).find((file) => file.path === selectedFilePath) || null;
      }

      function renderChips(items, emptyText = "暂无记录。") {
        if (!items.length) {
          return `<p class="panel-copy muted">${escapeHtml(emptyText)}</p>`;
        }
        return `<div class="chip-list">${items.map((item) => `<span class="chip">${escapeHtml(String(item))}</span>`).join("")}</div>`;
      }

      function renderDetailCard(title, items, emptyText = "暂无记录。") {
        return `
          <article class="detail-card">
            <strong>${escapeHtml(title)}</strong>
            ${renderChips(items, emptyText)}
          </article>
        `;
      }

      function renderArtifactValue(value) {
        if (Array.isArray(value)) {
          const simpleArray = value.every((item) => item === null || ["string", "number", "boolean"].includes(typeof item));
          if (simpleArray) {
          return renderChips(value.map((item) => String(item)), "暂无记录。");
          }
          return `<pre class="mini-json">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
        }
        if (value && typeof value === "object") {
          const entries = Object.entries(value);
          const simpleObject = entries.every(([, nestedValue]) => nestedValue === null || ["string", "number", "boolean"].includes(typeof nestedValue));
          if (simpleObject) {
            return `
              <div class="artifact-rows">
                ${entries.map(([nestedKey, nestedValue]) => `
                  <div class="artifact-row">
                    <span>${escapeHtml(nestedKey)}</span>
                    <code>${escapeHtml(String(nestedValue))}</code>
                  </div>
                `).join("")}
              </div>
            `;
          }
          return `<pre class="mini-json">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
        }
        if (value === null || value === undefined || value === "") {
          return `<p class="panel-copy muted">暂无记录。</p>`;
        }
        return `<code>${escapeHtml(String(value))}</code>`;
      }

      function translateEventMessage(message) {
        const exactMap = new Map([
          ["Accepted the structured requirement and reset the demo workspace.", "已接收结构化需求，并重置演示工作区。"],
          ["Architect agent is drafting contracts from the uploaded specification bundle.", "Architect Agent 正在根据上传的规格包起草合同。"],
          ["Generating initial schemas and API contracts.", "正在生成初始 Schema 和 API 合同。"],
          ["Created initial architecture contract", "已创建初始架构合同。"],
          ["Contract nodes were synced into the graph store.", "合同节点已同步到图存储。"],
          ["QA agent is drafting failing acceptance tests before implementation begins.", "QA Agent 正在实现开始前起草失败的验收测试。"],
          ["Generating red tests and harnesses for the target backend module.", "正在为目标后端模块生成红测与测试支架。"],
          ["Defined backend contract and rule checks", "已定义后端合同检查与业务规则检查。"],
          ["Applied generated red-test patches to the run workspace.", "已将生成的红测补丁应用到运行工作区。"],
          ["Running generated acceptance tests to confirm the workspace is red before implementation.", "正在运行生成的验收测试，以确认实现前工作区处于红测状态。"],
          ["Red test phase behaved as expected and the generated tests failed before implementation.", "红测阶段符合预期，生成的测试在实现前失败。"],
          ["Generated acceptance tests passed unexpectedly before implementation.", "生成的验收测试在实现前意外通过。"],
          ["Implementation agents are now generating backend and devops assets.", "实现阶段的 Agents 正在生成后端与 DevOps 产物。"],
          ["Backend agent is generating the FastAPI service and API contract surface.", "Backend Agent 正在生成 FastAPI 服务与 API 合同实现。"],
          ["Generated a FastAPI backend module with API contract coverage", "已生成带 API 合同覆盖的 FastAPI 后端模块。"],
          ["DevOps agent is preparing container and runtime wiring for the generated module.", "DevOps Agent 正在为生成模块准备容器与运行时配置。"],
          ["Prepared containerized runtime and verification artifacts", "已准备好容器化运行时与验证产物。"],
          ["Applied implementation patches from backend and devops agents.", "已应用来自 Backend 与 DevOps Agents 的实现补丁。"],
          ["Synchronizing generated Python files back into the graph model.", "正在将生成的 Python 文件同步回图模型。"],
          ["Graph synchronization finished for the latest generated runtime files.", "最新生成的运行时文件已完成图同步。"],
          ["Planner is executing graph-aware cascade updates for impacted backend and QA nodes.", "Planner 正在为受影响的后端与 QA 节点执行图感知的级联更新。"],
          ["Cascade update batches finished and the focused graph slice is ready for final verification.", "级联更新批次已完成，聚焦的图切片已准备好进入最终验证。"],
          ["Running final verification tests against the generated backend module.", "正在对生成的后端模块运行最终验证测试。"],
          ["Executing verification suite for the generated backend module.", "正在执行生成后端模块的验证测试套件。"],
          ["Verification failed. Rolling the workspace back to the latest safe checkpoint.", "验证失败，正在将工作区回滚到最近的安全检查点。"],
          ["Rollback manager is restoring the workspace after a failed verification pass.", "Rollback Manager 正在验证失败后恢复工作区。"],
          ["Verification suite passed and the generated backend module is accepted.", "验证测试套件已通过，生成的后端模块已被接受。"],
          ["Run completed successfully. Generated backend artifacts are ready to inspect.", "运行已成功完成，生成的后端产物可供检查。"],
          ["Studio launched a dedicated generation workspace for this run.", "Studio 已为本次运行启动独立的生成工作区。"],
          ["Queued the uploaded specification bundle for orchestration.", "已将上传的规格包加入编排队列。"],
          ["Bundle accepted and waiting for the generation pipeline to start.", "规格包已接收，等待生成流程开始。"],
          ["Run completed successfully.", "运行已成功完成。"],
        ]);
        if (exactMap.has(message)) {
          return exactMap.get(message);
        }
        const cascadeStart = message.match(/^Starting cascade batch (\d+)\.$/);
        if (cascadeStart) {
          return `正在启动 cascade batch ${cascadeStart[1]}。`;
        }
        const cascadeRerun = message.match(/^([A-Za-z]+) agent is re-running inside cascade batch (\d+)\.$/);
        if (cascadeRerun) {
          return `${cascadeRerun[1]} Agent 正在 cascade batch ${cascadeRerun[2]} 中重新运行。`;
        }
        const cascadeNoPatch = message.match(/^Cascade batch (\d+) produced no workspace patches\.$/);
        if (cascadeNoPatch) {
          return `cascade batch ${cascadeNoPatch[1]} 未产出工作区补丁。`;
        }
        const cascadePatched = message.match(/^Cascade batch (\d+) patches were applied and synced back into the graph\.$/);
        if (cascadePatched) {
          return `cascade batch ${cascadePatched[1]} 的补丁已应用并同步回图中。`;
        }
        return message;
      }

      async function loadPresets() {
        const payload = await fetchJson("/api/studio/presets");
        const presets = payload.presets || [];
        presetSelect.innerHTML = presets.map((preset) => {
          const label = preset.display_name || preset.summary || "示例规格包";
          const detail = preset.summary || preset.service_name || "";
          return `<option value="${preset.name}">${escapeHtml(label)}${detail ? ` · ${escapeHtml(detail)}` : ""}</option>`;
        }).join("");
        if (presets.length) {
          await loadPreset(presets[0].name);
        }
      }

      function renderRunHistory(runs) {
        if (!runs.length) {
          historyList.innerHTML = `<p class="panel-copy muted">当前还没有历史运行。</p>`;
          return;
        }
        historyList.innerHTML = runs.map((run) => `
          <article class="history-card ${run.run_id === currentRunId ? "is-active" : ""}">
            <header>
              <strong>${escapeHtml(run.bundle?.service_name || run.run_id)}</strong>
              <div class="history-meta">
                <span class="tag">${escapeHtml(formatStatusLabel(run.status))}</span>
                <span class="tag">${escapeHtml(formatStateLabel(run.current_state || "intake"))}</span>
              </div>
            </header>
            <p>${escapeHtml(run.bundle?.summary || run.requirement || "暂无说明。")}</p>
            <div class="history-meta">
              <span class="tag">${escapeHtml(new Date(run.updated_at).toLocaleString())}</span>
              <span class="tag">${escapeHtml(String(run.file_count || 0))} 个文件</span>
              ${run.resume_supported ? `<span class="tag">可续跑: ${escapeHtml(formatStateLabel(run.resume_from_state || ""))}</span>` : ""}
            </div>
            <div class="history-actions">
              <button class="button-secondary" type="button" data-history-open="${escapeHtml(run.run_id)}">查看</button>
              ${run.resume_supported ? `<button class="button-primary" type="button" data-history-resume="${escapeHtml(run.run_id)}">继续运行</button>` : ""}
            </div>
          </article>
        `).join("");

        Array.from(historyList.querySelectorAll("[data-history-open]")).forEach((button) => {
          button.addEventListener("click", async () => {
            await openRun(button.dataset.historyOpen);
          });
        });
        Array.from(historyList.querySelectorAll("[data-history-resume]")).forEach((button) => {
          button.addEventListener("click", async () => {
            await resumeRun(button.dataset.historyResume);
          });
        });
      }

      async function loadRunHistory() {
        const payload = await fetchJson("/api/studio/runs");
        renderRunHistory(payload.runs || []);
      }

      async function openRun(runId) {
        stopPolling();
        currentRunId = runId;
        selectedFilePath = null;
        const snapshot = await fetchJson(`/api/studio/runs/${runId}`);
        await renderSnapshot(snapshot);
        if (!TERMINAL_RUN_STATES.has(snapshot.status)) {
          pollHandle = setTimeout(pollRun, 700);
        }
      }

      async function resumeRun(runId) {
        stopPolling();
        setStatus("正在准备从失败点继续运行。", "running");
        const snapshot = await fetchJson(`/api/studio/runs/${runId}/resume`, {
          method: "POST",
        });
        currentRunId = snapshot.run_id;
        selectedFilePath = null;
        await renderSnapshot(snapshot);
        if (!TERMINAL_RUN_STATES.has(snapshot.status)) {
          pollHandle = setTimeout(pollRun, 700);
        }
      }

      async function loadPreset(name) {
        const payload = await fetchJson(`/api/studio/presets/${encodeURIComponent(name)}`);
        applyPresetFiles(payload.files || {});
        const bundle = payload.bundle || {};
        const label = payload.display_name || "示例规格包";
        setStatus(
          bundle.summary
            ? `已加载 ${label}。${bundle.summary}`
            : `已加载 ${label}。`,
          "idle"
        );
      }

      function renderMetrics(snapshot) {
        const bundle = snapshot?.bundle || {};
        const result = snapshot?.result || {};
        const metrics = [
          ["规格包", bundle.summary || bundle.service_name || "n/a"],
          ["当前状态", formatStateLabel(snapshot?.current_state || "intake")],
          ["运行状态", formatStatusLabel(snapshot?.status || "idle")],
          ["Endpoints", String((bundle.endpoints || []).length)],
          ["Tables", String((bundle.tables || []).length)],
          ["Graph Nodes", result.graph_nodes?.length ? String(result.graph_nodes.length) : "n/a"],
          ["Blast Radius", String((result.blast_radius || []).length)],
          ["Cascade Batches", String((result.cascade_batches || []).length)],
          ["Artifacts", String(Object.keys(result.artifacts || {}).length)],
        ];
        metricGrid.innerHTML = metrics.map(([label, value]) => `
          <div class="metric">
            <strong>${escapeHtml(label)}</strong>
            <span>${escapeHtml(String(value))}</span>
          </div>
        `).join("");
      }

      function formatRoleLabel(role) {
        return {
          architect: "架构",
          qa: "QA",
          backend: "后端",
          devops: "DevOps",
        }[role] || role;
      }

      function parseTimestampMs(value) {
        const parsed = Date.parse(value || "");
        return Number.isFinite(parsed) ? parsed : null;
      }

      function formatDuration(ms) {
        if (!ms) return "暂不可用";
        if (ms < 1000) return `${ms} 毫秒`;
        const seconds = ms / 1000;
        if (seconds < 60) {
          return `${seconds >= 10 ? seconds.toFixed(0) : seconds.toFixed(1)} 秒`;
        }
        const minutes = Math.floor(seconds / 60);
        const remainderSeconds = Math.round(seconds % 60);
        return `${minutes} 分 ${remainderSeconds} 秒`;
      }

      function collectAgentRecords(snapshot) {
        const artifacts = snapshot?.result?.artifacts || {};
        const events = snapshot?.events || [];
        return AGENT_ORDER.map((role) => {
          const roleEvents = events.filter((event) => event.role === role);
          const artifact = artifacts[role] || null;
          if (!roleEvents.length && !artifact) {
            return null;
          }

          let activeStartEvent = null;
          let totalDurationMs = 0;
          const phaseRuns = [];
          roleEvents.forEach((event) => {
            const timestampMs = parseTimestampMs(event.timestamp);
            if (event.status === "started") {
              activeStartEvent = event;
            }
            if (event.status === "completed" && activeStartEvent) {
              const startedMs = parseTimestampMs(activeStartEvent.timestamp);
              const durationMs =
                startedMs !== null && timestampMs !== null
                  ? Math.max(0, timestampMs - startedMs)
                  : null;
              if (durationMs !== null) {
                totalDurationMs += durationMs;
              }
              phaseRuns.push({
                state: event.state || activeStartEvent.state || "unknown",
                batch: event.batch ?? activeStartEvent.batch ?? null,
                started_at: activeStartEvent.timestamp,
                completed_at: event.timestamp,
                duration_ms: durationMs,
                patch_count: Number(event.details?.patch_count || 0),
                file_paths: Array.isArray(event.details?.file_paths) ? event.details.file_paths : [],
                generation_mode: event.details?.generation_mode || null,
                message: event.message,
              });
              activeStartEvent = null;
            }
          });

          const lastEvent = roleEvents[roleEvents.length - 1] || null;
          const latestStarted = [...roleEvents].reverse().find((event) => event.status === "started") || null;
          const latestCompleted = [...roleEvents].reverse().find((event) => event.status === "completed") || null;
          const latestMessage = latestCompleted?.message || lastEvent?.message || "当前还没有 Agent 摘要。";
          const latestContext = Array.isArray(latestStarted?.details?.context_node_ids)
            ? latestStarted.details.context_node_ids
            : [];
          const eventFilePaths = roleEvents.flatMap((event) =>
            Array.isArray(event.details?.file_paths) ? event.details.file_paths : []
          );
          const inferredFilePaths = [];
          if (typeof artifact?.test_file === "string") {
            inferredFilePaths.push(artifact.test_file);
          }
          if (typeof artifact?.service === "string" && artifact.service.includes("::")) {
            inferredFilePaths.push(artifact.service.split("::", 1)[0]);
          }
          if (role === "devops") {
            inferredFilePaths.push("Dockerfile", "docker-compose.yml", ".dockerignore");
          }
          const filePaths = [...new Set([...eventFilePaths, ...inferredFilePaths])];
          const artifactAttempts = Number.parseInt(String(artifact?.llm_attempts || ""), 10);
          const attemptRecords = Array.isArray(artifact?.llm_attempt_records) ? artifact.llm_attempt_records : [];
          const patchDiffs = Array.isArray(latestCompleted?.details?.patch_diffs)
            ? latestCompleted.details.patch_diffs
            : [];
          return {
            role,
            label: formatRoleLabel(role),
            artifact: artifact || {},
            events: roleEvents,
            lastEvent,
            latestStarted,
            latestCompleted,
            latestMessage,
            latestContext,
            filePaths,
            status: latestCompleted ? "completed" : latestStarted ? "running" : "idle",
            patchCount: Number(latestCompleted?.details?.patch_count || 0),
            generationMode: latestCompleted?.details?.generation_mode || artifact?.generation_mode || null,
            durationMs: totalDurationMs || null,
            attempts: Number.isFinite(artifactAttempts) ? artifactAttempts : null,
            attemptRecords,
            phaseRuns,
            patchDiffs,
          };
        }).filter(Boolean);
      }

      function ensureSelectedAgent(records) {
        if (!records.length) {
          selectedAgentRole = null;
          return null;
        }
        if (selectedAgentRole && records.some((record) => record.role === selectedAgentRole)) {
          return records.find((record) => record.role === selectedAgentRole) || records[0];
        }
        const latestRecord = [...records].sort((left, right) => {
          const leftMs = parseTimestampMs(left.lastEvent?.timestamp) || 0;
          const rightMs = parseTimestampMs(right.lastEvent?.timestamp) || 0;
          return rightMs - leftMs;
        })[0] || records[0];
        selectedAgentRole = latestRecord.role;
        return latestRecord;
      }

      function syncSelectedFileForAgent(record, snapshot) {
        if (!record) return;
        const availablePaths = new Set((snapshot?.files || []).map((file) => file.path));
        const candidatePaths = record.filePaths.filter((path) => availablePaths.has(path));
        if (!candidatePaths.length) return;
        if (!selectedFilePath || !candidatePaths.includes(selectedFilePath)) {
          selectedFilePath = candidatePaths[0];
        }
      }

      function renderAgentTrace(snapshot) {
        const records = collectAgentRecords(snapshot);
        if (!records.length) {
          agentList.innerHTML = `
            <p class="panel-copy muted">
              启动一次运行后，你可以查看每个 Agent 的最新状态及其产出文件。
            </p>
          `;
          agentDetail.innerHTML = `
            <p class="panel-copy muted">
              选择一个 Agent，查看它的状态、上下文、产出和相关运行事件。
            </p>
          `;
          return;
        }

        const selectedRecord = ensureSelectedAgent(records);
        syncSelectedFileForAgent(selectedRecord, snapshot);

        agentList.innerHTML = records.map((record) => `
          <button
            class="agent-button ${record.role === selectedAgentRole ? "is-active" : ""}"
            type="button"
            data-role="${escapeHtml(record.role)}"
          >
            <div class="agent-button__top">
              <strong class="agent-button__name">${escapeHtml(record.label)}</strong>
              <span class="tag" data-tone="${toneForEvent({ status: record.status === "completed" ? "completed" : "active" })}">
                ${escapeHtml(formatStatusLabel(record.status))}
              </span>
            </div>
            <div class="agent-button__meta">
              ${record.generationMode ? `<span class="tag">${escapeHtml(record.generationMode)}</span>` : ""}
              ${record.attempts ? `<span class="tag">${escapeHtml(String(record.attempts))} 次尝试</span>` : ""}
              ${record.filePaths.length ? `<span class="tag">${escapeHtml(String(record.filePaths.length))} 个文件</span>` : ""}
            </div>
            <p class="agent-button__summary">${escapeHtml(translateEventMessage(record.latestMessage))}</p>
          </button>
        `).join("");

        Array.from(agentList.querySelectorAll("button[data-role]")).forEach((button) => {
          button.addEventListener("click", async () => {
            selectedAgentRole = button.dataset.role;
            const activeRecord = records.find((record) => record.role === selectedAgentRole) || null;
            syncSelectedFileForAgent(activeRecord, currentSnapshot);
            renderAgentTrace(currentSnapshot);
            await renderFiles(currentSnapshot);
          });
        });

        const visibleRecord = selectedRecord || records[0];
        agentDetail.innerHTML = `
          <article class="agent-section">
            <div class="agent-detail__header">
              <div>
                <h4 class="agent-detail__title">${escapeHtml(visibleRecord.label)}</h4>
                <p class="agent-detail__copy">${escapeHtml(translateEventMessage(visibleRecord.latestMessage))}</p>
              </div>
              <div class="agent-button__meta">
                <span class="tag" data-tone="${toneForEvent({ status: visibleRecord.status === "completed" ? "completed" : "active" })}">
                  ${escapeHtml(formatStatusLabel(visibleRecord.status))}
                </span>
                  ${visibleRecord.generationMode ? `<span class="tag">${escapeHtml(visibleRecord.generationMode)}</span>` : ""}
                  ${visibleRecord.artifact?.llm_model ? `<span class="tag">${escapeHtml(String(visibleRecord.artifact.llm_model))}</span>` : ""}
                </div>
              </div>
          </article>
          <div class="summary-grid">
            <article class="summary-card">
              <strong>事件数</strong>
              <span>${escapeHtml(String(visibleRecord.events.length))}</span>
            </article>
            <article class="summary-card">
              <strong>耗时</strong>
              <span>${escapeHtml(formatDuration(visibleRecord.durationMs))}</span>
            </article>
            <article class="summary-card">
              <strong>补丁数</strong>
              <span>${escapeHtml(String(visibleRecord.patchCount))}</span>
            </article>
          </div>
          <article class="agent-section">
            <strong>上下文切片</strong>
            ${renderChips(visibleRecord.latestContext || [], "本次 Agent 运行没有记录上下文节点。")}
          </article>
          <article class="agent-section">
            <strong>阶段拆分</strong>
            ${
              visibleRecord.phaseRuns.length
                ? `<div class="agent-event-list">${visibleRecord.phaseRuns.map((run) => `
                    <div class="agent-event-card">
                      <div class="event-meta">
                        <span class="tag">${escapeHtml(formatStateLabel(run.state || "unknown"))}</span>
                        ${run.batch ? `<span class="tag">batch ${run.batch}</span>` : ""}
                        ${run.generation_mode ? `<span class="tag">${escapeHtml(String(run.generation_mode))}</span>` : ""}
                        <span class="tag">${escapeHtml(formatDuration(run.duration_ms))}</span>
                      </div>
                      <p>${escapeHtml(translateEventMessage(run.message || "Completed phase run."))}</p>
                    </div>
                  `).join("")}</div>`
                : `<p class="panel-copy muted">当前 Agent 还没有记录到已完成的阶段运行。</p>`
            }
          </article>
          <article class="agent-section">
            <strong>重试历史</strong>
            ${
              visibleRecord.attemptRecords.length
                ? `<div class="agent-event-list">${visibleRecord.attemptRecords.map((attempt) => `
                    <div class="agent-event-card">
                      <div class="event-meta">
                        <span class="tag">${escapeHtml(`第 ${attempt.attempt ?? "?"} 次`)}</span>
                        <span class="tag" data-tone="${attempt.success ? "success" : "active"}">${escapeHtml(attempt.success ? "成功" : "失败")}</span>
                        ${attempt.kind ? `<span class="tag">${escapeHtml(String(attempt.kind))}</span>` : ""}
                        ${attempt.model ? `<span class="tag">${escapeHtml(String(attempt.model))}</span>` : ""}
                        ${attempt.cached ? `<span class="tag">cached</span>` : ""}
                      </div>
                      <p>${escapeHtml(String(attempt.error || attempt.response_format || "本次尝试已完成，没有额外说明。"))}</p>
                    </div>
                  `).join("")}</div>`
                : `<p class="panel-copy muted">当前运行中，这个 Agent 没有记录独立的重试尝试。</p>`
            }
          </article>
          <article class="agent-section">
            <strong>相关文件</strong>
            ${
              visibleRecord.filePaths.length
                ? `<div class="file-chip-row">${visibleRecord.filePaths.map((path) => `
                    <button
                      class="file-chip-button ${selectedFilePath === path ? "is-active" : ""}"
                      type="button"
                      data-path="${escapeHtml(path)}"
                    >
                      ${escapeHtml(path)}
                    </button>
                  `).join("")}</div>`
                : `<p class="panel-copy muted">当前运行中，这个 Agent 没有产出工作区文件。</p>`
            }
          </article>
          <article class="agent-section">
            <strong>Artifact 快照</strong>
            ${renderArtifactValue(visibleRecord.artifact)}
          </article>
          <article class="agent-section">
            <strong>Patch Diff</strong>
            ${
              visibleRecord.patchDiffs.length
                ? visibleRecord.patchDiffs.map((patch) => `
                    <div class="agent-event-card">
                      <div class="event-meta">
                        <span class="tag">${escapeHtml(String(patch.operation || "patch"))}</span>
                        <span class="tag">${escapeHtml(String(patch.path || "unknown"))}</span>
                      </div>
                      <pre class="mini-json">${escapeHtml(String(patch.diff || ""))}</pre>
                    </div>
                  `).join("")
                : `<p class="panel-copy muted">本次 Agent 运行没有记录 patch diff。</p>`
            }
          </article>
          <article class="agent-section">
            <strong>相关事件</strong>
            <div class="agent-event-list">
              ${visibleRecord.events.length
                ? visibleRecord.events.slice().reverse().map((event) => `
                    <div class="agent-event-card">
                      <div class="event-meta">
                        <span class="tag" data-tone="${toneForEvent(event)}">${escapeHtml(event.stage)}</span>
                        <span class="tag">${escapeHtml(formatStatusLabel(event.status))}</span>
                        ${event.batch ? `<span class="tag">batch ${event.batch}</span>` : ""}
                      </div>
                      <p>${escapeHtml(translateEventMessage(event.message))}</p>
                    </div>
                  `).join("")
                : `<p class="panel-copy muted">当前还没有记录到 Agent 事件。</p>`}
            </div>
          </article>
        `;

        Array.from(agentDetail.querySelectorAll("button[data-path]")).forEach((button) => {
          button.addEventListener("click", async () => {
            selectedFilePath = button.dataset.path;
            activeWorkbenchPanel = "files";
            renderWorkbenchTabs();
            closeAllDrawers();
            renderAgentTrace(currentSnapshot);
            await renderFiles(currentSnapshot);
          });
        });
      }

      function renderCascade(snapshot) {
        const result = snapshot?.result || {};
        const batches = result.cascade_batches || [];
        const tasks = result.cascade_tasks || [];
        const taskMap = new Map(tasks.map((task) => [task.node_id, task]));
        if (!batches.length && !tasks.length) {
          cascadePanel.innerHTML = `
            <p class="panel-copy muted">
              运行进入图感知修复与影响传播阶段后，这里会显示 cascade planning 数据。
            </p>
          `;
          return;
        }
        const cyclicCount = tasks.filter((task) => task.cyclic).length;
        cascadePanel.innerHTML = `
          <div class="summary-grid">
            <article class="summary-card">
              <strong>受影响节点</strong>
              <span>${escapeHtml(String((result.blast_radius || []).length))}</span>
            </article>
            <article class="summary-card">
              <strong>批次数量</strong>
              <span>${escapeHtml(String(batches.length))}</span>
            </article>
            <article class="summary-card">
              <strong>循环任务</strong>
              <span>${escapeHtml(String(cyclicCount))}</span>
            </article>
          </div>
          ${renderDetailCard("执行顺序", result.cascade_order || [], "执行顺序暂不可用。")}
          <div class="batch-stack">
            ${batches.map((batch, index) => `
              <article class="batch-card">
                <header>
                  <h4>Batch ${index + 1}</h4>
                  <span class="tag">${escapeHtml(String(batch.length))} 个节点</span>
                </header>
                <div class="batch-node-list">
                  ${batch.map((nodeId) => {
                    const task = taskMap.get(nodeId) || {};
                    const dependencyCount = Array.isArray(task.dependencies) ? task.dependencies.length : 0;
                    const contextCount = Array.isArray(task.context_node_ids) ? task.context_node_ids.length : 0;
                    return `
                      <div class="batch-node">
                        <code>${escapeHtml(nodeId)}</code>
                        <div class="batch-meta">
                          ${task.role ? `<span class="tag">${escapeHtml(task.role)}</span>` : ""}
                          <span class="tag">${dependencyCount ? `${dependencyCount} 个依赖` : "root"}</span>
                          ${contextCount ? `<span class="tag">${contextCount} 个上下文</span>` : ""}
                          ${task.cyclic ? `<span class="tag">cyclic</span>` : ""}
                        </div>
                      </div>
                    `;
                  }).join("")}
                </div>
              </article>
            `).join("")}
          </div>
        `;
      }

      function renderGraph(snapshot) {
        const result = snapshot?.result || {};
        const statePath = collectStatePath(snapshot);
        const graphNodes = result.graph_nodes || [];
        const added = result.graph_sync_added || [];
        const removed = result.graph_sync_removed || [];
        const hasGraphData = statePath.length > 1 || graphNodes.length || added.length || removed.length;
        if (!hasGraphData) {
          graphPanel.innerHTML = `
            <p class="panel-copy muted">
              随着运行推进，这里会显示状态路径、Graph Sync 增量和聚焦的上下文切片。
            </p>
          `;
          return;
        }
        graphPanel.innerHTML = `
          <div class="summary-grid">
            <article class="summary-card">
              <strong>状态步数</strong>
              <span>${escapeHtml(String(statePath.length))}</span>
            </article>
            <article class="summary-card">
              <strong>新增节点</strong>
              <span>${escapeHtml(String(added.length))}</span>
            </article>
            <article class="summary-card">
              <strong>移除节点</strong>
              <span>${escapeHtml(String(removed.length))}</span>
            </article>
          </div>
          <div class="detail-list">
            ${renderDetailCard("状态路径", statePath.map((state) => formatStateLabel(state)), "尚未记录状态切换。")}
            ${renderDetailCard("Blast Radius", result.blast_radius || [], "Blast Radius 暂不可用。")}
            ${renderDetailCard("Context Slice", result.context_slice_nodes || [], "Context Slice 暂不可用。")}
            ${renderDetailCard("Graph Sync Added", added, "Graph Sync 暂无新增节点。")}
            ${renderDetailCard("Graph Sync Removed", removed, "Graph Sync 暂无移除节点。")}
          </div>
        `;
      }

      function renderArtifacts(snapshot) {
        const artifacts = snapshot?.result?.artifacts || {};
        const entries = Object.entries(artifacts);
        if (!entries.length) {
          artifactPanel.innerHTML = `
            <p class="panel-copy muted">
              各个 Agent 一旦产出 routes、文件与风险说明，这里就会显示对应汇总。
            </p>
          `;
          return;
        }
        artifactPanel.innerHTML = entries.map(([role, artifact]) => `
          <article class="artifact-card">
            <header>
              <h4>${escapeHtml(role)}</h4>
              <span class="tag">${escapeHtml(String(Object.keys(artifact || {}).length))} 项</span>
            </header>
            <div class="artifact-grid">
              ${Object.entries(artifact || {}).map(([key, value]) => `
                <section class="artifact-group">
                  <strong>${escapeHtml(key)}</strong>
                  ${renderArtifactValue(value)}
                </section>
              `).join("")}
            </div>
          </article>
        `).join("");
      }

      function toneForEvent(event) {
        if (event.status === "failed" || event.status === "unexpected_pass") return "active";
        if (event.status === "completed" || event.status === "expected_failure") return "success";
        return "active";
      }

      function renderEvents(snapshot) {
        const events = snapshot?.events || [];
        heroEvents.textContent = String(events.length);
        if (!events.length) {
          eventFeed.innerHTML = `
            <div class="event-card">
              <div class="event-meta"><span class="tag" data-tone="active">空闲</span></div>
              <strong>尚无运行活动</strong>
              <p>生成流程启动后，这里会展示状态变化、Agent 摘要、Graph Sync 工作和最终验证结果。</p>
            </div>
          `;
          return;
        }
        eventFeed.innerHTML = events.slice().reverse().map((event) => {
          const meta = [
            `<span class="tag" data-tone="${toneForEvent(event)}">${escapeHtml(event.stage)}</span>`,
            event.role ? `<span class="tag">${escapeHtml(event.role)}</span>` : "",
            event.batch ? `<span class="tag">batch ${event.batch}</span>` : "",
            event.state ? `<span class="tag">${escapeHtml(formatStateLabel(event.state))}</span>` : "",
          ].join("");
          const detailEntries = Object.entries(event.details || {});
          const detailCopy = detailEntries.length
            ? `<div class="batch-meta">${detailEntries.slice(0, 3).map(([key, value]) => `<span class="tag">${escapeHtml(key)}: ${escapeHtml(Array.isArray(value) ? String(value.length) : String(value))}</span>`).join("")}</div>`
            : "";
          return `
            <article class="event-card">
              <div class="event-meta">${meta}</div>
              <strong>${escapeHtml(translateEventMessage(event.message))}</strong>
              ${detailCopy}
              <p>${escapeHtml(new Date(event.timestamp).toLocaleString())}</p>
            </article>
          `;
        }).join("");
      }

      function syncFileViewerMode() {
        const file = currentFileRecord();
        fileMeta.textContent = file
          ? `${file.path} · ${file.language} · ${file.size} bytes`
          : "尚未选择生成文件。";
      }

      async function renderFiles(snapshot) {
        const files = snapshot?.files || [];
        heroFiles.textContent = String(files.length);
        if (!files.length) {
          fileList.innerHTML = "";
          fileViewer.textContent = "暂时还没有可用的生成文件。";
          selectedFilePath = null;
          syncFileViewerMode();
          return;
        }
        if (!selectedFilePath || !files.some((file) => file.path === selectedFilePath)) {
          selectedFilePath = files[0].path;
        }
        fileList.innerHTML = files.map((file) => `
          <button class="${file.path === selectedFilePath ? "active" : ""}" data-path="${escapeHtml(file.path)}">
            ${escapeHtml(file.path)}
          </button>
        `).join("");
        Array.from(fileList.querySelectorAll("button")).forEach((button) => {
          button.addEventListener("click", async () => {
            selectedFilePath = button.dataset.path;
            await renderFiles(currentSnapshot);
          });
        });
        await loadSelectedFile();
      }

      async function loadSelectedFile() {
        syncFileViewerMode();
        if (!currentRunId || !selectedFilePath) return;
        const response = await fetch(`/api/studio/runs/${currentRunId}/file?path=${encodeURIComponent(selectedFilePath)}`);
        if (!response.ok) {
          fileViewer.textContent = "无法加载当前选中的文件。";
          return;
        }
        fileViewer.textContent = await response.text();
      }

      function renderLogs(snapshot) {
        const result = snapshot?.result || {};
        redLog.textContent = result.red_test_output || snapshot?.error || "暂不可用。";
        verificationLog.textContent = result.verification_output || snapshot?.error || "暂不可用。";
      }

      async function renderSnapshot(snapshot) {
        currentSnapshot = snapshot;
        heroState.textContent = formatStateLabel(snapshot.current_state || "intake");
        heroStatus.textContent = formatStatusLabel(snapshot.status || "idle");
        setStatus(translateEventMessage(snapshot.current_message || "等待下一步操作。"), snapshot.status || "idle");
        renderStateRail(snapshot.current_state || "intake", snapshot);
        renderWorkbenchTabs();
        renderDrawers();
        renderMetrics(snapshot);
        renderAgentTrace(snapshot);
        renderCascade(snapshot);
        renderGraph(snapshot);
        renderArtifacts(snapshot);
        renderEvents(snapshot);
        renderLogs(snapshot);
        await renderFiles(snapshot);
        await loadRunHistory();
      }

      function stopPolling() {
        if (pollHandle) {
          clearTimeout(pollHandle);
          pollHandle = null;
        }
      }

      async function pollRun() {
        if (!currentRunId) return;
        const snapshot = await fetchJson(`/api/studio/runs/${currentRunId}`);
        await renderSnapshot(snapshot);
        if (!TERMINAL_RUN_STATES.has(snapshot.status)) {
          pollHandle = setTimeout(pollRun, 1100);
        }
      }

      async function startRun() {
        stopPolling();
        selectedFilePath = null;
        selectedAgentRole = null;
        closeAllDrawers();
        startRunButton.disabled = true;
        setStatus("正在创建新的 Studio 运行并准备生成工作区。", "running");
        try {
          const snapshot = await fetchJson("/api/studio/runs", {
            method: "POST",
            body: JSON.stringify({ files: collectBundleFiles() }),
          });
          currentRunId = snapshot.run_id;
          await renderSnapshot(snapshot);
          if (!TERMINAL_RUN_STATES.has(snapshot.status)) {
            pollHandle = setTimeout(pollRun, 700);
          }
        } catch (error) {
          setStatus(error.message, "failed");
        } finally {
          startRunButton.disabled = false;
        }
      }

      async function importFiles(fileListLike) {
        const files = Array.from(fileListLike || []);
        if (!files.length) return;
        const mapped = {};
        for (const file of files) {
          mapped[file.name] = await file.text();
        }
        applyPresetFiles(mapped);
        setStatus("已将本地结构化文件导入编辑器。确认内容后即可运行生成流程。", "idle");
      }

      const TERMINAL_RUN_STATES = new Set(["succeeded", "failed"]);

      loadPresetButton.addEventListener("click", async () => {
        if (!presetSelect.value) return;
        await loadPreset(presetSelect.value);
      });

      openSpecDrawerButton.addEventListener("click", () => {
        if (isSpecDrawerOpen) {
          closeAllDrawers();
          return;
        }
        openSpecDrawer();
      });

      openSpecInlineButton.addEventListener("click", () => {
        openSpecDrawer();
      });

      closeSpecDrawerButton.addEventListener("click", closeAllDrawers);
      toggleEventDrawerButton.addEventListener("click", toggleEventDrawer);
      closeEventDrawerButton.addEventListener("click", closeAllDrawers);
      drawerBackdrop.addEventListener("click", closeAllDrawers);

      Array.from(workbenchTabs.querySelectorAll("[data-panel]")).forEach((button) => {
        button.addEventListener("click", () => {
          activeWorkbenchPanel = button.dataset.panel || "agent";
          renderWorkbenchTabs();
        });
      });

      clearEditorsButton.addEventListener("click", () => {
        resetEditors();
        setStatus("编辑器已清空。请导入文件或加载示例规格包继续。", "idle");
      });

      startRunButton.addEventListener("click", startRun);
      refreshHistoryButton.addEventListener("click", () => {
        loadRunHistory().catch((error) => {
          setStatus(error.message, "failed");
        });
      });

      fileLoader.addEventListener("change", async (event) => {
        await importFiles(event.target.files);
        event.target.value = "";
      });

      syncFileViewerMode();
      renderWorkbenchTabs();
      renderDrawers();
      renderStateRail("intake", null);
      loadRunHistory().catch((error) => {
        setStatus(error.message, "failed");
      });
      loadPresets().catch((error) => {
        setStatus(error.message, "failed");
      });