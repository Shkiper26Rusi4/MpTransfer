import json
from pathlib import Path
import shutil
import sys
import time
import traceback
import logging
from logging.handlers import RotatingFileHandler
import re
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QLockFile
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QListWidget, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QDialog, QDialogButtonBox, QFormLayout,
    QComboBox, QLineEdit, QProgressBar, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QScrollArea)
from .bridge import Bridge, data_dir
from .storage import Store
from .importing import LABELS, read_table, guess, products_from_rows
from .engine import AvitoAdapter, TransferQueue
from .photos import match_files, unpack_zip, EXTENSIONS, cache_photo
from .pacing import seconds_to_hourly, hourly_to_seconds
from . import __version__
from .update_ui import UpdateControls

STATUS = {"unchecked": "Не проверен", "invalid": "Ошибка данных", "exists": "Уже существует",
          "missing": "Отсутствует", "review": "Требует проверки", "creating": "Создаётся",
          "created": "Черновик создан", "unknown": "Результат неизвестен", "error": "Ошибка"}


class Worker(QThread):
    result = Signal(object)
    failure = Signal(str)
    progress = Signal(object)

    def __init__(self, action):
        super().__init__()
        self.action = action

    def run(self):
        try:
            self.result.emit(self.action())
        except Exception as exc:
            self.failure.emit(str(exc))


