// Rows of package names that select on click, each with an optional right-aligned
// value (a spec or a count) and a bar showing that count relative to the largest.
export function render({ model, el }) {
  const list = document.createElement("div");
  list.className = "pl-list";
  el.append(list);

  function build() {
    el.classList.toggle("dark", model.dark_theme);
    el.style.setProperty("--pl-bar", model.dark_theme ? model.bar_color_dark : model.bar_color);
    list.replaceChildren();
    if (!model.items.length) {
      const empty = document.createElement("div");
      empty.className = "pl-empty";
      empty.textContent = model.empty;
      list.append(empty);
      return;
    }
    for (const item of model.items) {
      const row = document.createElement("div");
      row.className = "pl-row";
      const name = document.createElement("a");
      name.className = "pl-name";
      name.textContent = item.name;
      name.title = item.name;
      name.addEventListener("click", () => model.send_msg({ type: "select", name: item.name }));
      row.append(name);
      if (item.bar !== undefined && item.bar !== null) {
        const track = document.createElement("span");
        track.className = "pl-track";
        const bar = document.createElement("i");
        bar.style.width = `${Math.max(item.bar, 0.02) * 100}%`;
        track.append(bar);
        row.append(track);
      }
      const value = document.createElement("span");
      value.className = "pl-value";
      value.textContent = item.value ?? "";
      value.title = item.value ?? "";
      row.append(value);
      list.append(row);
    }
  }

  build();
  model.on(["items", "dark_theme", "empty", "bar_color", "bar_color_dark"], build);
}
