// SA3 UI kit: icons. One SVG sprite (/sa3/ui/icons.svg), one name per icon.
//   el.innerHTML = icon("play")  → <svg class="ui-icon"><use href="/sa3/ui/icons.svg#play"/></svg>
export const icon = (name, cls = "") => `<svg class="ui-icon ${cls}" aria-hidden="true"><use href="/sa3/ui/icons.svg#${name}"/></svg>`;
