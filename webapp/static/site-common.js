/* site-common.js — общая логика для всех страниц сайта CarWash Cloud.
   Подключается на каждой странице (кроме site-login.html) первым скриптом.
   Отвечает за:
   - проверку входа (редирект на /static/site-login.html, если токена нет)
   - обёртку fetch с заголовком X-Site-Token и обработкой 401
   - рендер сайдбара (с подсветкой активного пункта)
   - выбор активного филиала (для владельца — переключаемый, для админа/мойщика — фиксированный)
*/

const CW = (() => {
  const API = ""; // сайт и API на одном хосте

  // Тема сайта («Glass / Orb») подключается статическим <link> в <head>
  // каждой HTML-страницы (сразу после инлайн-<style>, что гарантирует
  // правильный порядок каскада) — см. webapp/static/*.html. Раньше тема
  // подключалась через JS (document.head.appendChild) отсюда, но это
  // зависело от момента выполнения скрипта и было ненадёжно
  // (могло не успеть отработать до первой отрисовки/из-за кэша браузера).

  // Тема v2 «Studio Blue» (светлая) больше не использует плавающие орбы —
  // фон теперь простой градиент в теле site-theme.css. Функция оставлена
  // пустой (а не удалена), чтобы не трогать порядок вызовов ниже.
  (function injectOrbBg() {})();

  function getToken() { return localStorage.getItem("cw_token") || ""; }
  function getName() { return localStorage.getItem("cw_name") || ""; }
  function getRole() { return localStorage.getItem("cw_role") || ""; }
  function getLoginBranch() { return localStorage.getItem("cw_branch") || ""; }

  function getActiveBranch() {
    const role = getRole();
    if (role === "владелец") {
      return localStorage.getItem("cw_active_branch") || "";
    }
    return getLoginBranch();
  }

  function setActiveBranch(branch) {
    localStorage.setItem("cw_active_branch", branch);
  }

  // ---------- мини-календарь в раскрытом сайдбаре (как в YCLIENTS) ----------
  // calViewDate — какой месяц сейчас показан в мини-календаре (сбрасывается
  // при переходе на другую страницу — обычная навигация по <a>, не SPA).
  // calSelectCallback — если текущая страница умеет сама применять выбранную
  // дату без перезагрузки (см. booking.html → CW.onCalDateSelect), клик по
  // дню в календаре вызывает этот колбэк вместо перехода на /booking.html.
  let calViewDate = null;
  let calSelectCallback = null;

  const WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

  function isoLocal(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  function parseIsoLocal(s) {
    const [y, m, d] = s.split("-").map(Number);
    return new Date(y, m - 1, d);
  }

  // Дата, выбранная в мини-календаре сайдбара. По умолчанию — сегодня;
  // страница журнала записи (booking.html) синхронизирует её со своей
  // датой через setCalDate() при каждом изменении (стрелки/«Сегодня»/клик
  // по записи в сетке).
  function getCalDate() {
    const iso = localStorage.getItem("cw_cal_date");
    if (iso) {
      const d = parseIsoLocal(iso);
      if (!isNaN(d.getTime())) return d;
    }
    return new Date();
  }
  function setCalDate(d) {
    localStorage.setItem("cw_cal_date", typeof d === "string" ? d : isoLocal(d));
  }
  // Регистрирует колбэк, вызываемый при клике по дню в мини-календаре, пока
  // открыта текущая страница — чтобы обновить журнал без перезагрузки.
  // Если колбэк не зарегистрирован (мы не на booking.html), клик по дню
  // просто переходит на /static/booking.html?date=...
  function onCalDateSelect(fn) { calSelectCallback = fn; }

  function buildCalendarHtml() {
    const selected = getCalDate();
    if (!calViewDate) calViewDate = new Date(selected.getFullYear(), selected.getMonth(), 1);
    const y = calViewDate.getFullYear(), m = calViewDate.getMonth();
    const firstOfMonth = new Date(y, m, 1);
    const startOffset = (firstOfMonth.getDay() + 6) % 7; // Пн = 0 ... Вс = 6
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const daysInPrevMonth = new Date(y, m, 0).getDate();
    const today = new Date();
    const selIso = isoLocal(selected), todayIso = isoLocal(today);

    const cells = [];
    for (let i = 0; i < startOffset; i++) {
      cells.push({ day: daysInPrevMonth - startOffset + 1 + i, muted: true });
    }
    for (let d = 1; d <= daysInMonth; d++) cells.push({ day: d, date: new Date(y, m, d) });
    let nextD = 1;
    while (cells.length % 7 !== 0) cells.push({ day: nextD++, muted: true });

    const cellsHtml = cells.map(c => {
      if (!c.date) return `<div class="cal-cell muted">${c.day}</div>`;
      const iso = isoLocal(c.date);
      const cls = ["cal-cell"];
      if (iso === selIso) cls.push("selected");
      else if (iso === todayIso) cls.push("today");
      return `<div class="${cls.join(" ")}" data-date="${iso}">${c.day}</div>`;
    }).join("");

    return `
      <div class="rail-cal" id="railCal">
        <div class="rail-cal-head">
          <button type="button" class="rail-cal-nav" id="calPrevM"><i class="ti ti-chevron-left"></i></button>
          <span class="rail-cal-title">${MONTHS_RU[m]} ${y}</span>
          <button type="button" class="rail-cal-nav" id="calNextM"><i class="ti ti-chevron-right"></i></button>
        </div>
        <div class="rail-cal-weekdays">${WEEKDAYS_RU.map(w => `<span>${w}</span>`).join("")}</div>
        <div class="rail-cal-grid">${cellsHtml}</div>
      </div>`;
  }

  // Попап выбора филиала — один элемент на всё время жизни страницы,
  // подвешенный к <body> с position:fixed, чтобы не обрезаться узким
  // прокручиваемым сайдбаром (см. renderSidebar → branchSelect).
  let branchPopEl = null;
  function ensureBranchPopEl() {
    if (branchPopEl) return branchPopEl;
    branchPopEl = document.createElement("div");
    branchPopEl.className = "rail-branch-pop";
    branchPopEl.style.display = "none";
    branchPopEl.innerHTML = `
      <div class="rail-branch-pop-title">Филиалы</div>
      <div class="rail-branch-pop-list" id="branchPopList"><div class="rail-branch-pop-empty">Загрузка…</div></div>
    `;
    document.body.appendChild(branchPopEl);
    branchPopEl.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", () => { branchPopEl.style.display = "none"; });
    window.addEventListener("resize", () => { branchPopEl.style.display = "none"; });
    window.addEventListener("scroll", () => { branchPopEl.style.display = "none"; }, true);
    return branchPopEl;
  }

  function requireAuth() {
    if (!getToken()) {
      window.location.href = "/static/site-login.html";
      return false;
    }
    return true;
  }

  async function authFetch(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {}, {
      "X-Site-Token": getToken(),
    });
    if (opts.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const res = await fetch(API + path, Object.assign({}, opts, { headers }));
    if (res.status === 401) {
      logout();
      throw new Error("Сессия истекла");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      const msg = (data && data.detail) || `Ошибка запроса (${res.status})`;
      throw new Error(msg);
    }
    return data;
  }

  async function downloadFile(path, filenameFallback) {
    const headers = { "X-Site-Token": getToken() };
    const res = await fetch(API + path, { headers });
    if (res.status === 401) { logout(); throw new Error("Сессия истекла"); }
    if (!res.ok) {
      let msg = `Ошибка запроса (${res.status})`;
      try { const data = await res.json(); if (data && data.detail) msg = data.detail; } catch (e) {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    let filename = filenameFallback || "file";
    const disp = res.headers.get("Content-Disposition") || "";
    const m = disp.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
    if (m) { try { filename = decodeURIComponent(m[1]); } catch (e) { filename = m[1]; } }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  function logout() {
    localStorage.removeItem("cw_token");
    localStorage.removeItem("cw_name");
    localStorage.removeItem("cw_role");
    localStorage.removeItem("cw_branch");
    localStorage.removeItem("cw_active_branch");
    window.location.href = "/static/site-login.html";
  }

  const NAV = [
    { group: "Обзор", items: [
      { key: "dashboard", icon: "ti-layout-dashboard", label: "Дашборд", href: "/static/dashboard.html" },
      { key: "cars", icon: "ti-car", label: "Машины", href: "/static/cars.html" },
      { key: "booking", icon: "ti-calendar-event", label: "Запись", href: "/static/booking.html" },
      { key: "cash", icon: "ti-cash", label: "Касса за смену", href: "/static/cash.html" },
    ]},
    { group: "Управление", items: [
      { key: "workers", icon: "ti-users", label: "Сотрудники", href: "/static/workers.html" },
      { key: "clients", icon: "ti-address-book", label: "Клиенты", href: "/static/clients.html" },
      { key: "loyalty", icon: "ti-heart", label: "Лояльность", href: "/static/loyalty.html" },
      { key: "finance", icon: "ti-receipt", label: "Расходы и доходы", href: "/static/finance.html" },
      { key: "reports", icon: "ti-chart-bar", label: "Отчёты", href: "/static/reports.html" },
    ]},
    { group: "Система", items: [
      { key: "history", icon: "ti-history", label: "История изменений", href: "/static/history.html", adminOnly: true },
      { key: "branches", icon: "ti-building-store", label: "Филиалы", href: "/static/branches.html", ownerOnly: true },
      { key: "settings", icon: "ti-settings", label: "Настройки", href: "/static/settings.html" },
    ]},
  ];

  function initials(name) {
    const parts = (name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "??";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function roleLabel(role) {
    return { "мойщик": "Мойщик", "админ": "Администратор", "владелец": "Владелец" }[role] || role;
  }

  /* Рендерит сайдбар в элемент с id="sidebarRoot".
     activeKey — ключ текущей страницы (см. NAV[].items[].key).

     Тема v3 «Plata»: узкий (84px) чёрный icon-rail с оранжевым акцентом
     на активном пункте (см. webapp/static/css/theme-plata.css). Разметка
     здесь соответствует классам .rail-logo/.rail-items/.rail-item/
     .rail-bottom/.rail-avatar из этого файла; .rail-branch и .rail-div —
     доп. классы, которых не было в исходном визуальном макете темы
     (там сайдбар был статичным мокапом без выбора филиала/ролей), они
     добавлены в конец theme-plata.css в том же визуальном языке.
     Подписи пунктов — title-тултип на hover, как и раньше. */
  function renderSidebar(activeKey) {
    const root = document.getElementById("sidebarRoot");
    if (!root) return;
    root.classList.add("rail");
    const role = getRole();

    // состояние «свёрнут/развёрнут» — сохраняется между страницами (обычные
    // переходы по <a>, не SPA), поэтому применяем класс/CSS-переменную сразу,
    // до отрисовки, чтобы не было «мигания» ширины при загрузке страницы
    const expanded = localStorage.getItem("cw_rail_expanded") === "1";
    root.classList.toggle("expanded", expanded);
    document.documentElement.style.setProperty("--rail-w", expanded ? "232px" : "84px");

    const groupsHtml = NAV.map(group => {
      const items = group.items.filter(it =>
        (!it.ownerOnly || role === "владелец") &&
        (!it.adminOnly || role === "админ" || role === "владелец")
      );
      if (!items.length) return "";
      return items.map(it => `
        <a class="rail-item ${it.key === activeKey ? "active" : ""}" data-href="${it.href}" title="${it.label}">
          <i class="ti ${it.icon}"></i><span class="rail-item-label">${it.label}</span>
        </a>`).join("") + `<div class="rail-div"></div>`;
    }).filter(Boolean).join("");
    // убираем последний лишний разделитель после последней группы
    const itemsHtml = groupsHtml.replace(/<div class="rail-div"><\/div>$/, "");

    const branch = getActiveBranch();

    root.innerHTML = `
      <div class="rail-toggle" id="railToggle" title="${expanded ? "Свернуть меню" : "Развернуть меню"}">
        <i class="ti ${expanded ? "ti-layout-sidebar-left-collapse" : "ti-menu-2"}"></i>
      </div>
      <div class="rail-logo" title="CarWash Cloud"><span class="rail-logo-mark">CW</span><span class="rail-logo-full">CarWash</span></div>

      <div class="rail-branch${role === "владелец" ? "" : " no-click"}" id="branchSelect" title="Филиал: ${branch || "не выбран"}">
        <span id="bsValue">${initials(branch || "—")}</span>
        <span class="rail-branch-name" id="bsValueFull">${branch || "Филиал не выбран"}</span>
        ${role === "владелец" ? `<i class="ti ti-chevron-down rail-branch-chev"></i>` : ""}
      </div>

      ${expanded ? buildCalendarHtml() : ""}

      <div class="rail-items">${itemsHtml}</div>

      <div class="rail-bottom">
        <div class="rail-item" id="logoutBtn" title="Выйти"><i class="ti ti-logout"></i><span class="rail-item-label">Выйти</span></div>
        <div class="rail-avatar-row">
          <div class="rail-avatar" title="${getName() || "—"} · ${roleLabel(role)}">${initials(getName())}</div>
          <div style="min-width:0">
            <div class="rail-user-name">${getName() || "—"}</div>
            <div class="rail-user-role">${roleLabel(role) || ""}</div>
          </div>
        </div>
      </div>
    `;

    document.getElementById("railToggle").addEventListener("click", () => {
      const next = !root.classList.contains("expanded");
      localStorage.setItem("cw_rail_expanded", next ? "1" : "0");
      renderSidebar(activeKey);
    });

    root.querySelectorAll(".rail-item[data-href]").forEach(el => {
      el.addEventListener("click", () => { window.location.href = el.dataset.href; });
    });
    document.getElementById("logoutBtn").addEventListener("click", logout);

    // ---------- попап выбора филиала (клик по названию — как в YCLIENTS) ----------
    // Открыт только владельцу — админ/мойщик закреплены за филиалом входа
    // и просто видят его название (см. класс .no-click). Попап рендерится
    // на уровне <body> (fixed), а не внутри .rail — иначе его обрезало бы
    // overflow:hidden узкого сайдбара при раскрытии вправо.
    if (role === "владелец") {
      const btn = document.getElementById("branchSelect");
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const pop = ensureBranchPopEl();
        if (pop.style.display !== "none") { pop.style.display = "none"; return; }
        const rect = btn.getBoundingClientRect();
        pop.style.left = Math.round(rect.right + 10) + "px";
        pop.style.top = Math.round(rect.top) + "px";
        pop.style.display = "block";
      });

      authFetch("/api/config").then(cfg => {
        const current = getActiveBranch() || cfg.branches[0];
        if (!getActiveBranch()) setActiveBranch(current);
        document.getElementById("bsValue").textContent = initials(current);
        document.getElementById("bsValueFull").textContent = current;
        btn.title = "Филиал: " + current;
        const pop = ensureBranchPopEl();
        const listEl = pop.querySelector("#branchPopList");
        listEl.innerHTML = cfg.branches.map(b => `
          <div class="rail-branch-pop-item ${b === current ? "active" : ""}" data-branch="${b}">
            <span>${b}</span>
            ${b === current ? `<i class="ti ti-check"></i>` : ""}
          </div>
        `).join("");
        listEl.querySelectorAll(".rail-branch-pop-item").forEach(el => {
          el.addEventListener("click", () => {
            setActiveBranch(el.dataset.branch);
            window.location.reload();
          });
        });
      }).catch(() => {
        ensureBranchPopEl().querySelector("#branchPopList").innerHTML =
          `<div class="rail-branch-pop-empty">Не удалось загрузить филиалы</div>`;
      });
    }

    // ---------- мини-календарь (виден только в раскрытом сайдбаре) ----------
    if (expanded) {
      document.getElementById("calPrevM").addEventListener("click", () => {
        calViewDate = new Date(calViewDate.getFullYear(), calViewDate.getMonth() - 1, 1);
        renderSidebar(activeKey);
      });
      document.getElementById("calNextM").addEventListener("click", () => {
        calViewDate = new Date(calViewDate.getFullYear(), calViewDate.getMonth() + 1, 1);
        renderSidebar(activeKey);
      });
      root.querySelectorAll(".rail-cal-grid .cal-cell[data-date]").forEach(el => {
        el.addEventListener("click", () => {
          const iso = el.dataset.date;
          setCalDate(iso);
          calViewDate = null;
          if (calSelectCallback) {
            calSelectCallback(iso);
            renderSidebar(activeKey);
          } else {
            window.location.href = "/static/booking.html?date=" + iso;
          }
        });
      });
    }
  }

  function money(n) {
    return (Math.round(n || 0)).toLocaleString("ru-RU") + " ₽";
  }

  function todayLabel() {
    return new Date().toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
  }

  return {
    getToken, getName, getRole, getLoginBranch,
    getActiveBranch, setActiveBranch,
    requireAuth, authFetch, downloadFile, logout,
    renderSidebar, initials, roleLabel, money, todayLabel,
    getCalDate, setCalDate, onCalDateSelect,
  };
})();
