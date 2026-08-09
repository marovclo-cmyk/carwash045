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
      <div class="rail-logo" title="CarWash Cloud">CW</div>

      <div class="rail-branch" id="branchSelect" title="Филиал: ${branch || "не выбран"}">
        <span id="bsValue">${initials(branch || "—")}</span>
        <span class="rail-branch-name" id="bsValueFull">${branch || "Филиал не выбран"}</span>
        <select id="bsSelect" style="position:absolute;inset:0;width:100%;height:100%;opacity:0;${role === 'владелец' ? 'cursor:pointer' : 'pointer-events:none'}"></select>
      </div>

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

    document.getElementById("branchSelect").style.position = "relative";

    document.getElementById("railToggle").addEventListener("click", () => {
      const next = !root.classList.contains("expanded");
      localStorage.setItem("cw_rail_expanded", next ? "1" : "0");
      renderSidebar(activeKey);
    });

    root.querySelectorAll(".rail-item[data-href]").forEach(el => {
      el.addEventListener("click", () => { window.location.href = el.dataset.href; });
    });
    document.getElementById("logoutBtn").addEventListener("click", logout);

    if (role === "владелец") {
      authFetch("/api/config").then(cfg => {
        const sel = document.getElementById("bsSelect");
        sel.innerHTML = cfg.branches.map(b => `<option value="${b}">${b}</option>`).join("");
        const current = getActiveBranch() || cfg.branches[0];
        sel.value = current;
        if (!getActiveBranch()) setActiveBranch(current);
        document.getElementById("bsValue").textContent = initials(current);
        document.getElementById("bsValueFull").textContent = current;
        document.getElementById("branchSelect").title = "Филиал: " + current;
        sel.addEventListener("change", () => {
          setActiveBranch(sel.value);
          window.location.reload();
        });
      }).catch(() => {});
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
  };
})();
