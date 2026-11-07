() => {
  // A "Composer" button next to the Arranger button: opens the Composer editor
  // (/sa3/page/composer, served by this same server: composer/server.py) in its own tab.
  const ICON = '<svg viewBox="0 0 16 16"><rect x="1.5" y="4" width="4.5" height="8" rx="1.2" stroke="currentColor" stroke-width="1.5" fill="none"/><rect x="10" y="4" width="4.5" height="8" rx="1.2" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M6.5 8h3" stroke="currentColor" stroke-width="1.5" stroke-dasharray="1.2 1.2"/></svg>';
  function mount() {
    const top = document.getElementById("sa3-right");
    if (!top || document.getElementById("sa3-compbtn")) return !!top;
    const b = document.createElement("button");
    b.id = "sa3-compbtn";
    b.title = "Composer: cut pieces from starred renders, leave gaps, render takes that bridge them";
    b.innerHTML = `${ICON}<span>Composer</span>`;
    b.addEventListener("click", () => window.open("/sa3/page/composer", "sa3-composer"));
    const anchor = document.getElementById("sa3-arrbtn") || document.getElementById("sa3-libbtn");
    if (anchor) top.insertBefore(b, anchor); else top.appendChild(b);
    return true;
  }
  const iv = setInterval(() => { if (mount() && document.getElementById("sa3-arrbtn")) clearInterval(iv); }, 500);
}
