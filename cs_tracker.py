import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta
import sqlite3
import math
import requests
import json
from collections import defaultdict
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import urllib.parse

# 🔢 Коэффициенты
COEFFICIENT_BUY_ORDER = 0.9  # Множитель для цены топ-1 ордера (можно <1 для запаса)
COEFFICIENT_LIS = 1.02  # Комиссия/наценка на LIS

# API Key (замените на ваш ключ)
from cstracker.config import API_KEY


class SkinTracker:
    def __init__(self, root):
        self.root = root
        self.root.title("Трекер скинов CS:GO")
        self.root.geometry("1600x800")

        # Создание базы данных
        self.create_database()

        # Создание интерфейса
        self.create_widgets()
        self.load_data()
        self.load_summary()

        # Словарь для хранения истории продаж
        self.sales_history = {}

    def create_database(self):
        self.conn = sqlite3.connect('skins.db')
        cursor = self.conn.cursor()

        # Проверяем, существует ли таблица
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='skins'")
        table_exists = cursor.fetchone()

        if not table_exists:
            # Создаем новую таблицу
            cursor.execute('''
                CREATE TABLE skins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    buy_price REAL NOT NULL,
                    total_buy_price REAL NOT NULL,
                    buy_date TEXT NOT NULL,
                    unlock_date TEXT NOT NULL,
                    sell_date TEXT,
                    sell_price REAL,
                    profit REAL,
                    profit_percent REAL,
                    with_commission BOOLEAN NOT NULL,
                    with_sell_commission BOOLEAN NOT NULL DEFAULT 0
                )
            ''')
        else:
            # Проверяем, существует ли колонка with_sell_commission
            cursor.execute("PRAGMA table_info(skins)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'with_sell_commission' not in columns:
                # Добавляем недостающую колонку
                cursor.execute("ALTER TABLE skins ADD COLUMN with_sell_commission BOOLEAN NOT NULL DEFAULT 0")

        self.conn.commit()

    def create_widgets(self):
        # Верхняя панель с кнопками
        top_frame = tk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=10, pady=5)

        add_button = tk.Button(top_frame, text="Добавить покупку",
                               command=self.open_add_window, bg="#4CAF50", fg="white")
        add_button.pack(side=tk.LEFT, padx=5)

        refresh_button = tk.Button(top_frame, text="Обновить",
                                   command=self.refresh_all, bg="#2196F3", fg="white")
        refresh_button.pack(side=tk.LEFT, padx=5)

        # Новая кнопка для обновления цен с маркета
        self.market_button = tk.Button(top_frame, text="Обновить цены с маркета",
                                       command=self.update_market_prices, bg="#FF9800", fg="white")
        self.market_button.pack(side=tk.LEFT, padx=5)

        # Панель фильтров
        filter_frame = tk.Frame(self.root)
        filter_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(filter_frame, text="Фильтр по статусу:").pack(side=tk.LEFT, padx=5)
        self.status_filter_var = tk.StringVar(value="Все")
        status_combo = ttk.Combobox(filter_frame, textvariable=self.status_filter_var,
                                    values=["Все", "Ждет разблокировки", "Доступен для продажи", "Продан"],
                                    state="readonly", width=20)
        status_combo.pack(side=tk.LEFT, padx=5)
        status_combo.bind('<<ComboboxSelected>>', lambda e: self.load_data())

        # Таблица
        table_frame = tk.Frame(self.root)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Создание Treeview с новыми столбцами
        columns = ('id', 'name', 'quantity', 'price', 'total', 'buy_date',
                   'unlock_date', 'sell_date', 'sell_price', 'profit', 'status',
                   'order_buy', 'order_sell', 'history')

        self.tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=15)

        # Определение заголовков с сортировкой
        self.tree.heading('id', text='ID', command=lambda: self.sort_column('id'))
        self.tree.heading('name', text='Название скина', command=lambda: self.sort_column('name'))
        self.tree.heading('quantity', text='Кол-во', command=lambda: self.sort_column('quantity'))
        self.tree.heading('price', text='Цена за шт', command=lambda: self.sort_column('price'))
        self.tree.heading('total', text='Общая цена', command=lambda: self.sort_column('total'))
        self.tree.heading('buy_date', text='Дата покупки', command=lambda: self.sort_column('buy_date'))
        self.tree.heading('unlock_date', text='Разблокировка', command=lambda: self.sort_column('unlock_date'))
        self.tree.heading('sell_date', text='Дата продажи', command=lambda: self.sort_column('sell_date'))
        self.tree.heading('sell_price', text='Цена продажи', command=lambda: self.sort_column('sell_price'))
        self.tree.heading('profit', text='Профит', command=lambda: self.sort_column('profit'))
        self.tree.heading('status', text='Статус', command=lambda: self.sort_column('status'))
        self.tree.heading('order_buy', text='Order Buy', command=lambda: self.sort_column('order_buy'))
        self.tree.heading('order_sell', text='Order Sell', command=lambda: self.sort_column('order_sell'))
        self.tree.heading('history', text='History', command=lambda: self.sort_column('history'))

        # Настройка ширины колонок
        self.tree.column('id', width=40)
        self.tree.column('name', width=150)
        self.tree.column('quantity', width=60)
        self.tree.column('price', width=80)
        self.tree.column('total', width=90)
        self.tree.column('buy_date', width=100)
        self.tree.column('unlock_date', width=100)
        self.tree.column('sell_date', width=100)
        self.tree.column('sell_price', width=90)
        self.tree.column('profit', width=100)
        self.tree.column('status', width=120)
        self.tree.column('order_buy', width=90)
        self.tree.column('order_sell', width=90)
        self.tree.column('history', width=90)

        # Скроллбары
        v_scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scrollbar = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)

        # Размещение элементов
        self.tree.grid(row=0, column=0, sticky='nsew')
        v_scrollbar.grid(row=0, column=1, sticky='ns')
        h_scrollbar.grid(row=1, column=0, sticky='ew')

        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        # Контекстное меню
        self.tree.bind('<Button-3>', self.show_context_menu)
        self.tree.bind('<Double-1>', self.on_history_click)  # Добавляем обработчик двойного клика

        # Меню для продажи
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Продать", command=self.open_sell_window)
        self.context_menu.add_command(label="Удалить", command=self.delete_item)

        # Панель статистики с выбором периода
        self.create_summary_widgets()

        # Словари для хранения цен с маркета
        self.buy_order_prices = {}
        self.sell_order_prices = {}
        # Словарь для хранения истории продаж
        self.sales_history = {}

    def create_summary_widgets(self):
        self.summary_frame = tk.Frame(self.root)
        self.summary_frame.pack(fill=tk.X, padx=10, pady=5)

        # Заголовок статистики
        tk.Label(self.summary_frame, text="📊 СТАТИСТИКА", font=("Arial", 12, "bold")).pack(anchor=tk.W)

        # Панель выбора периода
        period_frame = tk.Frame(self.summary_frame)
        period_frame.pack(fill=tk.X, pady=5)

        tk.Label(period_frame, text="Период:").pack(side=tk.LEFT, padx=5)

        # Дата начала
        tk.Label(period_frame, text="С:").pack(side=tk.LEFT, padx=5)
        self.start_date_var = tk.StringVar(value="01.01.2020")
        tk.Entry(period_frame, textvariable=self.start_date_var, width=10).pack(side=tk.LEFT, padx=2)

        # Дата окончания
        tk.Label(period_frame, text="По:").pack(side=tk.LEFT, padx=5)
        self.end_date_var = tk.StringVar(value=datetime.now().strftime("%d.%m.%Y"))
        tk.Entry(period_frame, textvariable=self.end_date_var, width=10).pack(side=tk.LEFT, padx=2)

        # Кнопка обновления статистики
        tk.Button(period_frame, text="Обновить статистику", command=self.load_summary,
                  bg="#FF9800", fg="white").pack(side=tk.LEFT, padx=10)

        # Метка для отображения статистики
        self.summary_label = tk.Label(self.summary_frame, text="", justify=tk.LEFT, fg="blue")
        self.summary_label.pack(pady=5)

    def refresh_all(self):
        self.load_data()
        self.load_summary()

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def sort_column(self, col):
        """Сортировка по колонке"""
        # Получаем текущие данные
        data = []
        for item in self.tree.get_children():
            values = self.tree.item(item)['values']
            data.append((values, item))

        # Определяем тип сортировки
        numeric_columns = ['id', 'quantity', 'price', 'total', 'sell_price', 'profit', 'order_buy', 'order_sell',
                           'history']
        if col in numeric_columns:
            # Числовая сортировка
            def extract_number(value):
                if value == '-' or value == '':
                    return 0
                try:
                    return float(str(value).replace(',', '.').replace(' руб.', '').replace('%', ''))
                except:
                    return 0

            data.sort(key=lambda x: extract_number(x[0][self.get_column_index(col)]))
        else:
            # Строковая сортировка
            data.sort(key=lambda x: str(x[0][self.get_column_index(col)]))

        # Перестраиваем таблицу
        for index, (values, item) in enumerate(data):
            self.tree.move(item, '', index)

    def get_column_index(self, col_name):
        """Получение индекса колонки по имени"""
        columns = ['id', 'name', 'quantity', 'price', 'total', 'buy_date',
                   'unlock_date', 'sell_date', 'sell_price', 'profit', 'status',
                   'order_buy', 'order_sell', 'history']
        return columns.index(col_name)

    def on_history_click(self, event):
        """Обработчик двойного клика по колонке History"""
        item = self.tree.identify_row(event.y)
        # Исправлено: identify_column принимает только x координату
        column = self.tree.identify_column(event.x)

        # Проверяем, что кликнули по колонке History (колонка 14, '#14')
        if item and column == '#14':
            self.tree.selection_set(item)
            self.show_history_graph()

    def show_history_graph(self):
        """Отображение графика истории продаж"""
        selected = self.tree.selection()
        if not selected:
            return

        item = self.tree.item(selected[0])
        values = item['values']
        skin_name = values[1]  # Название скина

        # Получаем историю продаж из словаря (загружена при обновлении цен)
        history_data = self.sales_history.get(skin_name, [])

        if not history_data:
            messagebox.showwarning("Предупреждение", f"Нет данных истории продаж для {skin_name}")
            return

        # Создаем новое окно для графика
        graph_window = tk.Toplevel(self.root)
        graph_window.title(f"История продаж: {skin_name}")
        graph_window.geometry("800x600")

        # Создаем фигуру matplotlib
        fig, ax = plt.subplots(figsize=(10, 6))

        # Подготавливаем данные для графика
        # Берем последние 50 сделок (или все, если меньше)
        recent_history = history_data[:50]

        # Переворачиваем массив, чтобы последние сделки были справа
        recent_history.reverse()

        # Создаем массивы для осей
        x_values = list(range(1, len(recent_history) + 1))
        y_values = [deal[1] for deal in recent_history]  # Цены сделок

        # Строим график
        line, = ax.plot(x_values, y_values, 'o-', markersize=6, linewidth=1, markerfacecolor='blue',
                        markeredgecolor='blue')
        ax.set_xlabel('Номер сделки (1 - самая старая из отображаемых)')
        ax.set_ylabel('Цена (RUB)')
        ax.set_title(f'История продаж: {skin_name}')
        ax.grid(True, alpha=0.3)

        # Создаем аннотацию для отображения цены при наведении
        annotation = ax.annotate('', xy=(0, 0), xytext=(10, 10), textcoords='offset points',
                                 bbox=dict(boxstyle='round,pad=0.3', fc='yellow', alpha=0.7),
                                 arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0'))
        annotation.set_visible(False)

        # Функция для обновления аннотации
        def update_annotation(ind):
            index = ind["ind"][0]
            x, y = line.get_data()
            annotation.xy = (x[index], y[index])
            annotation.set_text(f'Цена: {y[index]:.2f} RUB')
            annotation.get_bbox_patch().set_alpha(0.7)

        # Функция для обработки события наведения
        def on_hover(event):
            if event.inaxes == ax:
                cont, ind = line.contains(event)
                if cont:
                    update_annotation(ind)
                    annotation.set_visible(True)
                    fig.canvas.draw_idle()
                else:
                    if annotation.get_visible():
                        annotation.set_visible(False)
                        fig.canvas.draw_idle()

        # Подключаем обработчик событий
        fig.canvas.mpl_connect('motion_notify_event', on_hover)

        # Поворачиваем подписи оси X для лучшей читаемости
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

        # Встраиваем график в tkinter
        canvas = FigureCanvasTkAgg(fig, master=graph_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Добавляем кнопку закрытия
        close_button = tk.Button(graph_window, text="Закрыть", command=graph_window.destroy)
        close_button.pack(pady=5)

    def get_sales_history(self, item_name):
        """Получение истории продаж для предмета"""
        try:
            if not API_KEY or API_KEY == "YOUR_API_KEY_HERE":
                print("API ключ не установлен")
                return []

            # Кодируем имя предмета для URL
            encoded_name = urllib.parse.quote(item_name)

            # Формируем URL для запроса
            url = (
                f"https://market.csgo.com/api/v2/get-list-items-info"
                f"?key={API_KEY}&list_hash_name[]={encoded_name}"
            )

            # Выполняем запрос
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            # Парсим JSON ответ
            data = response.json()

            # Проверяем успешность запроса
            if not data.get('success', False):
                print(f"Ошибка API: {data.get('error', 'Неизвестная ошибка')}")
                return []

            # Извлекаем историю продаж
            item_data = data.get('data', {}).get(item_name, {})
            history = item_data.get('history', [])

            # history - это массив массивов вида [timestamp, price]
            # Сортируем по убыванию времени (новые сделки первыми)
            history.sort(key=lambda x: x[0], reverse=True)

            return history

        except requests.exceptions.RequestException as e:
            print(f"Ошибка сети при запросе истории: {e}")
            return []
        except Exception as e:
            print(f"Ошибка при получении истории продаж: {e}")
            return []

    def open_add_window(self):
        self.add_window = tk.Toplevel(self.root)
        self.add_window.title("Добавить покупку")
        self.add_window.geometry("400x550")
        self.add_window.resizable(False, False)

        # Переменные
        self.name_var = tk.StringVar()
        self.quantity_var = tk.StringVar(value="1")
        self.price_var = tk.StringVar()
        self.commission_var = tk.BooleanVar(value=True)
        self.buy_date_var = tk.StringVar(value=datetime.now().strftime("%d.%m.%Y"))
        self.days_var = tk.StringVar(value="7")

        # Поля ввода
        tk.Label(self.add_window, text="Название скина:").pack(pady=5)
        tk.Entry(self.add_window, textvariable=self.name_var, width=40).pack(pady=5)

        tk.Label(self.add_window, text="Количество:").pack(pady=5)
        tk.Entry(self.add_window, textvariable=self.quantity_var, width=40).pack(pady=5)

        tk.Label(self.add_window, text="Цена за штуку:").pack(pady=5)
        tk.Entry(self.add_window, textvariable=self.price_var, width=40).pack(pady=5)

        tk.Checkbutton(self.add_window, text="Учитывать комиссию Lis (2%)",
                       variable=self.commission_var).pack(pady=5)

        tk.Label(self.add_window, text="Дата покупки (дд.мм.гггг):").pack(pady=5)
        tk.Entry(self.add_window, textvariable=self.buy_date_var, width=40).pack(pady=5)

        tk.Label(self.add_window, text="Дней до разблокировки:").pack(pady=5)
        tk.Entry(self.add_window, textvariable=self.days_var, width=40).pack(pady=5)

        # Информация о цене продажи
        self.sell_info_label = tk.Label(self.add_window, text="", fg="blue", justify=tk.LEFT)
        self.sell_info_label.pack(pady=10)

        # Кнопки
        button_frame = tk.Frame(self.add_window)
        button_frame.pack(pady=20)

        tk.Button(button_frame, text="Рассчитать", command=self.calculate_sell_price,
                  bg="#FF9800", fg="white").pack(side=tk.LEFT, padx=5)

        tk.Button(button_frame, text="Сохранить", command=self.save_purchase,
                  bg="#4CAF50", fg="white").pack(side=tk.LEFT, padx=5)

        tk.Button(button_frame, text="Отмена", command=self.add_window.destroy,
                  bg="#f44336", fg="white").pack(side=tk.LEFT, padx=5)

        # Обновление информации при изменении цены
        self.price_var.trace('w', self.on_price_change)
        self.quantity_var.trace('w', self.on_price_change)
        self.commission_var.trace('w', self.on_price_change)

    def on_price_change(self, *args):
        self.calculate_sell_price()

    def calculate_sell_price(self):
        try:
            quantity = int(self.quantity_var.get() or 0)
            price = float(self.price_var.get() or 0)
            with_commission = self.commission_var.get()

            if quantity <= 0 or price <= 0:
                self.sell_info_label.config(text="Введите корректные данные")
                return

            # Рассчитываем общую стоимость покупки
            total_buy = quantity * price
            if with_commission:
                total_buy *= 1.02  # Добавляем 2% комиссии

            # Рассчитываем цену продажи для возврата первоначальной суммы (без профита)
            # Нужно: (цена_продажи * количество) * 0.9 = total_buy
            # цена_продажи = total_buy / (количество * 0.9)
            break_even_price_per_item = total_buy / (quantity * 0.9)  # Учитываем 10% комиссию при продаже

            # Рассчитываем цену продажи для получения 10% профита
            # Нужно: (цена_продажи * количество) * 0.9 = total_buy * 1.1
            # цена_продажи = (total_buy * 1.1) / (количество * 0.9)
            target_total = total_buy * 1.10  # +10% профит
            target_sell_price_per_item = target_total / (quantity * 0.9)  # Учитываем 10% комиссию при продаже

            self.sell_info_label.config(
                text=f"Общая стоимость покупки: {total_buy:.2f} руб.\n"
                     f"Цена продажи для возврата затрат: {break_even_price_per_item:.2f} руб./шт\n"
                     f"Цена продажи для +10% профита: {target_sell_price_per_item:.2f} руб./шт\n"
                     f"Общая цена продажи (возврат): {break_even_price_per_item * quantity:.2f} руб.\n"
                     f"Общая цена продажи (+10%): {target_sell_price_per_item * quantity:.2f} руб."
            )
        except ValueError:
            self.sell_info_label.config(text="Введите корректные данные")
        except Exception as e:
            self.sell_info_label.config(text=f"Ошибка расчета: {str(e)}")

    def save_purchase(self):
        try:
            name = self.name_var.get().strip()
            quantity = int(self.quantity_var.get())
            price = float(self.price_var.get())
            buy_date = self.buy_date_var.get()
            days = int(self.days_var.get())
            with_commission = self.commission_var.get()

            if not name:
                messagebox.showerror("Ошибка", "Введите название скина")
                return

            # Проверка даты
            try:
                datetime.strptime(buy_date, "%d.%m.%Y")
            except ValueError:
                messagebox.showerror("Ошибка", "Введите корректную дату (дд.мм.гггг)")
                return

            # Рассчитываем дату разблокировки
            buy_datetime = datetime.strptime(buy_date, "%d.%m.%Y")
            unlock_datetime = buy_datetime + timedelta(days=days)
            unlock_date = unlock_datetime.strftime("%d.%m.%Y")

            # Рассчитываем общую стоимость
            total_buy = quantity * price
            if with_commission:
                total_buy *= 1.02

            # Сохраняем в базу данных
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO skins (name, quantity, buy_price, total_buy_price, 
                                 buy_date, unlock_date, with_commission)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (name, quantity, price, total_buy, buy_date, unlock_date, with_commission))

            self.conn.commit()
            self.add_window.destroy()
            self.refresh_all()
            messagebox.showinfo("Успех", "Покупка успешно добавлена")

        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректные данные")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Произошла ошибка: {str(e)}")

    def open_sell_window(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Предупреждение", "Выберите скин для продажи")
            return

        item = self.tree.item(selected[0])
        values = item['values']
        skin_id = values[0]

        self.sell_window = tk.Toplevel(self.root)
        self.sell_window.title("Продать скин")
        self.sell_window.geometry("350x350")
        self.sell_window.resizable(False, False)

        # Переменные
        self.sell_price_var = tk.StringVar()
        self.sell_date_var = tk.StringVar(value=datetime.now().strftime("%d.%m.%Y"))
        self.sell_commission_var = tk.BooleanVar(value=True)

        tk.Label(self.sell_window, text=f"Скин: {values[1]}").pack(pady=5)
        tk.Label(self.sell_window, text=f"Количество: {values[2]}").pack(pady=2)
        tk.Label(self.sell_window, text=f"Цена покупки: {values[4]} руб.").pack(pady=2)

        tk.Label(self.sell_window, text="Цена продажи за штуку:").pack(pady=5)
        tk.Entry(self.sell_window, textvariable=self.sell_price_var, width=30).pack(pady=2)

        tk.Label(self.sell_window, text="Дата продажи (дд.мм.гггг):").pack(pady=5)
        tk.Entry(self.sell_window, textvariable=self.sell_date_var, width=30).pack(pady=2)

        tk.Checkbutton(self.sell_window, text="Учитывать комиссию продажи (10%)",
                       variable=self.sell_commission_var).pack(pady=5)

        # Информация о профите
        self.profit_info_label = tk.Label(self.sell_window, text="", fg="blue")
        self.profit_info_label.pack(pady=5)

        # Обновление информации при изменении цены
        self.sell_price_var.trace('w', self.on_sell_price_change)

        button_frame = tk.Frame(self.sell_window)
        button_frame.pack(pady=15)

        tk.Button(button_frame, text="Рассчитать", command=self.calculate_profit,
                  bg="#FF9800", fg="white").pack(side=tk.LEFT, padx=5)

        tk.Button(button_frame, text="Продать", command=lambda: self.sell_skin(skin_id),
                  bg="#4CAF50", fg="white").pack(side=tk.LEFT, padx=5)

        tk.Button(button_frame, text="Отмена", command=self.sell_window.destroy,
                  bg="#f44336", fg="white").pack(side=tk.LEFT, padx=5)

    def on_sell_price_change(self, *args):
        self.calculate_profit()

    def calculate_profit(self):
        try:
            selected = self.tree.selection()
            if not selected:
                return

            item = self.tree.item(selected[0])
            values = item['values']

            sell_price = float(self.sell_price_var.get() or 0)
            quantity = int(values[2])
            total_buy_price = float(str(values[4]).replace(',', '.'))
            with_sell_commission = self.sell_commission_var.get()

            if sell_price <= 0:
                return

            # Рассчитываем профит
            total_sell_price = sell_price * quantity
            if with_sell_commission:
                total_sell_price *= 0.9  # Вычитаем 10% комиссию

            profit = total_sell_price - total_buy_price
            profit_percent = (profit / total_buy_price * 100) if total_buy_price > 0 else 0

            self.profit_info_label.config(
                text=f"Общая сумма продажи: {sell_price * quantity:.2f} руб.\n"
                     f"После комиссии: {total_sell_price:.2f} руб.\n"
                     f"Профит: {profit:.2f} руб. ({profit_percent:.1f}%)"
            )
        except ValueError:
            self.profit_info_label.config(text="Введите корректную цену")
        except Exception as e:
            self.profit_info_label.config(text=f"Ошибка расчета")

    def sell_skin(self, skin_id):
        try:
            sell_price = float(self.sell_price_var.get())
            sell_date = self.sell_date_var.get()
            with_sell_commission = self.sell_commission_var.get()

            # Проверка даты
            try:
                datetime.strptime(sell_date, "%d.%m.%Y")
            except ValueError:
                messagebox.showerror("Ошибка", "Введите корректную дату (дд.мм.гггг)")
                return

            # Получаем информацию о скине
            cursor = self.conn.cursor()
            cursor.execute('SELECT * FROM skins WHERE id = ?', (skin_id,))
            skin = cursor.fetchone()

            if not skin:
                return

            quantity = skin[2]
            total_buy_price = skin[4]

            # Рассчитываем профит
            total_sell_price = sell_price * quantity
            if with_sell_commission:
                total_sell_price *= 0.9  # Вычитаем 10% комиссию

            profit = total_sell_price - total_buy_price
            profit_percent = (profit / total_buy_price * 100) if total_buy_price > 0 else 0

            # Обновляем запись
            cursor.execute('''
                UPDATE skins 
                SET sell_date = ?, sell_price = ?, profit = ?, profit_percent = ?, with_sell_commission = ?
                WHERE id = ?
            ''', (sell_date, sell_price, profit, profit_percent, with_sell_commission, skin_id))

            self.conn.commit()
            self.sell_window.destroy()
            self.refresh_all()
            messagebox.showinfo("Успех", "Информация о продаже сохранена")

        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректную цену")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Произошла ошибка: {str(e)}")

    def delete_item(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Предупреждение", "Выберите скин для удаления")
            return

        if messagebox.askyesno("Подтверждение", "Вы уверены, что хотите удалить этот скин?"):
            item = self.tree.item(selected[0])
            skin_id = item['values'][0]

            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM skins WHERE id = ?', (skin_id,))
            self.conn.commit()
            self.refresh_all()
            messagebox.showinfo("Успех", "Скин удален")

    def get_skin_status(self, buy_date, unlock_date, sell_date):
        """Определяет статус скина"""
        if sell_date and sell_date != "-":
            return "Продан"

        # Проверяем дату разблокировки
        try:
            unlock_datetime = datetime.strptime(unlock_date, "%d.%m.%Y")
            current_datetime = datetime.now()

            if current_datetime < unlock_datetime:
                return "Ждет разблокировки"
            else:
                return "Доступен для продажи"
        except:
            return "Неизвестно"

    def update_market_prices(self):
        """Обновление цен с маркета (и buy orders, и sell orders, и истории продаж)"""
        try:
            self.market_button.config(state='disabled', text='Обновление...')
            self.root.update()

            # URL для получения цен на покупку (buy orders)
            buy_orders_url = "https://market.csgo.com/api/v2/prices/orders/RUB.json"
            # URL для получения цен на продажу (sell orders)
            sell_orders_url = "https://market.csgo.com/api/v2/prices/RUB.json"

            # Загружаем данные о buy orders (цены на покупку предметов)
            buy_response = requests.get(buy_orders_url, timeout=30)
            buy_response.raise_for_status()
            buy_orders_data = buy_response.json()

            # Загружаем данные о sell orders (цены на продажу предметов)
            sell_response = requests.get(sell_orders_url, timeout=30)
            sell_response.raise_for_status()
            sell_orders_data = sell_response.json()

            # Создаем словари цен
            self.buy_order_prices = {}
            for item in buy_orders_data.get("items", []):
                name = item.get("market_hash_name", "")
                price = item.get("price", 0)
                if name and price:
                    self.buy_order_prices[name] = float(price)

            self.sell_order_prices = {}
            for item in sell_orders_data.get("items", []):
                name = item.get("market_hash_name", "")
                price = item.get("price", 0)
                if name and price:
                    self.sell_order_prices[name] = float(price) * COEFFICIENT_BUY_ORDER

            # Получаем уникальные названия скинов из базы данных для загрузки их истории
            cursor = self.conn.cursor()
            cursor.execute("SELECT DISTINCT name FROM skins")
            skin_names = [row[0] for row in cursor.fetchall()]

            # Загружаем историю продаж для каждого скина
            self.sales_history = {}
            for skin_name in skin_names:
                history = self.get_sales_history(skin_name)
                self.sales_history[skin_name] = history
                # Добавим небольшую задержку, чтобы не перегружать API
                # time.sleep(0.1)  # Можно раскомментировать при необходимости

            # Обновляем таблицу
            self.load_data()

            messagebox.showinfo("Успех",
                                f"Цены с маркета обновлены.\n"
                                f"Buy orders: {len(self.buy_order_prices)} позиций\n"
                                f"Sell orders: {len(self.sell_order_prices)} позиций\n"
                                f"История продаж: {len(self.sales_history)} позиций")

        except requests.exceptions.RequestException as e:
            messagebox.showerror("Ошибка", f"Ошибка сети при загрузке данных: {e}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка при обновлении цен: {e}")
        finally:
            self.market_button.config(state='normal', text='Обновить цены с маркета')

    def load_data(self):
        # Очищаем таблицу
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Загружаем данные из базы с учетом фильтра
        cursor = self.conn.cursor()

        query = '''
            SELECT id, name, quantity, buy_price, total_buy_price, 
                   buy_date, unlock_date, sell_date, sell_price, profit, profit_percent
            FROM skins ORDER BY buy_date DESC
        '''

        cursor.execute(query)
        all_data = cursor.fetchall()

        # Применяем фильтр по статусу
        status_filter = self.status_filter_var.get()
        filtered_data = []

        for row in all_data:
            status = self.get_skin_status(row[5], row[6], row[7])
            if status_filter == "Все" or status == status_filter:
                filtered_data.append(row)

        for row in filtered_data:
            # Определяем статус
            status = self.get_skin_status(row[5], row[6], row[7])

            if row[7]:  # Если есть дата продажи
                profit_text = f"{row[9]:.2f} ({row[10]:.1f}%)"
            else:
                profit_text = "-"

            # Получаем цены с маркета
            buy_order_price = self.buy_order_prices.get(row[1], 0)
            sell_order_price = self.sell_order_prices.get(row[1], 0)

            buy_order_text = f"{buy_order_price:.2f}" if buy_order_price > 0 else "-"
            sell_order_text = f"{sell_order_price:.2f}" if sell_order_price > 0 else "-"

            # Получаем среднюю цену из истории продаж (последние 10 сделок)
            history_data = self.sales_history.get(row[1], [])
            if history_data:
                # Берем последние 10 сделок или все, если меньше
                last_deals = history_data[:10]
                avg_price = sum(deal[1] for deal in last_deals) / len(last_deals)
                history_text = f"{avg_price:.2f}"
            else:
                history_text = "-"

            # Форматируем данные для отображения
            values = (
                row[0],  # id
                row[1],  # name
                row[2],  # quantity
                f"{row[3]:.2f}",  # buy_price
                f"{row[4]:.2f}",  # total_buy_price
                row[5],  # buy_date
                row[6],  # unlock_date
                row[7] if row[7] else "-",  # sell_date
                f"{row[8]:.2f}" if row[8] else "-",  # sell_price
                profit_text,  # profit
                status,  # status
                buy_order_text,  # order_buy
                sell_order_text,  # order_sell
                history_text  # history
            )

            # Определяем тег для цветового выделения
            if status == "Продан":
                tag = 'sold'
            elif status == "Ждет разблокировки":
                tag = 'locked'
            else:
                tag = 'available'

            self.tree.insert('', tk.END, values=values, tags=(tag,))

        # Настройка цветов
        self.tree.tag_configure('sold', background='#e8f5e8')  # Зеленый - продано
        self.tree.tag_configure('locked', background='#ffebee')  # Красный - заблокировано
        self.tree.tag_configure('available', background='#fff3e0')  # Оранжевый - доступно

    def load_summary(self):
        """Загрузка статистики с учетом выбранного периода"""
        try:
            start_date = self.start_date_var.get()
            end_date = self.end_date_var.get()

            # Проверка формата дат
            datetime.strptime(start_date, "%d.%m.%Y")
            datetime.strptime(end_date, "%d.%m.%Y")

            cursor = self.conn.cursor()

            # Общий профит за период (только проданные скины)
            cursor.execute("""
                SELECT SUM(profit) FROM skins 
                WHERE profit IS NOT NULL 
                AND buy_date >= ? AND buy_date <= ?
            """, (start_date, end_date))
            total_profit = cursor.fetchone()[0] or 0

            # Общая сумма покупок за период
            cursor.execute("""
                SELECT SUM(total_buy_price) FROM skins 
                WHERE buy_date >= ? AND buy_date <= ?
            """, (start_date, end_date))
            total_spent = cursor.fetchone()[0] or 0

            # Количество скинов по статусам за период
            cursor.execute("""
                SELECT COUNT(*) FROM skins 
                WHERE sell_date IS NOT NULL 
                AND buy_date >= ? AND buy_date <= ?
            """, (start_date, end_date))
            sold_count = cursor.fetchone()[0] or 0

            cursor.execute("""
                SELECT COUNT(*) FROM skins 
                WHERE sell_date IS NULL 
                AND buy_date >= ? AND buy_date <= ?
            """, (start_date, end_date))
            unsold_count = cursor.fetchone()[0] or 0

            # Количество скинов в ожидании разблокировки за период
            cursor.execute("""
                SELECT COUNT(*) FROM skins 
                WHERE sell_date IS NULL 
                AND unlock_date > ? 
                AND buy_date >= ? AND buy_date <= ?
            """, (datetime.now().strftime("%d.%m.%Y"), start_date, end_date))
            locked_count = cursor.fetchone()[0] or 0

            # Количество доступных для продажи за период
            cursor.execute("""
                SELECT COUNT(*) FROM skins 
                WHERE sell_date IS NULL 
                AND unlock_date <= ? 
                AND buy_date >= ? AND buy_date <= ?
            """, (datetime.now().strftime("%d.%m.%Y"), start_date, end_date))
            available_count = cursor.fetchone()[0] or 0

            # Сумма вложенных денег (доступные для продажи + ждущие разблокировки)
            cursor.execute("""
                SELECT SUM(total_buy_price) FROM skins 
                WHERE sell_date IS NULL 
                AND buy_date >= ? AND buy_date <= ?
            """, (start_date, end_date))
            invested_money = cursor.fetchone()[0] or 0

            # Сумма денег в ждущих разблокировки
            cursor.execute("""
                SELECT SUM(total_buy_price) FROM skins 
                WHERE sell_date IS NULL 
                AND unlock_date > ? 
                AND buy_date >= ? AND buy_date <= ?
            """, (datetime.now().strftime("%d.%m.%Y"), start_date, end_date))
            locked_money = cursor.fetchone()[0] or 0

            # Сумма денег доступных для продажи
            cursor.execute("""
                SELECT SUM(total_buy_price) FROM skins 
                WHERE sell_date IS NULL 
                AND unlock_date <= ? 
                AND buy_date >= ? AND buy_date <= ?
            """, (datetime.now().strftime("%d.%m.%Y"), start_date, end_date))
            available_money = cursor.fetchone()[0] or 0

            summary_text = f"📊 СТАТИСТИКА за период {start_date} - {end_date}:\n"
            summary_text += f"Общий профит: {total_profit:.2f} руб.\n"
            summary_text += f"Всего потрачено: {total_spent:.2f} руб.\n"
            summary_text += f"Вложено сейчас: {invested_money:.2f} руб. (Ждут: {locked_money:.2f} руб. | Доступно: {available_money:.2f} руб.)\n"
            summary_text += f"Продано скинов: {sold_count} | В наличии: {unsold_count}\n"
            summary_text += f"Ждут разблокировки: {locked_count} | Доступны для продажи: {available_count}"

            self.summary_label.config(text=summary_text)

        except ValueError:
            self.summary_label.config(text="❌ Ошибка: Введите корректные даты (дд.мм.гггг)")
        except Exception as e:
            self.summary_label.config(text=f"❌ Ошибка при загрузке статистики: {str(e)}")


if __name__ == "__main__":
    root = tk.Tk()
    app = SkinTracker(root)
    root.mainloop()
