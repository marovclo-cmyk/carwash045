/* site-common.js — общий модуль для всех страниц CarWash Cloud.
   Подключается первым скриптом на каждой странице (после theme.css).
   Отвечает за: конфиг навигации, рендер сайдбара, мелкие хелперы. */

const CW = (() => {
  const NAV = [
    { key:"dashboard", icon:"fa-table-cells-large", label:"Дашборд", href:"dashboard.html" },
    { key:"cars",      icon:"fa-car",               label:"Машины", href:"cars.html" },
    { key:"cash",      icon:"fa-cash-register",     label:"Касса за смену", href:"cash.html" },
    { div:true },
    { key:"workers",   icon:"fa-users",             label:"Сотрудники", href:"workers.html" },
    { key:"loyalty",   icon:"fa-heart",             label:"Лояльность", href:"loyalty.html" },
    { key:"finance",   icon:"fa-receipt",           label:"Расходы и доходы", href:"finance.html" },
    { key:"reports",   icon:"fa-chart-bar",         label:"Отчёты", href:"reports.html" },
    { div:true },
    { key:"history",   icon:"fa-clock-rotate-left", label:"История изменений", href:"history.html" },
    { key:"branches",  icon:"fa-store",             label:"Филиалы", href:"branches.html" },
    { key:"settings",  icon:"fa-gear",              label:"Настройки", href:"settings.html" },
  ];

  const EMP_COLORS = ["#918df6","#2c78fc","#33c758","#d6409f","#ffa600","#9580ff"];

  function empColor(name){
    let h=0; for(let i=0;i<name.length;i++) h=(h*31+name.charCodeAt(i))%EMP_COLORS.length;
    return EMP_COLORS[h];
  }

  function initials(name){
    const parts=(name||"").trim().split(/\s+/).filter(Boolean);
    if(!parts.length) return "??";
    if(parts.length===1) return parts[0].slice(0,2).toUpperCase();
    return (parts[0][0]+parts[1][0]).toUpperCase();
  }

  function money(n){ return (Math.round(n||0)).toLocaleString("ru-RU")+" ₽"; }

  function todayLabel(){
    return new Date().toLocaleDateString("ru-RU",{day:"numeric", month:"long", year:"numeric"});
  }

  /* Рендерит сайдбар в #sidebarRoot. activeKey — ключ текущей страницы (NAV[].key).
     branches — { "Название": "ИН" } короткий инициал для чипа филиала; onBranchChange(name) — колбэк. */
  function renderSidebar(activeKey, opts = {}) {
    const root = document.getElementById("sidebarRoot");
    if (!root) return;
    const branches = opts.branches || {};
    const activeBranch = opts.activeBranch || Object.keys(branches)[0] || "";

    const navHtml = NAV.map(it => {
      if (it.div) return `<div class="rail-div"></div>`;
      return `<div class="rail-item ${it.key === activeKey ? "active" : ""}" data-href="${it.href}" data-tip="${it.label}">
                <i class="fa-solid ${it.icon}"></i>
              </div>`;
    }).join("");

    root.innerHTML = `
      <div class="rail-brand" data-tip="CarWash Cloud"><i class="fa-solid fa-droplet"></i></div>

      <div class="rail-branch" id="cwBranchSelect" data-tip="Филиал: ${activeBranch || "не выбран"}">
        <span id="cwBranchValue">${initials(activeBranch || "—")}</span>
        <select id="cwBranchSelectInput"></select>
      </div>

      <div class="rail-nav">${navHtml}</div>

      <div class="rail-foot">
        <div class="rail-item avatar" data-tip="${opts.userName || "Пользователь"} · ${opts.userRole || ""}">${initials(opts.userName || "")}</div>
        <div class="rail-item" id="cwLogoutBtn" data-tip="Выйти"><i class="fa-solid fa-arrow-right-from-bracket"></i></div>
      </div>
    `;

    root.querySelectorAll(".rail-item[data-href]").forEach(el => {
      el.addEventListener("click", () => { window.location.href = el.dataset.href; });
    });

    document.getElementById("cwLogoutBtn").addEventListener("click", () => {
      if (typeof opts.onLogout === "function") opts.onLogout();
    });

    const sel = document.getElementById("cwBranchSelectInput");
    sel.innerHTML = Object.keys(branches).map(b => `<option value="${b}">${b}</option>`).join("");
    sel.value = activeBranch;
    sel.addEventListener("change", () => {
      document.getElementById("cwBranchValue").textContent = initials(sel.value);
      document.getElementById("cwBranchSelect").dataset.tip = "Филиал: " + sel.value;
      if (typeof opts.onBranchChange === "function") opts.onBranchChange(sel.value);
    });
  }

  /* Toast — ожидает элемент <div class="toast" id="toast"><i></i><span id="toastText"></span></div> в DOM. */
  let toastTimer;
  function showToast(text) {
    const t = document.getElementById("toast");
    if (!t) return;
    document.getElementById("toastText").textContent = text;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
  }

  /* Анимированный count-up для чисел в metric-value / hero-value. */
  function countUp(el, to, isMoney) {
    const dur = 700;
    const start = performance.now();
    function tick(now) {
      const p = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      const val = Math.round(to * eased);
      el.textContent = isMoney ? money(val) : val;
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  return { NAV, empColor, initials, money, todayLabel, renderSidebar, showToast, countUp };
})();