class MappingDialog(QDialog):
    def __init__(self, headers, mapping, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сопоставление столбцов — проверьте поля")
        self.resize(640, 700)
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("Неизвестные столбцы сохраняются как дополнительные характеристики."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        form = QFormLayout(widget)
        self.combos = {}
        for field, label in LABELS.items():
            combo = QComboBox()
            combo.addItem("Не указан", -1)
            for i, header in enumerate(headers):
                combo.addItem(f"{i+1}. {header}", i)
            combo.setCurrentIndex(mapping.get(field, -1)+1)
            form.addRow(label, combo)
            self.combos[field] = combo
        scroll.setWidget(widget)
        outer.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Импортировать")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def mapping(self):
        return {k: c.currentData() for k,c in self.combos.items() if c.currentData() >= 0}

    def validate(self):
        mapping = self.mapping()
        if "title" not in mapping or len(set(mapping.values())) != len(mapping):
            QMessageBox.warning(self, "Сопоставление", "Укажите название. Один столбец нельзя назначить двум полям.")
            return
        self.accept()


def button(text, action, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    b.clicked.connect(action)
    return b


class Window(UpdateControls,QMainWindow):
    def __init__(self, directory=None):
        super().__init__()
        self.directory = Path(directory or data_dir())
        self.store = Store(self.directory / "transfer.sqlite3")
        self.bridge = Bridge(self.directory)
        self.bridge.operation_timeout=self.store.setting('timeout',30)
        self.bridge.read_retries=self.store.setting('read_retries',2)
        self.adapter = AvitoAdapter(self.bridge,self.store)
        self.worker = None
        self.queue = None
        self.auto_diagnosed = False
        self.account_verified = False
        self.started = time.monotonic()
        self.setWindowTitle("Marketplace Transfer")
        screen=QApplication.primaryScreen().availableGeometry()
        self.resize(min(1420,int(screen.width()*.96)),min(920,int(screen.height()*.94)))
        self.setMinimumSize(min(1000,int(screen.width()*.9)),min(680,int(screen.height()*.9)))
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        logo = QLabel("MARKETPLACE\nTRANSFER")
        logo.setObjectName("logo")
        side.addWidget(logo)
        self.navigation = QListWidget()
        self.navigation.addItems(["Главная", "Товары", "Проверка", "Перенос", "Фотографии", "История", "Логи", "Настройки"])
        side.addWidget(self.navigation)
        side.addWidget(QLabel(f"Ozon Excel → Авито\nВерсия {__version__} · черновики"))
        layout.addWidget(sidebar)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.home = self.page("Обзор переноса", "Товары, проверка и черновики — в одном рабочем пространстве")
        self.source = QLabel()
        self.home.addWidget(self.source)
        self.connection = QLabel("Chrome: нет подключения · Авторизация: не проверена")
        self.home.addWidget(self.connection)
        actions = QHBoxLayout()
        actions.addWidget(button("Выбрать Excel", self.choose_excel, True))
        actions.addWidget(button("Диагностика Chrome", self.diagnose))
        self.home.addLayout(actions)
        stats = QHBoxLayout()
        self.cards = {}
        for key, label in [("total", "Всего"), ("exists", "Существуют"), ("missing", "Отсутствуют"), ("created", "Черновики"), ("error", "Ошибки"), ("ready", "Готовы")]:
            card = QLabel(f"0\n{label}")
            card.setObjectName("card")
            card.setAlignment(Qt.AlignCenter)
            stats.addWidget(card)
            self.cards[key] = (card, label)
        self.home.addLayout(stats)
        self.notice = QLabel("Массовый перенос закрыт до проверки настоящего черновика на Авито.\nДиагностика страницы не подтверждает полноту списка объявлений.")
        self.notice.setWordWrap(True)
        self.notice.setObjectName("notice")
        self.home.addWidget(self.notice)
        self.home.addWidget(button("ПРОВЕРИТЬ ТОВАРЫ", self.check_all, True))
        self.home.addWidget(button("НАЧАТЬ ПЕРЕНОС", self.start_transfer, True))
        self.home.addStretch()

        products = self.page("Товары", "Исходные значения сохраняются без выдуманных характеристик")
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию, артикулу или SKU")
        self.search.textChanged.connect(self.refresh)
        self.filter = QComboBox()
        self.filter.addItem("Все", "")
        for key, label in STATUS.items():
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.filter)
        toolbar.addWidget(button("Выбрать Excel", self.choose_excel))
        toolbar.addWidget(button('Править данные',self.edit_selected_product))
        products.addLayout(toolbar)
        self.table = self.make_table(["ID", "Название", "Артикул", "SKU", "Цена", "Статус", "Фото", "Причина / ссылка"])
        self.table.cellDoubleClicked.connect(self.open_ad)
        products.addWidget(self.table)
        products.addWidget(button("Экспорт отчёта в Excel", self.export_report))

        check = self.page("Проверка товаров", "Только полный просмотр доступных разделов позволяет подтвердить отсутствие")
        check.addWidget(button("Проверить все товары", self.check_all, True))
        self.check_summary = QLabel()
        self.check_summary.setWordWrap(True)
        check.addWidget(self.check_summary)
        self.diagnostic = QPlainTextEdit()
        self.diagnostic.setReadOnly(True)
        check.addWidget(self.diagnostic)
        decision_buttons=QHBoxLayout()
        decision_buttons.addWidget(button('Подтвердить совпадение выбранного товара',self.confirm_existing))
        decision_buttons.addWidget(button('Подтвердить, что это разные товары',self.confirm_distinct))
        check.addLayout(decision_buttons)
        check.addWidget(QLabel('Сначала выделите товар в разделе «Товары». Решения сохраняются и учитываются при повторной сверке.'))

        transfer = self.page("Перенос в черновики", "Публикация и платные действия не поддерживаются")
        controls = QHBoxLayout()
        for text, action in [("НАЧАТЬ ПЕРЕНОС", self.start_transfer), ("ПАУЗА", self.pause), ("ПРОДОЛЖИТЬ", self.resume), ("ОСТАНОВИТЬ", self.stop), ("Повторить ошибки", self.retry)]:
            controls.addWidget(button(text, action))
        transfer.addLayout(controls)
        self.current = QLabel("Очередь не запущена")
        self.current.setWordWrap(True)
        transfer.addWidget(self.current)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        transfer.addWidget(self.progress)
        self.metrics = QLabel("Скорость и прогноз появятся после фактической обработки товаров.")
        self.metrics.setWordWrap(True)
        transfer.addWidget(self.metrics)
        transfer.addStretch()

        photos = self.page("Фотографии", "Оригинальные файлы · точное сопоставление по артикулу или SKU")
        photo_actions = QHBoxLayout()
        photo_actions.addWidget(button("Добавить файлы", self.photo_files))
        photo_actions.addWidget(button("Добавить папку", self.photo_folder))
        photo_actions.addWidget(button("Добавить ZIP", self.photo_zip))
        photo_actions.addWidget(button("Проверить и скачать фото", self.prepare_photos))
        photos.addLayout(photo_actions)
        self.photo_table = self.make_table(["Товар", "Артикул", "Количество", "Источники"])
        self.photo_table.cellDoubleClicked.connect(lambda row,column:self.edit_photo_sources())
        photos.addWidget(self.photo_table)
        photos.addWidget(button('Изменить список и порядок фотографий',self.edit_photo_sources))
        photos.addWidget(button("Заменить фото выбранного черновика", self.update_draft_photos))

        history = self.page("История", "Состояния и результаты операций сохраняются в SQLite")
        self.history_session=QComboBox()
        self.history_session.addItem('Все сеансы','')
        self.history_session.currentIndexChanged.connect(self.refresh)
        history.addWidget(self.history_session)
        self.history_summary=QLabel()
        self.history_summary.setWordWrap(True)
        history.addWidget(self.history_summary)
        history_tools=QHBoxLayout()
        self.history_search=QLineEdit()
        self.history_search.setPlaceholderText('Поиск по названию или артикулу')
        self.history_search.textChanged.connect(self.refresh)
        self.history_filter=QComboBox()
        self.history_filter.addItem('Все','')
        for key,label in STATUS.items():self.history_filter.addItem(label,key)
        self.history_filter.currentIndexChanged.connect(self.refresh)
        history_tools.addWidget(self.history_search)
        history_tools.addWidget(self.history_filter)
        history.addLayout(history_tools)
        self.history_table = self.make_table(["Время UTC", "Товар", "Артикул", "Результат", "Причина", "Авито ID", "Ссылка", "Фото", "Файл Excel"])
        history.addWidget(self.history_table)
        history.addWidget(button('Сбросить фильтры истории',self.reset_history_filters))
        history.addWidget(button("Экспорт истории в Excel", self.export_history_report))
        logs = self.page("Логи", "Технические сообщения без паролей, cookies и токенов")
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(3000)
        log_directory=self.directory/'logs'
        log_directory.mkdir(exist_ok=True)
        self.log_path=log_directory/'application.log'
        self.logger=logging.Logger('MarketplaceTransfer')
        self.log_handler=RotatingFileHandler(self.log_path,maxBytes=2*1024*1024,backupCount=3,encoding='utf-8')
        self.log_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        self.logger.addHandler(self.log_handler)
        logs.addWidget(self.logs)
        logs.addWidget(button("Экспорт технических логов", self.export_logs))
        settings = self.page("Настройки", "Данные сохраняются отдельно от файлов программы")
        form = QFormLayout()
        self.theme = QComboBox()
        self.theme.addItems(["Тёмная", "Светлая"])
        self.theme.setCurrentIndex(self.store.setting("theme", 0))
        form.addRow("Тема", self.theme)
        form.addRow("Папка данных", QLabel(str(self.directory)))
        form.addRow("Браузер", QLabel("Google Chrome · Native Messaging"))
        form.addRow("Маршрут", QLabel("Ozon Excel → черновики Авито"))
        self.photo_path = QLineEdit(self.store.setting("photo_folder", ""))
        form.addRow("Папка фотографий", self.photo_path)
        self.photo_policy=QComboBox()
        self.photo_policy.addItem('Остановить товар при ошибке фото','strict')
        self.photo_policy.addItem('Удалить незагруженные, сохранить с остальными','keep_loaded')
        self.photo_policy.setCurrentIndex(1 if self.store.setting('photo_failure_policy')=='keep_loaded' else 0)
        form.addRow('При ошибке фотографии',self.photo_policy)
        form.addRow('Лимит Авито',QLabel('Первые 10 фотографий; исходный набор сохраняется полностью'))
        self.interval = QSpinBox()
        self.interval.setRange(1, 300)
        self.interval.setValue(self.store.setting("interval", 3))
        form.addRow("Минимальный интервал, сек.", self.interval)
        self.target_cycle=QDoubleSpinBox()
        self.target_cycle.setDecimals(2)
        self.target_cycle.setRange(0,3600)
        self.target_cycle.setKeyboardTracking(False)
        self.target_cycle.setSpecialValueText('Максимальная доступная скорость')
        self.target_cycle.setValue(self.store.setting('target_cycle_seconds',0))
        self.target_cycle.setToolTip('Время между началами товаров, включая создание и проверку. Например, 120 секунд = около 30 товаров в час. Изменение применяется после сохранения текущего товара.')
        form.addRow('Секунд на карточку',self.target_cycle)
        self.target_hourly=QDoubleSpinBox()
        self.target_hourly.setDecimals(2)
        self.target_hourly.setRange(0,3600)
        self.target_hourly.setKeyboardTracking(False)
        self.target_hourly.setSpecialValueText('Максимальная доступная скорость')
        self.target_hourly.setValue(seconds_to_hourly(self.target_cycle.value()))
        self.target_hourly.setToolTip('Связано с полем секунд: 3600 / секунд на карточку. Ноль отключает плановое ограничение. Фактическая скорость зависит от сайта.')
        form.addRow('Карточек в час',self.target_hourly)
        self.pace_explanation=QLabel()
        self.pace_explanation.setWordWrap(True)
        form.addRow(self.pace_explanation)
        self.adaptive_pacing=QCheckBox('Автоматически ускоряться и замедляться')
        self.adaptive_pacing.setChecked(self.store.setting('adaptive_pacing_enabled',False))
        form.addRow('Адаптивный темп',self.adaptive_pacing)
        self.adaptive_min=QSpinBox()
        self.adaptive_min.setRange(17,3600)
        self.adaptive_min.setValue(self.store.setting('adaptive_min_seconds',17))
        form.addRow('Самый быстрый цикл, сек.',self.adaptive_min)
        self.adaptive_max=QSpinBox()
        self.adaptive_max.setRange(17,3600)
        self.adaptive_max.setValue(self.store.setting('adaptive_max_seconds',60))
        form.addRow('Самый медленный цикл, сек.',self.adaptive_max)
        self.adaptive_pacing.setToolTip('После трёх подтверждённых сохранений цикл сокращается на 10 секунд до нижней границы. Временный сбой увеличивает его на 25% (не менее 5 секунд). Три временных сбоя подряд останавливают очередь. Ошибки данных темп не меняют. Фактическое время ограничено скоростью сайта.')
        self.target_cycle.valueChanged.connect(lambda value:self.sync_pace_fields(value,True))
        self.target_hourly.valueChanged.connect(lambda value:self.sync_pace_fields(value,False))
        self.adaptive_pacing.toggled.connect(self.describe_pacing)
        self.adaptive_min.valueChanged.connect(self.describe_pacing)
        self.adaptive_max.valueChanged.connect(self.describe_pacing)
        self.describe_pacing()
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 300)
        self.timeout.setValue(self.store.setting("timeout", 30))
        form.addRow("Тайм-аут диагностики, сек.", self.timeout)
        self.read_retries=QSpinBox()
        self.read_retries.setRange(0,5)
        self.read_retries.setValue(self.store.setting('read_retries',2))
        form.addRow('Повторы чтения при переходе',self.read_retries)
        self.error_policy=QComboBox()
        self.error_policy.addItem('Продолжать безопасные товары','continue')
        self.error_policy.addItem('Остановить очередь','stop')
        self.error_policy.setCurrentIndex(1 if self.store.setting('error_policy')=='stop' else 0)
        form.addRow('При ошибке товара',self.error_policy)
        form.addRow("Восстановление", QLabel("История сохраняется всегда. Неизвестный результат требует сверки."))
        self.extension_id = QLineEdit(self.store.setting("extension_id", ""))
        self.extension_id.setPlaceholderText("ID расширения из chrome://extensions")
        form.addRow("ID расширения Chrome", self.extension_id)
        settings.addLayout(form)
        self.init_update_controls(form,settings)
        settings.addWidget(button('Применить темп очереди',self.apply_pacing))
        settings.addWidget(button("Сохранить настройки", self.save_settings, True))
        settings.addWidget(button("Зарегистрировать Native Messaging", self.register_host))
        settings.addWidget(button("Открыть папку расширения", self.open_extension))
        settings.addWidget(button("Резервная копия базы", self.backup))
        settings.addStretch()
        self.navigation.setCurrentRow(0)
        self.apply_theme()
        self.refresh()
        self.restore_transfer_summary()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)

    def page(self, title, subtitle):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel(title)
        heading.setObjectName("heading")
        layout.addWidget(heading)
        sub = QLabel(subtitle)
        sub.setWordWrap(True)
        layout.addWidget(sub)
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        self.pages.addWidget(scroll)
        return layout

    def make_table(self, labels):
        table = QTableWidget(0, len(labels))
        table.setHorizontalHeaderLabels(labels)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(1, 260)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().hide()
        return table

    def populate(self, table, rows):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                table.setItem(i, j, QTableWidgetItem(str('' if value is None else value)))

    def refresh(self):
        rows = self.store.active_rows()
        search = self.search.text().casefold()
        status = self.filter.currentData()
        selected = [r for r in rows if (not status or r["status"] == status) and search in " ".join([self.store.product(r).title, self.store.product(r).article, self.store.product(r).sku]).casefold()]
        self.populate(self.table, [[r["id"], (p := self.store.product(r)).title, p.article, p.sku, p.price, STATUS.get(r["status"], r["status"]), len(p.photos), r["error"] or r["url"]] for r in selected])
        self.populate(self.photo_table, [[(p := self.store.product(r)).title, p.article, len(p.photos), "; ".join(p.photos)] for r in rows])
        self.refresh_history()
        counts = {s: sum(r["status"] == s for r in rows) for s in STATUS}
        counts["total"] = len(rows)
        counts["error"] += counts["invalid"] + counts["unknown"]
        from .drafts import DraftCreator,category_for
        proof=self.store.setting('avito_integration_validation',{})
        counts['ready']=0
        for row in rows:
            if row['status']!='missing':continue
            try:
                product=self.store.product(row)
                DraftCreator.validate(product)
                if category_for(product) in proof.get('categories',[]):counts['ready']+=1
            except (ValueError,RuntimeError):pass
        for key, (card, label) in self.cards.items():
            card.setText(f"{counts[key]}\n{label}")
        self.source.setText("Файл: " + self.store.setting("source_file", "не выбран"))
        self.check_summary.setText(f"Всего: {len(rows)} · Существуют: {counts['exists']} · Отсутствуют: {counts['missing']} · Требуют проверки: {counts['review']} · Не проверены: {counts['unchecked']} · Ошибки: {counts['error']} · Готовы: {counts['ready']}")
        self.notice.setText(f"Подтверждено настоящих тестовых черновиков: {len(proof.get('drafts',[]))}. Проверенных категорий: {len(proof.get('categories',[]))}.\nПеред запуском выполните полную проверку текущего аккаунта. Спорные совпадения требуют решения пользователя.")

    def refresh_history(self):
        revision=self.store.history_revision()
        current=self.history_session.currentData() or ''
        if revision!=getattr(self,'_sessions_revision',None):
            self._sessions_revision=revision
            self._session_records=self.store.sessions()
            blocked=self.history_session.blockSignals(True)
            self.history_session.clear()
            self.history_session.addItem('Все сеансы','')
            kinds={'legacy':'Восстановленная история','transfer':'Перенос','check':'Проверка','photos':'Фотографии','operation':'Операция'}
            for s in self._session_records:
                self.history_session.addItem(f"{s['started_at'][:19].replace('T',' ')} UTC · {kinds.get(s['kind'],s['kind'])} · {Path(s['source_file']).name}",s['id'])
            index=self.history_session.findData(current)
            self.history_session.setCurrentIndex(max(0,index))
            self.history_session.blockSignals(blocked)
        current=self.history_session.currentData() or ''
        signature=(revision,current,self.history_filter.currentData(),self.history_search.text())
        if signature==getattr(self,'_history_signature',None):return
        self._history_signature=signature
        records=self.store.history(current or None)
        displayed=[]
        for record in records:
            data=json.loads(record['data'] or '{}')
            if self.history_filter.currentData() and record['status']!=self.history_filter.currentData():continue
            if self.history_search.text().casefold() not in (data.get('title','')+' '+data.get('article','')).casefold():continue
            displayed.append([record['at'],data.get('title'),data.get('article'),STATUS.get(record['status'],record['status']),record['detail'],record['ad_id'],record['url'],record['photo_count'],record.get('source_file','')])
        self.populate(self.history_table,displayed)
        text=f'Записей в базе: {revision[3]} · В выбранном сеансе: {len(records)} · Показано с учётом фильтров: {len(displayed)}.'
        selected=next((s for s in self._session_records if s['id']==current),None)
        if selected:
            duration=f"{selected['duration_seconds']:.1f} сек." if selected['duration_seconds'] is not None else 'не записывалась в старой версии'
            text+=f"\nExcel: {selected['source_file']}\nВсего: {selected['source_total']} · В очереди: {selected['queued_total']} · Было ранее: {selected['existing_before']} · Найдены существующие: {selected['existing']} · Создано: {selected['created']} · Ошибки: {selected['errors']}\nДлительность: {duration} · {selected['detail']}"
        self.history_summary.setText(text)

    def restore_transfer_summary(self):
        last=next((s for s in self.store.sessions() if s['kind'] in ('transfer','legacy')),None)
        if not last:return
        if last['kind']=='legacy':
            rows=self.store.active_rows()
            completed=sum(row['status'] in ('created','exists') for row in rows)
            self.progress.setValue(int(100*completed/len(rows)) if rows else 0)
            self.current.setText('Сохранённые результаты до обновления · подробности во вкладке «История»')
            self.metrics.setText(f"Черновиков в базе: {sum(row['status']=='created' for row in rows)} · Существуют: {sum(row['status']=='exists' for row in rows)}\nДлительность старых запусков не записывалась.")
            return
        items=self.store.session_items(last['id'])
        processed=sum(bool(r['transfer_result'] or r['check_result']) for r in items)
        total=last['queued_total']
        self.progress.setValue(int(100*processed/total) if total else 0)
        self.current.setText('Последний сеанс: '+last['started_at'][:19].replace('T',' ')+' UTC · '+last['status'])
        self.metrics.setText(f"Готово: {last['created']} · Ошибки: {last['errors']} · Осталось: {max(0,total-processed)}\nДлительность: {last['duration_seconds'] or 0:.1f} сек. · История восстановлена из SQLite.")

    def reset_history_filters(self):
        self.history_search.clear()
        self.history_filter.setCurrentIndex(0)
        self.history_session.setCurrentIndex(0)
        self.refresh_history()

    def export_history_report(self):
        path,_=QFileDialog.getSaveFileName(self,'Экспорт истории','История_MarketplaceTransfer.xlsx','Excel (*.xlsx)')
        if path:
            from .history import export_history
            selected=self.history_session.currentData() or None
            self.launch(lambda:export_history(self.store,path,selected),lambda _:self.log('История экспортирована в Excel'))

    def launch(self, action, on_result=None):
        if self.worker and self.worker.isRunning():
            self.log("Дождитесь завершения текущей операции")
            return
        self.worker = Worker(action)
        self.worker.failure.connect(self.error)
        self.worker.progress.connect(self.on_progress)
        if on_result:
            self.worker.result.connect(on_result)
        self.worker.finished.connect(self.operation_finished)
        self.worker.start()

    def operation_finished(self):
        self.progress.setRange(0,100)
        self.refresh()
        QTimer.singleShot(0,self.try_install_update)

    def log(self, text):
        text=re.sub(r'(https?://[^\s?]+)\?[^\s]+',r'\1?[параметры скрыты]',str(text))
        text=re.sub(r'(?i)(authorization|cookie|password|token)\s*[:=]\s*\S+',r'\1=[скрыто]',text)
        self.logs.appendPlainText(time.strftime("%H:%M:%S") + "  " + text)
        self.logger.info(text)

    def error(self, text):
        self.progress.setRange(0,100)
        self.log(text)
        QMessageBox.warning(self, "Операция не завершена", text)

    def choose_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите таблицу Ozon", "", "Таблицы (*.xlsx *.xls *.csv)")
        if path:
            self.launch(lambda: read_table(path), lambda data: self.map_import(path, data))

    def map_import(self, path, data):
        headers, rows = data
        key = "mapping:" + json.dumps(headers, ensure_ascii=False)
        dialog = MappingDialog(headers, self.store.setting(key, guess(headers)), self)
        if dialog.exec() != QDialog.Accepted:
            return
        mapping = dialog.mapping()
        self.store.set_setting(key, mapping)
        # Wait until the read worker has completed before launching the import worker.
        def perform():
            products = products_from_rows(headers, rows, mapping)
            defaults=self.store.setting('source_defaults:'+str(path),{})
            for product in products:
                if not product.condition:
                    product.condition=defaults.get('condition','')
                if not product.origin_country:
                    product.origin_country=defaults.get('origin_country','')
            self.store.import_products(products, path)
            return f"Импортировано строк: {len(products)}. Уникальных товаров в базе: {len(self.store.rows())}."
        QTimer.singleShot(0, lambda: self.launch(perform, self.log))

    def diagnose(self):
        timeout = self.timeout.value()
        def action():
            tabs = self.bridge.call("tabs", timeout=timeout)
            target = self.directory / "diagnostics"
            target.mkdir(exist_ok=True)
            (target / "tabs.json").write_text(json.dumps(tabs, ensure_ascii=False, indent=2), encoding="utf-8")
            diagnosis = self.bridge.call("diagnose", timeout=timeout)
            (target / "last-page.json").write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")
            return diagnosis
        self.launch(action, self.show_diagnosis)

    def show_diagnosis(self, data):
        self.account_verified = data.get("accountAccessible") is True or data.get('accountVerified') is True
        self.diagnostic.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        self.navigation.setCurrentRow(2)
        self.log("Диагностика получена. Авторизация и полнота списка автоматически не подтверждены.")

    def check_all(self):
        self.progress.setRange(0,0)
        self.launch(lambda: TransferQueue(self.store, self.adapter).check_all(), self.show_check_result)

    def show_check_result(self, snapshot):
        self.account_verified=bool(snapshot.get('sections'))
        self.diagnostic.setPlainText(json.dumps({"complete":snapshot.get("complete"),"sections":snapshot.get("sections"),"reason":snapshot.get("reason")},ensure_ascii=False,indent=2))
        self.navigation.setCurrentRow(2)
        self.log("Проверка завершена полностью" if snapshot.get("complete") else "Проверка неполная: отсутствующие товары не подтверждены")

    def selected_product(self):
        selected=self.table.currentRow()
        if selected<0:
            self.error('Сначала выделите строку в разделе «Товары»')
            return None
        pid=int(self.table.item(selected,0).text())
        return next(row for row in self.store.rows() if row['id']==pid)

    def review_dialog(self, distinct=False):
        from .domain import compare
        from .inventory import product_from_detail
        row=self.selected_product()
        if not row:return
        snapshot=self.store.latest_snapshot()
        if not snapshot or not snapshot.get('complete'):
            self.error('Для решения нужна полная проверка объявлений')
            return
        product=self.store.product(row)
        candidates=[item for item in snapshot.get('items',[]) if compare(product,product_from_detail(item)) in ('review','exists')]
        if not candidates:
            self.error('В последней проверке нет спорных кандидатов для этого товара')
            return
        dialog=QDialog(self)
        dialog.setWindowTitle('Проверка совпадений')
        dialog.resize(900,650)
        outer=QVBoxLayout(dialog)
        outer.addWidget(QLabel(product.title+' · Артикул '+product.article))
        choice=QComboBox()
        for item in candidates:choice.addItem(item['title']+' · ID '+item['id'],item['id'])
        outer.addWidget(choice)
        detail=QPlainTextEdit()
        detail.setReadOnly(True)
        def show_candidate():
            item=next(i for i in candidates if i['id']==choice.currentData())
            source='ИСТОЧНИК\n'+json.dumps({'title':product.title,'article':product.article,'kit':product.kit,'quantity':product.quantity,'dimensions':product.dimensions,'volume':product.volume,'compatibility':product.compatibility,'description':product.description},ensure_ascii=False,indent=2)
            detail.setPlainText(source+'\n\nАВИТО\n'+item['title']+'\n'+item.get('detail',{}).get('pageText',item.get('summary','')))
        choice.currentIndexChanged.connect(show_candidate)
        show_candidate()
        outer.addWidget(detail)
        accept=button('Это отдельный товар — исключить все показанные совпадения' if distinct else 'Связать с выбранным объявлением',dialog.accept,True)
        outer.addWidget(accept)
        outer.addWidget(button('Отмена',dialog.reject))
        if dialog.exec()!=QDialog.Accepted:return
        if distinct:
            self.store.set_setting('match_decision:'+product.key,{'kind':'distinct','candidate_ids':[i['id'] for i in candidates]})
            self.store.transition(row['id'],'missing','Пользователь подтвердил различие со всеми показанными кандидатами')
        else:
            item=next(i for i in candidates if i['id']==choice.currentData())
            self.store.set_setting('match_decision:'+product.key,{'kind':'exists','id':item['id']})
            self.store.transition(row['id'],'exists','Совпадение подтверждено пользователем',ad_id=item['id'],url='https://www.avito.ru'+item['path'])
        self.refresh()

    def confirm_existing(self):self.review_dialog(False)

    def confirm_distinct(self):self.review_dialog(True)

    def start_transfer(self):
        if self.worker and self.worker.isRunning():
            self.log('Очередь уже выполняется')
            return
        self.queue = TransferQueue(self.store, self.adapter, interval=self.interval.value())
        self.progress.setRange(0,0)
        self.current.setText('Проверка текущего аккаунта перед переносом')
        def action():
            self.queue.callback = self.worker.progress.emit
            if not self.adapter.last_inventory or not self.adapter.last_inventory.get('complete'):
                self.queue.check_all()
            return self.queue.run()
        self.launch(action)

    def pause(self):
        if self.queue:
            self.queue.pause()
            self.log("Пауза запрошена после безопасной точки")

    def resume(self):
        if self.queue and self.worker and self.worker.isRunning():
            self.queue.resume()
            self.log("Очередь продолжена")
        else:self.start_transfer()

    def stop(self):
        if self.queue:
            self.queue.stop()
            self.log("Остановка запрошена; текущая операция должна завершиться")

    def retry(self):
        if self.worker and self.worker.isRunning():
            self.log('Дождитесь завершения текущей операции')
            return
        ids=[row['id'] for row in self.store.active_rows() if row['status'] in ('error','unknown')]
        if not ids:
            self.log('Ошибок для повторной обработки нет')
            return
        self.queue=TransferQueue(self.store,self.adapter,interval=self.interval.value(),product_ids=ids)
        def action():
            self.queue.callback=self.worker.progress.emit
            self.queue.check_all()
            self.queue.run()
        self.launch(action)

    def on_progress(self, data):
        self.current.setText(f"{data['title']} · {data['article']}\n{data['stage']}")
        total = data["total"]
        if total:self.progress.setRange(0,100)
        processed = data.get("processed", 0)
        self.progress.setValue(int(100*processed/total) if total else 0)
        elapsed = data.get("elapsed", 0)
        self.metrics.setText(f"Готово: {data['done']} · Ошибки: {data['errors']} · Осталось: {total-processed}\nСреднее: {elapsed/processed:.1f} сек. · Скорость: {processed*3600/elapsed:.1f} шт/ч · Осталось примерно: {data.get('eta', 0)/60:.1f} мин." if processed and elapsed else "Ожидание фактических результатов")
        if data.get('pacing'):
            self.metrics.setText(self.metrics.text()+'\n'+data['pacing'])
        self.refresh()

    def photo_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Фотографии с артикулом или SKU в имени", "", "Изображения (*.jpg *.jpeg *.png *.webp)")
        if files:
            self.assign_photos(files)

    def edit_photo_sources(self):
        if self.worker and self.worker.isRunning():
            self.error('Остановите текущую операцию перед изменением набора фотографий')
            return
        selected=self.photo_table.currentRow()
        rows=self.store.active_rows()
        if selected<0 or selected>=len(rows):
            self.error('Выберите товар на вкладке «Фотографии»')
            return
        row=rows[selected]
        if row['status'] in ('creating','unknown'):
            self.error('Сначала завершите сверку незавершённой операции этого товара')
            return
        p=self.store.product(row)
        dialog=QDialog(self)
        dialog.setWindowTitle('Фотографии: '+p.title)
        dialog.resize(800,600)
        outer=QVBoxLayout(dialog)
        outer.addWidget(QLabel('Перетащите строки для изменения порядка. В Авито передаются первые 10; оригиналы сохраняются.'))
        sources=QListWidget()
        sources.setSelectionMode(QListWidget.ExtendedSelection)
        sources.setDragDropMode(QListWidget.InternalMove)
        sources.addItems(p.photos)
        outer.addWidget(sources)
        entry=QLineEdit()
        entry.setPlaceholderText('Ссылка на фотографию или полный путь к файлу')
        outer.addWidget(entry)
        def add_source():
            if entry.text().strip():sources.addItem(entry.text().strip());entry.clear()
        def add_files():
            files,_=QFileDialog.getOpenFileNames(dialog,'Изображения','','Изображения (*.jpg *.jpeg *.png *.webp)')
            sources.addItems(files)
        def remove_sources():
            for item in sources.selectedItems():sources.takeItem(sources.row(item))
        actions=QHBoxLayout()
        actions.addWidget(button('Добавить ссылку / путь',add_source))
        actions.addWidget(button('Добавить файлы',add_files))
        actions.addWidget(button('Убрать выбранные из набора',remove_sources))
        outer.addLayout(actions)
        outer.addWidget(button('Сохранить набор',dialog.accept,True))
        outer.addWidget(button('Отмена',dialog.reject))
        if dialog.exec()!=QDialog.Accepted:return
        p.photos=list(dict.fromkeys(sources.item(index).text() for index in range(sources.count())))
        self.store.update_product(row['id'],p)
        self.store.transition(row['id'],row['status'],'Исходный набор фото изменён. Для уже созданного черновика используйте отдельную команду замены.',photo_count=row['photo_count'])
        self.refresh()

    def photo_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Папка фотографий", self.photo_path.text())
        if path:
            self.photo_path.setText(path)
            self.launch(lambda: [str(p) for p in Path(path).rglob("*") if p.suffix.lower() in EXTENSIONS], self.assign_photos)

    def photo_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "Архив фотографий", "", "ZIP (*.zip)")
        if path:
            self.launch(lambda: unpack_zip(path, self.directory / "unpacked" / Path(path).stem), self.assign_photos)

    def assign_photos(self, files):
        rows = self.store.rows()
        mapping, ambiguous = match_files([self.store.product(r) for r in rows], files)
        count = 0
        for row in rows:
            p = self.store.product(row)
            p.photos = list(dict.fromkeys(p.photos + mapping[p.key]))
            count += len(mapping[p.key])
            self.store.update_product(row["id"], p)
        self.log(f"Сопоставлено фото: {count}. Неоднозначных: {len(ambiguous)}. Несопоставленных: {len(files)-count-len(ambiguous)}.")
        self.refresh()

    def prepare_photos(self):
        def action():
            count, errors = 0, 0
            for row in self.store.active_rows():
                p = self.store.product(row)
                paths = []
                try:
                    for source in p.photos:
                        if not source.startswith(("https://", "http://")) and not Path(source).is_absolute():
                            source = str(Path(row["source"]).parent / source)
                        paths.append(cache_photo(source, self.directory / "photos"))
                    p.photos = paths
                    self.store.update_product(row["id"], p)
                    count += len(paths)
                except Exception:
                    errors += 1
                    self.store.transition(row["id"], "error", "Не удалось получить или проверить фотографию. Проверьте источник в карточке товара.")
            return f"Подготовлено фото: {count}. Товаров с ошибками фото: {errors}."
        self.launch(action, self.log)

    def update_draft_photos(self):
        selected=self.photo_table.currentRow()
        rows=self.store.active_rows()
        if selected<0 or selected>=len(rows):
            self.error('Выберите товар на вкладке «Фотографии»')
            return
        row=rows[selected]
        if row['status']!='created' or not row['ad_id']:
            self.error('Для замены нужен подтверждённый созданный черновик')
            return
        product=self.store.product(row)
        def action():
            self.store.transition(row['id'],'creating','Замена фотографий существующего черновика')
            try:
                result=self.adapter.replace_photos(product,row['ad_id'])
                self.store.transition(row['id'],'created',f"Фотографии заменены: подтверждено {result['photo_count']}. ID объявления сохранён.",ad_id=result['id'],url=result['url'],photo_count=result['photo_count'])
                return 'Фотографии черновика заменены и проверены'
            except Exception as exc:
                self.store.transition(row['id'],'unknown','Результат замены фото требует сверки: '+str(exc))
                raise
        self.launch(action,self.log)

    def open_ad(self, row, column):
        pid = int(self.table.item(row, 0).text())
        record = next(r for r in self.store.rows() if r["id"] == pid)
        url = QUrl(record["url"] or "")
        if column==7 and url.scheme() == "https" and (url.host() == "avito.ru" or url.host().endswith(".avito.ru")):
            def action():
                from .navigation import PageNavigator
                nav=PageNavigator(self.bridge)
                nav.inspect(operation='openListing',path=url.path())
                return nav.wait(lambda d:d.get('path')==url.path() and bool(d.get('headings')))
            self.launch(action,lambda _:self.log('Объявление открыто в подключённом Chrome'))
        else:
            self.edit_selected_product()

    def edit_selected_product(self):
        if self.worker and self.worker.isRunning():
            self.error('Остановите текущую операцию перед правкой данных')
            return
        row=self.selected_product()
        if not row:return
        if row['status'] in ('created','creating','unknown'):
            self.error('У этого товара уже есть результат или незавершённая операция. Откройте объявление по ссылке; фотографии меняются отдельной командой.')
            return
        p=self.store.product(row)
        dialog=QDialog(self)
        dialog.setWindowTitle('Данные для переноса')
        dialog.resize(700,650)
        outer=QVBoxLayout(dialog)
        title=QLabel('Исходное название: '+p.title)
        title.setWordWrap(True)
        outer.addWidget(title)
        form=QFormLayout()
        fields={}
        from .titles import avito_title
        try:default_title=p.avito_title or avito_title(p.title)
        except ValueError:default_title=''
        for key,label,value in [('avito_title','Название Авито (до 50 символов)',default_title),('price','Цена',p.price),('brand','Бренд',p.brand),('model','Модель',p.model),('condition','Состояние: Новое / Б/у',p.condition),('origin_country','Страна происхождения',p.origin_country)]:
            edit=QLineEdit(value)
            if key=='avito_title':edit.setMaxLength(50)
            fields[key]=edit
            form.addRow(label,edit)
        outer.addLayout(form)
        description=QPlainTextEdit(p.description)
        outer.addWidget(QLabel('Описание'))
        outer.addWidget(description)
        outer.addWidget(button('Сохранить данные',dialog.accept,True))
        outer.addWidget(button('Отмена',dialog.reject))
        if dialog.exec()!=QDialog.Accepted:return
        if not fields['avito_title'].text().strip():
            self.error('Название Авито не должно быть пустым')
            return
        for key,edit in fields.items():setattr(p,key,edit.text().strip())
        p.description=description.toPlainText()
        p.validate()
        self.store.update_product(row['id'],p)
        self.store.set_setting('match_decision:'+p.key,{})
        self.store.transition(row['id'],'invalid' if p.errors else 'unchecked','; '.join(p.errors) or 'Данные изменены: требуется повторная проверка')
        self.refresh()

    def export_report(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт отчёта", "Отчёт.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        def action():
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Товары"
            ws.append(["Название", "Артикул", "SKU", "Цена", "Статус", "Дата UTC", "ID Авито", "Ссылка", "Фото подтверждено", "Причина"])
            for row in self.store.rows():
                p = self.store.product(row)
                values = [p.title, p.article, p.sku, p.price, STATUS.get(row["status"]), row["updated"], row["ad_id"], row["url"], row["photo_count"], row["error"]]
                ws.append([("'"+v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v) for v in values])
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            wb.save(path)
            return "Отчёт сохранён"
        self.launch(action, self.log)

    def export_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт логов", "marketplace-transfer.log", "Логи (*.log)")
        if path:
            if Path(path).resolve()!=self.log_path.resolve():shutil.copy2(self.log_path,path)

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(self, "Резервная копия", "transfer-backup.sqlite3", "SQLite (*.sqlite3)")
        if path:
            self.launch(lambda: self.store.backup(path), lambda _: self.log("Резервная копия сохранена"))

    def sync_pace_fields(self,value,from_seconds):
        # Normalize the supported domain once. Block both signals so the rounded
        # reciprocal cannot recursively drift or repeatedly rewrite the inputs.
        value=0 if value==0 else max(1,min(3600,value))
        seconds=round(value if from_seconds else hourly_to_seconds(value),2)
        seconds=0 if seconds==0 else max(1,min(3600,seconds))
        old_seconds=self.target_cycle.blockSignals(True)
        old_hourly=self.target_hourly.blockSignals(True)
        try:
            self.target_cycle.setValue(seconds)
            self.target_hourly.setValue(round(seconds_to_hourly(seconds),2))
        finally:
            self.target_cycle.blockSignals(old_seconds)
            self.target_hourly.blockSignals(old_hourly)
        self.describe_pacing()

    def describe_pacing(self,*unused):
        seconds=self.target_cycle.value()
        automatic=self.adaptive_pacing.isChecked()
        special='Автоматический старт' if automatic else 'Максимальная доступная скорость'
        self.target_cycle.setSpecialValueText(special)
        self.target_hourly.setSpecialValueText(special)
        text=f'Задано: {seconds:g} сек./карточку = {seconds_to_hourly(seconds):.2f} карточек/ч.' if seconds else ('Начальный темп выбирается автоматически.' if automatic else 'Плановое ограничение отключено.')
        if automatic:
            fastest,slowest=self.adaptive_min.value(),self.adaptive_max.value()
            initial=max(fastest,min(slowest,seconds or 60))
            text+=f' Автотемп: {fastest}–{slowest} сек. ({seconds_to_hourly(slowest):.2f}–{seconds_to_hourly(fastest):.2f} карточек/ч). Старт после применения: {initial:g} сек., в этих границах.'
        text+=' Фактическая скорость зависит от загрузки фотографий и сохранения на Авито.'
        self.pace_explanation.setText(text)

    def pacing_values(self):
        if self.adaptive_min.value()>self.adaptive_max.value():
            self.error('Самый быстрый цикл не должен быть длиннее самого медленного')
            return None
        return {'target_cycle_seconds':self.target_cycle.value(),
                'adaptive_pacing_enabled':self.adaptive_pacing.isChecked(),
                'adaptive_min_seconds':self.adaptive_min.value(),
                'adaptive_max_seconds':self.adaptive_max.value(),
                'adaptive_pacing_state':{}}

    def apply_pacing(self):
        values=self.pacing_values()
        if values is None:return
        value=self.target_cycle.value()
        self.store.set_settings(values)
        if values['adaptive_pacing_enabled']:
            actual=max(self.adaptive_min.value(),min(self.adaptive_max.value(),value or 60))
            self.log(f"Адаптивный темп включён: старт {actual} сек., границы {self.adaptive_min.value()}–{self.adaptive_max.value()} сек. Изменение применяется после текущей карточки.")
        else:
            self.log(f'Плановый темп: {value} сек. на товар; применяется после текущей карточки' if value else 'Плановый темп отключён: максимальная доступная скорость')

    def save_settings(self):
        if self.worker and self.worker.isRunning():
            self.error('Остановите текущую операцию перед изменением настроек')
            return
        values=self.pacing_values()
        if values is None:return
        self.store.set_settings(values)
        for key, value in {"theme": self.theme.currentIndex(), "photo_folder": self.photo_path.text(), "interval": self.interval.value(), "target_cycle_seconds":self.target_cycle.value(), "timeout": self.timeout.value(), "extension_id": self.extension_id.text(),'photo_failure_policy':self.photo_policy.currentData(),'read_retries':self.read_retries.value(),'error_policy':self.error_policy.currentData()}.items():
            self.store.set_setting(key, value)
        self.bridge.operation_timeout=self.timeout.value()
        self.bridge.read_retries=self.read_retries.value()
        self.apply_theme()
        self.log("Настройки сохранены")

    def register_host(self):
        try:
            from .registration import register
            register(self.extension_id.text(), self.directory)
            self.store.set_setting("extension_id", self.extension_id.text())
            self.log("Native Messaging зарегистрирован. Откройте меню расширения и нажмите «Подключить».")
        except Exception as exc:
            self.error(str(exc))

    def open_extension(self):
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(base / "extension")))

    def tick(self):
        if self._installing:return
        self.connection.setText(f"Chrome: {'расширение подключено' if self.bridge.connected else 'нет подключения расширения'} · Авторизация: {'личный кабинет доступен' if self.bridge.connected and self.account_verified else 'не подтверждена'}")
        self.statusBar().showMessage(f"Время работы: {int(time.monotonic()-self.started)//60} мин. · Данные: {self.directory}")
        # Reflect durable results produced by background tasks or a diagnostic recovery run.
        modified=self.store.path.stat().st_mtime_ns
        if modified!=getattr(self,'last_db_modified',None):
            self.last_db_modified=modified
            self.refresh()
        if not self.bridge.connected:
            self.auto_diagnosed = False
            self.account_verified = False
        elif not self.auto_diagnosed and self.bridge.extension and not (self.worker and self.worker.isRunning()):
            self.auto_diagnosed = True
            self.diagnose()

    def apply_theme(self):
        dark = self.theme.currentIndex() == 0
        bg, panel, fg, muted = ("#10151f", "#1b2433", "#e9eff8", "#94a6c0") if dark else ("#f2f5fa", "#ffffff", "#192538", "#53647d")
        self.setStyleSheet(f"""
            QWidget {{background:{bg}; color:{fg}; font-family:'Segoe UI'; font-size:13px;}}
            QWidget#sidebar {{background:{panel};}} QLabel#logo {{font-size:19px; font-weight:800; padding:20px 4px; color:#6e9eff;}}
            QLabel#heading {{font-size:28px; font-weight:700; padding-bottom:6px;}}
            QLabel#card {{background:{panel}; border-radius:12px; padding:22px 8px; font-size:19px;}}
            QLabel#notice {{background:{panel}; border-left:3px solid #e1ac55; padding:18px; border-radius:8px;}}
            QPushButton {{background:{panel}; border:1px solid {muted}; border-radius:7px; padding:11px 15px;}}
            QPushButton:hover {{border-color:#6e9eff;}} QPushButton#primary {{background:#3369d7; color:white; border:0; font-weight:600;}}
            QListWidget {{background:{panel}; border:0;}} QListWidget::item {{padding:14px 12px; border-radius:6px;}} QListWidget::item:selected {{background:#33599a; color:white;}}
            QLineEdit,QComboBox,QSpinBox,QPlainTextEdit {{background:{panel}; border:1px solid {muted}; border-radius:6px; padding:7px;}}
            QTableWidget {{background:{bg}; alternate-background-color:{panel}; gridline-color:{panel}; border:0;}}
            QHeaderView::section {{background:{panel}; color:{muted}; border:0; padding:10px;}}
            QProgressBar {{border:0; border-radius:6px; background:{panel}; height:24px; text-align:center;}}
            QProgressBar::chunk {{background:#467de6; border-radius:6px;}}
        """)

    def closeEvent(self, event):
        if self.update_busy:
            self.log('Дождитесь завершения загрузки обновления или отмените её')
            event.ignore()
            return
        if self.worker and self.worker.isRunning():
            if self.queue:
                self.queue.stop()
            self.log("Дождитесь завершения текущей операции перед закрытием")
            event.ignore()
            return
        self.bridge.close()
        self.log_handler.close()
        self.logger.removeHandler(self.log_handler)
        event.accept()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument('--health-check',type=Path)
    args = parser.parse_args()
    if args.health_check:
        from .updating.install import data_signature
        directory=args.data_dir or data_dir()
        store=Store(directory/'transfer.sqlite3',recover=False)
        result=data_signature(store.path)
        result.update({'version':__version__,'history_rows':len(store.history()),'sessions':len(store.sessions()),'data_directory':str(directory.resolve())})
        args.health_check.parent.mkdir(parents=True,exist_ok=True)
        args.health_check.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return
    app = QApplication(sys.argv)
    app.setApplicationName("Marketplace Transfer")
    directory = args.data_dir or data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if not args.smoke_test:
        from .updating.locking import update_in_progress,application_mutex
        if update_in_progress(directory/'updates'):
            QMessageBox.information(None,'Marketplace Transfer','Выполняется обновление программы. Дождитесь его завершения.')
            return
        from .updating.install import pending_recovery
        if pending_recovery(directory):
            QMessageBox.warning(None,'Незавершённое обновление','Обновление было прервано. Перед новой работой запустите «Восстановление Marketplace Transfer» из меню «Пуск». Товарные данные и резервная копия сохранены.')
            return
        try:instance_mutex=application_mutex()
        except RuntimeError as exc:
            QMessageBox.information(None,'Marketplace Transfer',str(exc))
            return
    lock = QLockFile(str(directory / "application.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "Marketplace Transfer", "Программа уже запущена")
        return
    window = Window(directory)
    if not args.smoke_test:
        try:
            from .registration import bundled_extension_id, register
            base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
            host = base / "MarketplaceTransferHost.exe" if getattr(sys, "frozen", False) else base / "outputs" / "MarketplaceTransfer" / "MarketplaceTransferHost.exe"
            identity = bundled_extension_id(base)
            register(identity, directory, host_path=host)
            window.extension_id.setText(identity)
            window.log("Native Host зарегистрирован для расширения " + identity)
        except Exception as exc:
            window.log("Настройка Native Host: " + str(exc))
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets" / "app.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window.show()
    if not args.smoke_test:window.schedule_update_checks()
    if args.smoke_test:
        def smoke():
            try:
                results = []
                for index in range(8):
                    window.navigation.setCurrentRow(index)
                    results.append(window.pages.currentIndex() == index)
                args.smoke_test.parent.mkdir(parents=True, exist_ok=True)
                args.smoke_test.write_text(json.dumps({"ok": all(results), "pages": len(results), "frozen": bool(getattr(sys,"frozen",False)), "python":sys.version,"version":__version__,"history_rows":window.history_table.rowCount(),"stored_events":len(window.store.history()),"sessions":len(window.store.sessions()),"database":str(window.store.path.resolve()),"metrics":window.metrics.text()},ensure_ascii=False,indent=2), encoding="utf-8")
            finally:
                window.close()
        QTimer.singleShot(1000, smoke)
    sys.exit(app.exec())
