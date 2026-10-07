<template>
  <main>
    <header class="topbar">
      <span class="brand">光伏组串IV扫描台</span>
      <template v-if="session">
        <nav>
          <button class="navbtn" :class="{ on: view === 'scans' }" @click="view = 'scans'">扫描记录</button>
          <button class="navbtn" :class="{ on: view === 'tickets' }" @click="view = 'tickets'">停电工作票</button>
        </nav>
        <span class="who">{{ session.username }}（{{ roleLabel }}）</span>
        <button class="secondary" @click="logout">退出</button>
      </template>
    </header>

    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>

    <div v-else-if="view === 'scans'">
      <p class="sub">已登录：{{ session.username }}（{{ roleLabel }}）。组串入队前会先核验该场区当日停电工作票是否签完，未签完一律拒收。</p>
      <section>
        <button class="secondary" @click="refresh">刷新列表</button>
      </section>
      <section v-if="isWriter">
        <label>场区</label><input v-model="scanSite" placeholder="例如 一号场区" />
        <p class="gate" :class="gateOk ? 'gate-ok' : 'gate-no'">{{ gateText }}</p>
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
      <p class="sub">当日停电工作票：现场发出申请，值班长签字后该场区才允许写入；签字记录与痕迹同批落库，发起人禁止给本人负责的场区代签。</p>
      <section v-if="canApply">
        <h3>发起申请</h3>
        <label>场区</label><input v-model="ticketSite" placeholder="例如 一号场区" />
        <button :disabled="loading" @click="applyTicket">发出申请</button>
        <p v-if="ticketError" class="err">{{ ticketError }}</p>
      </section>
      <section v-if="isChief">
        <h3>临时签字授权</h3>
        <label>用户名</label><input v-model="grantUser" placeholder="例如 watcher" />
        <button :disabled="loading" @click="grantPower">授予临时值班长签字权限</button>
        <p v-if="grantError" class="err">{{ grantError }}</p>
        <table v-if="grants.length">
          <thead>
            <tr><th>用户</th><th>权限</th><th>授权人</th><th>过期时间</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="g in grants" :key="g.id">
              <td>{{ g.username }}</td>
              <td>临时值班长签字</td>
              <td>{{ g.granted_by }}</td>
              <td>{{ fmt(g.expires_at) }}</td>
              <td><button class="secondary" @click="revokePower(g.id)">收回</button></td>
            </tr>
          </tbody>
        </table>
      </section>
      <section>
        <h3>待签区</h3>
        <p v-if="!pendingTickets.length" class="empty">目前空闲</p>
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
                <button
                  v-if="canSign"
                  :disabled="t.applicant === session.username"
                  :title="t.applicant === session.username ? '发起人禁止给本人负责的场区代签' : '值班长签字'"
                  @click="signTicket(t.id)"
                >签字</button>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <section>
        <h3>已签区</h3>
        <p v-if="!signedTickets.length" class="empty">目前空闲</p>
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
        <p v-if="!traces.length" class="empty">目前空闲</p>
        <table v-else>
          <thead>
            <tr><th>编号</th><th>工作票</th><th>场区</th><th>动作</th><th>操作人</th><th>时间</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="tr in traces" :key="tr.id">
              <td>{{ tr.id }}</td>
              <td>#{{ tr.ticket_id }}</td>
              <td>{{ tr.site }}</td>
              <td>{{ tr.action === 'apply' ? '申请' : '签字' }}</td>
              <td>{{ tr.actor }}</td>
              <td>{{ fmt(tr.created_at) }}</td>
              <td>{{ tr.detail }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const view = ref("scans");
const logs = ref([]);
const pendingTickets = ref([]);
const signedTickets = ref([]);
const traces = ref([]);
const grants = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const scanSite = ref("一号场区");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const ticketSite = ref("一号场区");
const grantUser = ref("watcher");
const error = ref("");
const ticketError = ref("");
const grantError = ref("");
const loading = ref(false);
let timer;
const ROLE_LABELS = { writer: "可提交", reader: "只读", chief: "值班长" };
const isWriter = computed(() => session.value?.role === "writer");
const isChief = computed(() => session.value?.role === "chief");
const roleLabel = computed(() => ROLE_LABELS[session.value?.role] || session.value?.role);
const canApply = computed(() => ["writer", "chief"].includes(session.value?.role));
const canSign = computed(
  () => isChief.value || grants.value.some((g) => g.username === session.value?.username)
);
const todayStr = new Date().toISOString().slice(0, 10);
const signedSitesToday = computed(
  () => new Set(signedTickets.value.filter((t) => t.work_date === todayStr).map((t) => t.site))
);
const gateOk = computed(() => !!scanSite.value.trim() && signedSitesToday.value.has(scanSite.value.trim()));
const gateText = computed(() => {
  const site = scanSite.value.trim();
  if (!site) return "请先填写场区";
  return gateOk.value
    ? `场区 ${site} 当日停电工作票已签完，允许入队`
    : `场区 ${site} 当日停电工作票未签完，提交将被拒`;
});
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString();
}
async function refresh() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
  const tres = await fetch("/api/tickets", { headers: headers() });
  if (tres.ok) {
    const data = await tres.json();
    pendingTickets.value = data.pending;
    signedTickets.value = data.signed;
    traces.value = data.traces;
  }
  const gres = await fetch("/api/grants", { headers: headers() });
  if (gres.ok) grants.value = await gres.json();
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
    view.value = "scans";
    await refresh();
    timer = setInterval(refresh, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  pendingTickets.value = [];
  signedTickets.value = [];
  traces.value = [];
  grants.value = [];
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
        site: scanSite.value,
        string_code: stringCode.value,
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
  loading.value = true;
  try {
    const res = await fetch("/api/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ site: ticketSite.value }),
    });
    const data = await res.json();
    if (!res.ok) { ticketError.value = data.detail || "申请失败"; return; }
    await refresh();
  } catch { ticketError.value = "申请时网络异常"; }
  finally { loading.value = false; }
}
async function signTicket(id) {
  ticketError.value = "";
  loading.value = true;
  try {
    const res = await fetch(`/api/tickets/${id}/sign`, {
      method: "POST",
      headers: headers(),
    });
    const data = await res.json();
    if (!res.ok) { ticketError.value = data.detail || "签字失败"; return; }
    await refresh();
  } catch { ticketError.value = "签字时网络异常"; }
  finally { loading.value = false; }
}
async function grantPower() {
  grantError.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/grants", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ username: grantUser.value }),
    });
    const data = await res.json();
    if (!res.ok) { grantError.value = data.detail || "授权失败"; return; }
    await refresh();
  } catch { grantError.value = "授权时网络异常"; }
  finally { loading.value = false; }
}
async function revokePower(id) {
  grantError.value = "";
  loading.value = true;
  try {
    const res = await fetch(`/api/grants/${id}`, { method: "DELETE", headers: headers() });
    const data = await res.json();
    if (!res.ok) { grantError.value = data.detail || "收回失败"; return; }
    await refresh();
  } catch { grantError.value = "收回时网络异常"; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      timer = setInterval(refresh, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 980px; margin: 0 auto; padding: 1.5rem; }
.topbar { display: flex; align-items: center; gap: 1rem; padding: 0.6rem 0.9rem; margin-bottom: 1rem; background: #064e3b; border: 1px solid #166534; border-radius: 8px; }
.topbar .brand { color: #86efac; font-weight: 700; font-size: 1.05rem; }
.topbar nav { display: flex; gap: 0.4rem; }
.topbar .who { margin-left: auto; color: #a7f3d0; font-size: 0.9rem; }
.navbtn { background: #14532d; color: #bbf7d0; border: 1px solid #166534; }
.navbtn.on { background: #16a34a; color: #fff; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h3 { color: #86efac; margin: 0 0 0.75rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.secondary { background: #365314; }
.err { color: #fecaca; }
.empty { color: #6ee7b7; font-style: italic; }
.gate { font-size: 0.85rem; margin: -0.4rem 0 0.75rem; }
.gate-ok { color: #86efac; }
.gate-no { color: #fde68a; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
</style>
