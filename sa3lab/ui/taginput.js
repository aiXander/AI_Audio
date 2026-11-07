// SA3 UI kit: tag input: chips + a text field with autocomplete from known tags. Enter or comma adds,
// Backspace on an empty field removes the last chip.
//   const ti = new TagInput(host, {tags: [...], known: [...], onChange(tags)})  ·  ti.tags  ·  ti.set([...])
export class TagInput {
  constructor(host, { tags = [], known = [], onChange = null, placeholder = "add tag" } = {}) {
    this.tags = [...tags];
    this.known = known;
    this.onChange = onChange;
    this.el = document.createElement("div");
    this.el.className = "ui-tags";
    this.inp = document.createElement("input");
    this.inp.className = "ui-tags-in";
    this.inp.placeholder = placeholder;
    this.list = document.createElement("datalist");
    this.list.id = "ui-tags-" + Math.random().toString(36).slice(2, 8);
    this.inp.setAttribute("list", this.list.id);
    this.list.innerHTML = known.map((t) => `<option value="${String(t).replace(/"/g, "&quot;")}">`).join("");
    host.appendChild(this.el);
    host.appendChild(this.list);
    this.inp.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === ",") && this.inp.value.trim()) { e.preventDefault(); this.add(this.inp.value); }
      else if (e.key === "Backspace" && !this.inp.value && this.tags.length) { this.tags.pop(); this.paint(); this.changed(); }
    });
    this.inp.addEventListener("change", () => { if (this.known.includes(this.inp.value)) this.add(this.inp.value); });
    this.paint();
  }
  add(t) {
    t = String(t).replace(/,/g, " ").trim();
    this.inp.value = "";
    if (!t || this.tags.some((x) => x.toLowerCase() === t.toLowerCase())) return;
    this.tags.push(t);
    this.paint();
    this.changed();
  }
  set(tags) { this.tags = [...tags]; this.paint(); }
  changed() { if (this.onChange) this.onChange(this.tags); }
  paint() {
    this.el.innerHTML = "";
    this.tags.forEach((t, i) => {
      const c = document.createElement("span");
      c.className = "ui-chip accent";
      c.textContent = t;
      const x = document.createElement("b");
      x.textContent = "×";
      x.onclick = () => { this.tags.splice(i, 1); this.paint(); this.changed(); };
      c.appendChild(x);
      this.el.appendChild(c);
    });
    this.el.appendChild(this.inp);
  }
}
