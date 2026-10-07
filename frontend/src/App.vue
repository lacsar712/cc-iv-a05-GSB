<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；场区当日停电工作票签完才放行写入。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <nav class="topbar">
        <button :class="{ on: view === 'scans' }" @click="switchView('scans')">扫描台</button>
        <button :class="{ on: view === 'tickets' }" @click="switchView('tickets')">工作票</button>
        <span class="who">{{ session.username }}（{{ roleLabel }}）</span>
        <button class="secondary" @click="refresh">刷新列表</button>
        <button class="secondary" @click="logout">退出</button>
      </nav>

      <div v-if="view === 'scans'">
        <section v-if="isWriter">
          <label>场区</label>
          <input v-model="site" placeholder="例如 一号场区" @input="checkSite" />
          <p v-if="siteHint" class="hint">{{ siteHint }}</p>
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>场区</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.site || "—" }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '等候处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <div v-else>
        <section>
          <h3>发起申请</h3>
          <p class="sub2">现场对场区当日停电工作票发出申请，值班长签字后该场区才允许写入。</p>
          <label>场区</label>
          <input v-model="ticketSite" placeholder="例如 一号场区" />
          <button :disabled="loading" @click="applyTicket">发出申请</button>
          <p v-if="ticketError" class="err">{{ ticketError }}</p>
          <p v-if="ticketMsg" class="okmsg">{{ ticketMsg }}</p>
        </section>
        <section>
          <h3>待签区</h3>
          <p v-if="!pendingTickets.length" class="idle">目前空闲</p>
          <table v-else>
            <thead>
              <tr><th>编号</th><th>场区</th><th>日期</th><th>发起人</th><th>申请时间</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="t in pendingTickets" :key="t.id">
                <td>{{ t.id }}</td>
                <td>{{ t.site }}</td>
                <td>{{ t.work_date }}</td>
                <td>{{ t.applicant }}</td>
                <td>{{ fmt(t.created_at) }}</td>
                <td>
                  <button v-if="canSign" :disabled="loading" @click="signTicket(t.id)">签字</button>
                  <span v-else>—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
        <section>
          <h3>已签区</h3>
          <p v-if="!signedTickets.length" class="idle">目前空闲</p>
          <table v-else>
            <thead>
              <tr><th>编号</th><th>场区</th><th>日期</th><th>发起人</th><th>签字人</th><th>签字时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="t in signedTickets" :key="t.id">
                <td>{{ t.id }}</td>
                <td>{{ t.site }}</td>
                <td>{{ t.work_date }}</td>
                <td>{{ t.applicant }}</td>
                <td>{{ t.signed_by }}</td>
                <td>{{ fmt(t.signed_at) }}</td>
              </tr>
            </tbody>
          </table>
        </section>
        <section>
          <h3>痕迹区</h3>
          <p v-if="!traces.length" class="idle">目前空闲</p>
          <table v-else>
            <thead>
              <tr><th>编号</th><th>票号</th><th>场区</th><th>动作</th><th>操作人</th><th>时间</th><th>详情</th></tr>
            </thead>
            <tbody>
              <tr v-for="tr in traces" :key="tr.id">
                <td>{{ tr.id }}</td>
                <td>{{ tr.ticket_id }}</td>
                <td>{{ tr.site }}</td>
                <td>{{ actionLabel(tr.action) }}</td>
                <td>{{ tr.actor }}</td>
                <td>{{ fmt(tr.created_at) }}</td>
                <td>{{ tr.detail }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const logs = ref([]);
const view = ref("scans");
const tickets = ref([]);
const traces = ref([]);
const canSign = ref(false);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const site = ref("");
const siteHint = ref("");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const ticketSite = ref("");
const ticketError = ref("");
const ticketMsg = ref("");
const error = ref("");
const loading = ref(false);
let timer;
let checkTimer;
const isWriter = computed(() => session.value?.role === "writer");
const roleLabel = computed(() => {
  const r = session.value?.role;
  if (r === "writer") return "可提交";
  if (r === "chief") return "值班长";
  return "只读";
});
const pendingTickets = computed(() => tickets.value.filter((t) => t.status === "pending"));
const signedTickets = computed(() => tickets.value.filter((t) => t.status === "signed"));
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(ts) {
  return ts ? new Date(ts).toLocaleString() : "—";
}
function actionLabel(a) {
  return a === "apply" ? "申请" : a === "sign" ? "签字" : a;
}
async function refresh() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
  if (view.value === "tickets") await loadTickets();
}
async function loadMe() {
  if (!session.value) return;
  const res = await fetch("/api/me", { headers: headers() });
  if (res.ok) {
    const d = await res.json();
    canSign.value = !!d.can_sign;
  }
}
async function loadTickets() {
  if (!session.value) return;
  const [t, tr] = await Promise.all([
    fetch("/api/tickets", { headers: headers() }),
    fetch("/api/tickets/traces", { headers: headers() }),
  ]);
  if (t.status === 401 || tr.status === 401) { logout(); return; }
  if (t.ok) tickets.value = await t.json();
  if (tr.ok) traces.value = await tr.json();
}
function switchView(v) {
  view.value = v;
  ticketError.value = "";
  ticketMsg.value = "";
  if (v === "tickets") loadTickets();
}
async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await Promise.all([refresh(), loadMe()]);
    timer = setInterval(refresh, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  tickets.value = [];
  traces.value = [];
  canSign.value = false;
  view.value = "scans";
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: stringCode.value,
        site: site.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "提交失败"; return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refresh();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function applyTicket() {
  ticketError.value = "";
  ticketMsg.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ site: ticketSite.value }),
    });
    const data = await res.json();
    if (!res.ok) { ticketError.value = data.detail || "申请失败"; return; }
    ticketMsg.value = `已发起 ${data.site} 当日停电工作票申请，等待值班长签字`;
    ticketSite.value = "";
    await loadTickets();
  } catch { ticketError.value = "申请时网络异常"; }
  finally { loading.value = false; }
}
async function signTicket(id) {
  ticketError.value = "";
  ticketMsg.value = "";
  loading.value = true;
  try {
    const res = await fetch(`/api/tickets/${id}/sign`, { method: "POST", headers: headers() });
    const data = await res.json();
    if (!res.ok) { ticketError.value = data.detail || "签字失败"; return; }
    ticketMsg.value = `${data.site} 当日停电工作票已签字放行`;
    await loadTickets();
  } catch { ticketError.value = "签字时网络异常"; }
  finally { loading.value = false; }
}
function checkSite() {
  clearTimeout(checkTimer);
  const s = site.value.trim();
  if (!s) { siteHint.value = ""; return; }
  checkTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/tickets/check?site=${encodeURIComponent(s)}`, { headers: headers() });
      if (!res.ok) return;
      const d = await res.json();
      siteHint.value = d.signed
        ? "当日停电工作票已签完，允许写入"
        : d.status === "pending"
          ? "当日停电工作票待签字，暂不能写入"
          : "当日停电工作票未申请，暂不能写入";
    } catch { /* 提示失败不影响提交，后端兜底拦截 */ }
  }, 300);
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      loadMe();
      timer = setInterval(refresh, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 980px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h3 { color: #86efac; margin: 0 0 0.5rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
.sub2 { color: #a7f3d0; font-size: 0.85rem; margin: 0 0 0.75rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.topbar { display: flex; align-items: center; gap: 0.5rem; background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 0.6rem 1rem; margin-bottom: 1rem; }
.topbar .who { margin-left: auto; color: #a7f3d0; font-size: 0.9rem; }
.topbar button { margin-right: 0; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.on { background: #4ade80; color: #052e16; }
.err { color: #fecaca; }
.okmsg { color: #86efac; }
.hint { color: #fde68a; font-size: 0.85rem; margin: -0.4rem 0 0.75rem; }
.idle { color: #6ee7b7; opacity: 0.75; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
</style>
