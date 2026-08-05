/* ============================================================
   common-plata.js — общий рендер сайдбара для всех макетов темы
   «Plata». Повторяет структуру NAV из оригинального site-common.js,
   но без авторизации/бэкенда — это визуальный макет (mock).
   ============================================================ */
const PLATA = (() => {
  const NAV = [
    { key: "dashboard", icon: "ti-layout-dashboard", label: "Дашборд", href: "dashboard.html" },
    { key: "cars", icon: "ti-car", label: "Машины", href: "cars.html" },
    { key: "cash", icon: "ti-cash", label: "Касса за смену", href: "cash.html" },
    { key: "workers", icon: "ti-users", label: "Сотрудники", href: "workers.html" },
    { key: "loyalty", icon: "ti-heart", label: "Лояльность", href: "loyalty.html" },
    { key: "finance", icon: "ti-receipt", label: "Расходы и доходы", href: "finance.html" },
    { key: "reports", icon: "ti-chart-bar", label: "Отчёты", href: "reports.html" },
    { key: "history", icon: "ti-history", label: "История изменений", href: "history.html" },
    { key: "branches", icon: "ti-building-store", label: "Филиалы", href: "branches.html" },
    { key: "settings", icon: "ti-settings", label: "Настройки", href: "settings.html" },
  ];

  function renderSidebar(activeKey) {
    const root = document.getElementById("railRoot");
    if (!root) return;
    const itemsHtml = NAV.map(it => `
      <a class="rail-item ${it.key === activeKey ? "active" : ""}" href="${it.href}" title="${it.label}">
        <i class="ti ${it.icon}"></i>
      </a>
    `).join("");
    root.innerHTML = `
      <div class="rail-logo">P</div>
      <div class="rail-items">${itemsHtml}</div>
      <div class="rail-bottom">
        <a class="rail-item" href="index.html" title="Выйти"><i class="ti ti-logout"></i></a>
        <div class="rail-avatar">АД</div>
      </div>
    `;
  }

  function money(n) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    return Math.round(n).toLocaleString("ru-RU") + " ₽";
  }

  function initials(name) {
    if (!name) return "?";
    return name.trim().split(/\s+/).slice(0, 2).map(w => w[0]).join("").toUpperCase();
  }

  const EMP_COLORS = ["#FF5000", "#1B8A57", "#FF711F", "#7A5CFF", "#0D0D0D", "#D63A2E"];
  function empColor(name) {
    let h = 0;
    for (let i = 0; i < (name || "").length; i++) h = (h * 31 + name.charCodeAt(i)) % EMP_COLORS.length;
    return EMP_COLORS[h];
  }

  return { renderSidebar, money, initials, empColor };
})();
