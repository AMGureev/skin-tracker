"""Non-blocking legacy market dialogs. No worker touches Tk or SQLite."""

import tkinter as tk
from tkinter import ttk, messagebox


class MarketActions:
    def __init__(self, app):
        self.app = app

    def selected_row(self):
        selection = self.app.tree.selection()
        if len(selection) != 1:
            messagebox.showwarning("CS Market", "Выберите ровно одну позицию.")
            return None
        if not self.app._cs_market_api_key_ok():
            return None
        return self.app._cs_market_skin_row(self.app.tree.item(selection[0], "values")[0])

    def request(self, method, callback, *, parent=None, **params):
        app = self.app
        key = app._get_market_api_key()

        def delivered(data):
            if parent is not None and not parent.winfo_exists():
                return
            if not data.get("success", False):
                messagebox.showerror("CS Market", "Маркет отклонил запрос. Проверьте API-ключ, цену и состояние лота.")
                return
            callback(data)

        app._tasks.submit(
            f"market-action:{method}:{sorted(params.items())}",
            lambda: app._cs_market_get(method, api_key=key, **params), delivered,
            lambda exc: messagebox.showerror("CS Market", f"Ошибка запроса: {type(exc).__name__}")
            if parent is None or parent.winfo_exists() else None,
        )

    def window(self, title):
        win = tk.Toplevel(self.app.root)
        win.title(title)
        self.app._style_toplevel(win)
        pad = ttk.Frame(win, style="App.TFrame", padding=16)
        pad.pack(fill=tk.BOTH, expand=True)
        return win, pad

    def listbox(self, parent, labels):
        frame = ttk.Frame(parent, style="App.TFrame")
        frame.pack(fill=tk.BOTH, expand=True, pady=8)
        scroll = ttk.Scrollbar(frame)
        box = tk.Listbox(frame, height=8, bg=self.app._c["bg_elevated"], fg=self.app._c["fg"],
                         selectbackground=self.app._c["accent"], yscrollcommand=scroll.set,
                         relief=tk.FLAT, highlightthickness=0)
        scroll.configure(command=box.yview)
        box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        for label in labels:
            box.insert(tk.END, label)
        return box

    def open_list(self):
        row = self.selected_row()
        if not row:
            return
        skin_id, name, stored_asset, _ = row
        app = self.app
        win, pad = self.window("CS Market — выставить предмет")
        tk.Label(pad, text=name, **app._label_kw(bold=True), wraplength=480).pack(anchor=tk.W)
        status = tk.StringVar(win, value="Обновите инвентарь на сайте и загрузите экземпляры.")
        tk.Label(pad, textvariable=status, **app._label_kw(), wraplength=480).pack(anchor=tk.W, pady=8)
        inv_items = []
        buttons = ttk.Frame(pad, style="App.TFrame")
        buttons.pack(fill=tk.X)

        def updated(data):
            status.set("Запрос отправлен. Через 10–20 с загрузите инвентарь.")

        def loaded(data):
            inv_items[:] = [item for item in data.get("items", [])
                            if item.get("market_hash_name") == name
                            and str(item.get("tradable")) in ("1", "true", "True")]
            box.delete(0, tk.END)
            for item in inv_items:
                box.insert(tk.END, f"asset {item.get('id')} · цена: {item.get('market_price', '—')}")
            status.set(f"Найдено экземпляров: {len(inv_items)}. Можно ввести asset id вручную.")

        def load_inventory():
            status.set("Загрузка инвентаря…")
            self.request("my-inventory", loaded, parent=win, lang="ru")

        ttk.Button(buttons, text="Обновить на сайте", command=lambda: self.request("update-inventory", updated, parent=win)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="Загрузить инвентарь", command=load_inventory).pack(side=tk.LEFT)
        box = self.listbox(pad, [])
        tk.Label(pad, text="Asset id (или выберите экземпляр выше)", **app._label_kw()).pack(anchor=tk.W)
        asset_var = tk.StringVar(win, value=stored_asset or "")
        tk.Entry(pad, textvariable=asset_var, **app._entry_kw()).pack(fill=tk.X, pady=4)
        price_row = ttk.Frame(pad, style="App.TFrame")
        price_row.pack(fill=tk.X, pady=8)
        price_var, currency = tk.StringVar(win), tk.StringVar(win, value="RUB")
        tk.Label(price_row, text="Цена", **app._label_kw()).pack(side=tk.LEFT, padx=4)
        tk.Entry(price_row, textvariable=price_var, width=16, **app._entry_kw()).pack(side=tk.LEFT, padx=8)
        ttk.Combobox(price_row, textvariable=currency, values=("RUB", "USD", "EUR"), state="readonly", width=8).pack(side=tk.LEFT)

        def listed(data, asset):
            item_id = data.get("item_id")
            if item_id is None:
                messagebox.showwarning("CS Market", "В ответе нет item_id. Проверьте лот на сайте.")
                return
            with app.conn:
                app.conn.execute("UPDATE skins SET market_asset_id=?, market_item_id=? WHERE id=?",
                                 (str(asset), str(item_id), skin_id))
            app.load_data()
            if win.winfo_exists():
                win.destroy()
            messagebox.showinfo("CS Market", f"Предмет выставлен. item_id: {item_id}")

        def sell():
            asset = asset_var.get().strip()
            if not asset and box.curselection():
                asset = str(inv_items[box.curselection()[0]].get("id", ""))
            try:
                price = float(price_var.get().replace(",", "."))
                if not asset or price <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("CS Market", "Укажите asset id и положительную цену.")
                return
            status.set("Выставление на продажу…")
            # Record the remote binding even if the dialog is closed while the request runs.
            self.request("add-to-sale", lambda data: listed(data, asset),
                         id=asset, price=app._price_to_market_api_int(price, currency.get()), cur=currency.get())

        ttk.Button(pad, text="Выставить", style="Success.TButton", command=sell).pack(anchor=tk.W, pady=8)
        app._autosize_toplevel(win, min_w=520, min_h=420)

    def active_items(self, row, callback):
        def loaded(data):
            _, name, _, stored_id = row
            items = [item for item in data.get("items", []) if str(item.get("status")) == "1"
                     and ((stored_id and str(item.get("item_id")) == str(stored_id))
                          or (not stored_id and item.get("market_hash_name") == name))]
            if not items:
                messagebox.showinfo("CS Market", "Активных лотов по этой записи нет.")
                return
            callback(items)
        self.request("items", loaded)

    def choose_item(self, title, items, callback):
        if len(items) == 1:
            callback(items[0])
            return
        win, pad = self.window(title)
        labels = [f"item_id {item.get('item_id')} · {self.app._market_api_int_to_display(item.get('price'), item.get('currency') or 'RUB')} {item.get('currency') or 'RUB'}"
                  for item in items]
        box = self.listbox(pad, labels)

        def choose():
            if not box.curselection():
                return
            item = items[box.curselection()[0]]
            win.destroy()
            callback(item)

        ttk.Button(pad, text="Выбрать", command=choose).pack(anchor=tk.E)
        self.app._autosize_toplevel(win, min_w=460, min_h=320)

    def open_remove(self):
        row = self.selected_row()
        if not row:
            return

        def remove(item):
            item_id, currency = item.get("item_id"), item.get("currency") or "RUB"
            if not messagebox.askyesno("CS Market", f"Снять с продажи {row[1]} (лот {item_id})?"):
                return

            def removed(data):
                with self.app.conn:
                    self.app.conn.execute("UPDATE skins SET market_asset_id=NULL, market_item_id=NULL WHERE id=?", (row[0],))
                self.app.load_data()
                messagebox.showinfo("CS Market", "Предмет снят с продажи.")

            self.request("set-price", removed, item_id=int(item_id), price=0, cur=currency)

        self.active_items(row, lambda items: self.choose_item("CS Market — снять лот", items, remove))

    def open_set_price(self):
        row = self.selected_row()
        if not row:
            return

        def edit(item):
            item_id, currency = item.get("item_id"), item.get("currency") or "RUB"
            old_price = self.app._market_api_int_to_display(item.get("price"), currency)
            win, pad = self.window("CS Market — цена лота")
            tk.Label(pad, text=f"{row[1]}\nСейчас: {old_price} {currency}", **self.app._label_kw()).pack(anchor=tk.W)
            variable = tk.StringVar(win, value=str(old_price))
            tk.Entry(pad, textvariable=variable, **self.app._entry_kw()).pack(fill=tk.X, pady=12)

            def saved(data):
                if win.winfo_exists():
                    win.destroy()
                messagebox.showinfo("CS Market", "Цена обновлена.")

            def save():
                try:
                    price = float(variable.get().replace(",", "."))
                    if price <= 0:
                        raise ValueError
                except ValueError:
                    messagebox.showwarning("CS Market", "Введите положительную цену.")
                    return
                self.request("set-price", saved, item_id=int(item_id),
                             price=self.app._price_to_market_api_int(price, currency), cur=currency)

            ttk.Button(pad, text="Сохранить цену", style="Success.TButton", command=save).pack(anchor=tk.W)
            self.app._autosize_toplevel(win, min_w=400, min_h=220)

        self.active_items(row, lambda items: self.choose_item("CS Market — выбрать лот", items, edit))
