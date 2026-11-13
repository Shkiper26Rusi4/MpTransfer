"""Update controls use a separate worker, never the marketplace queue's worker."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from PySide6.QtCore import QThread,Signal,QTimer
from PySide6.QtWidgets import QLabel,QLineEdit,QCheckBox,QPushButton,QDialog,QVBoxLayout,QPlainTextEdit,QDialogButtonBox,QProgressDialog,QMessageBox
from . import __version__
from .history import safe_text
from .updating.security import load_config,parse_repository,UpdateError,verify_document
from .updating.feed import ReleaseFeed
from .updating.locking import UpdateLock
from .updating.install import prepare_job,installed_directory,atomic_json,validate_job


class UpdateTask(QThread):
    result=Signal(object)
    failure=Signal(str)
    progress=Signal(int)
    def __init__(self,action,parent):
        super().__init__(parent)
        self.action=action
        self.cancel=threading.Event()
    def run(self):
        try:self.result.emit(self.action(self))
        except Exception as exc:self.failure.emit(safe_text(exc))


class UpdateControls:
    def init_update_controls(self,form,layout):
        self.update_worker=None
        self.update_busy=False
        self.pending_update_job=None
        self.install_requested=False
        self._installing=False
        self.update_config=load_config(self.directory)
        self.update_auto=QCheckBox('Автоматически проверять обновления')
        self.update_auto.setChecked(self.store.setting('update_auto_check',True))
        self.update_startup=QCheckBox('Проверять обновления при запуске')
        self.update_startup.setChecked(self.store.setting('update_on_startup',True))
        form.addRow('Обновления',self.update_auto)
        form.addRow('',self.update_startup)
        form.addRow('Версия программы',QLabel(__version__))
        self.update_repository=QLineEdit(self.update_config.get('repository') or '')
        self.update_repository.setPlaceholderText('HTTPS-адрес вашего репозитория GitHub')
        self.update_repository.setToolTip('Принимаются только обновления с закреплённой в программе доверенной подписью. Ключ доверия через это поле изменить нельзя.')
        form.addRow('Репозиторий обновлений',self.update_repository)
        self.update_status=QLabel()
        self.update_status.setWordWrap(True)
        form.addRow('Состояние обновления',self.update_status)
        check=QPushButton('Проверить обновления сейчас')
        check.clicked.connect(lambda:self.check_updates(manual=True))
        layout.addWidget(check)
        source=QPushButton('Сохранить источник обновлений')
        source.clicked.connect(self.save_update_source)
        layout.addWidget(source)
        self.install_downloaded=QPushButton('Установить загруженное обновление')
        self.install_downloaded.clicked.connect(self.request_pending_install)
        layout.addWidget(self.install_downloaded)
        self.update_timer=QTimer(self)
        self.update_timer.setInterval(6*60*60*1000)
        self.update_timer.timeout.connect(lambda:self.check_updates(False))
        self.update_auto.toggled.connect(self.persist_update_settings)
        self.update_startup.toggled.connect(self.persist_update_settings)
        self.refresh_update_status()

    def refresh_update_status(self):
        at=self.store.setting('update_last_check','ещё не выполнялась')
        error=self.store.setting('update_last_error','')
        message=self.store.setting('update_message','')
        self.update_status.setText(f'Последняя попытка проверки (UTC): {at}\n{message}'+('\n'+error if error else ''))
        pending=self.store.setting('pending_update_job')
        self.install_downloaded.setEnabled(bool(pending and Path(pending).is_file()))

    def persist_update_settings(self,*unused):
        self.store.set_settings({'update_auto_check':self.update_auto.isChecked(),'update_on_startup':self.update_startup.isChecked()})
        if self.update_auto.isChecked():self.update_timer.start()
        else:self.update_timer.stop()

    def save_update_source(self):
        if self.update_busy:
            self.update_status.setText('Дождитесь завершения текущей проверки или загрузки обновления')
            return
        try:
            text=self.update_repository.text().strip()
            repo=parse_repository(text) if text else None
            self.store.set_settings({'update_repository':repo,'pending_update_job':None,'update_last_error':'',
                                    'update_message':'Источник сохранён. Публичный ключ доверия остаётся прежним.'})
            self.update_config=load_config(self.directory)
            self.pending_update_job=None;self.install_requested=False
            self.update_repository.setText(self.update_config.get('repository') or '')
            self.refresh_update_status()
        except Exception as exc:self.update_failed(str(exc),True)

    def schedule_update_checks(self):
        if self.update_auto.isChecked():
            self.update_timer.start()
            if self.update_startup.isChecked():QTimer.singleShot(3000,lambda:self.check_updates(False))
        receipt=self.directory/'updates/last-result.json'
        if receipt.exists():
            from .updating.security import load_json
            try:
                result=load_json(receipt.read_bytes())
                if result.get('status')=='rolled_back':
                    self.store.set_setting('update_message','Последнее обновление не прошло проверку; прежняя версия и база восстановлены.')
                    self.refresh_update_status()
            except (OSError,UpdateError):pass

    def _update_task(self,action,on_result,manual=False):
        if self.update_busy:
            self.update_status.setText('Проверка или загрузка обновления уже выполняется')
            return False
        worker=UpdateTask(action,self)
        self.update_worker=worker
        self.update_busy=True
        worker.result.connect(on_result)
        worker.failure.connect(lambda text:self.update_failed(text,manual))
        def finished():
            if self.update_worker is worker:self.update_busy=False
            QTimer.singleShot(0,self.try_install_update)
        worker.finished.connect(finished)
        worker.start()
        return True

    def check_updates(self,manual=False):
        if self._installing:return
        if not manual:
            if not self.update_auto.isChecked():return
            until=self.store.setting('update_postponed_until')
            if until and datetime.fromisoformat(until)>datetime.now(timezone.utc):return
        self.store.set_setting('update_last_check',datetime.now(timezone.utc).isoformat())
        self.update_status.setText('Проверка подписанного релиза…')
        with self.store.connect() as db:schema=db.execute('PRAGMA user_version').fetchone()[0]
        feed=ReleaseFeed(self.update_config,__version__,schema)
        def action(worker):
            with UpdateLock(self.directory/'updates'):return feed.check()
        self._update_task(action,lambda document:self.update_checked(document,manual),manual)

    def update_failed(self,text,manual=False):
        self.store.set_settings({'update_last_error':safe_text(text),'update_message':'Обновление не установлено; текущая программа продолжает работу.'})
        self.refresh_update_status()
        if hasattr(self,'download_progress'):self.download_progress.close()
        self.log('Обновления: '+safe_text(text))
        if manual:QMessageBox.information(self,'Обновление Marketplace Transfer',safe_text(text))

    def update_checked(self,document,manual):
        self.store.set_settings({'update_last_error':'','update_last_success':datetime.now(timezone.utc).isoformat(),
                                'update_message':'Новая версия доступна' if document else 'Новых стабильных версий нет'})
        self.refresh_update_status()
        if not document:
            if manual:QMessageBox.information(self,'Marketplace Transfer','Установлена актуальная стабильная версия.')
            return
        payload=verify_document(document,self.update_config)
        dialog=QDialog(self)
        dialog.setWindowTitle('Доступно обновление Marketplace Transfer')
        dialog.resize(620,430)
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Текущая версия: {__version__}\nНовая версия: {payload['version']}"))
        notes=QPlainTextEdit(payload['notes'])
        notes.setReadOnly(True)
        layout.addWidget(notes)
        buttons=QDialogButtonBox()
        install=buttons.addButton('Скачать и установить',QDialogButtonBox.AcceptRole)
        later=buttons.addButton('Позже',QDialogButtonBox.RejectRole)
        install.clicked.connect(dialog.accept);later.clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()==QDialog.Accepted:
            # The check worker can still be emitting its result for a few ms.
            QTimer.singleShot(100,lambda:self.download_update(document))
        else:
            self.store.set_setting('update_postponed_until',(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())

    def download_update(self,document):
        if not getattr(sys,'frozen',False) or Path(sys.executable).parent.resolve()!=installed_directory().resolve():
            self.update_failed('Установка обновления доступна в установленном EXE. Для исходников или переносимой версии используйте установщик.',True)
            return
        with self.store.connect() as db:schema=db.execute('PRAGMA user_version').fetchone()[0]
        feed=ReleaseFeed(self.update_config,__version__,schema)
        self.download_progress=QProgressDialog('Скачивание и проверка подписи…','Отмена',0,100,self)
        self.download_progress.setWindowTitle('Обновление Marketplace Transfer')
        self.download_progress.setAutoClose(False)
        def action(worker):
            with UpdateLock(self.directory/'updates'):
                path=feed.download(document,self.directory/'updates/downloads',worker.progress.emit,worker.cancel)
                if worker.cancel.is_set():raise UpdateError('Загрузка отменена')
                return prepare_job(document,path,self.directory,Path(sys.executable).parent,__version__,self.update_config)
        if not self._update_task(action,self.update_downloaded,True):
            self.download_progress.close()
            return
        self.update_worker.progress.connect(self.download_progress.setValue)
        self.download_progress.canceled.connect(self.update_worker.cancel.set)
        self.download_progress.show()

    def update_downloaded(self,job):
        self.download_progress.close()
        if self.update_worker.cancel.is_set():
            self.update_failed('Загрузка отменена',False)
            return
        self.store.set_setting('pending_update_job',str(job))
        self.pending_update_job=Path(job)
        self.install_requested=True
        self.update_status.setText('Обновление проверено и загружено. Установка начнётся после безопасного завершения текущей операции.')
        self.try_install_update()

    def request_pending_install(self):
        pending=self.store.setting('pending_update_job')
        if pending and Path(pending).is_file():
            self.pending_update_job=Path(pending)
            self.install_requested=True
            self.try_install_update()

    def try_install_update(self):
        if not self.install_requested or not self.pending_update_job or self._installing:return
        if self.worker and self.worker.isRunning():return
        if self.update_busy:return
        try:
            job=validate_job(self.pending_update_job,self.directory,installed_directory())
            verify_document(job['document'],self.update_config)
            import os
            job['parent_pid']=os.getpid()
            atomic_json(self.pending_update_job,job)
            self._installing=True
            self.timer.stop();self.update_timer.stop()
            helper=self.pending_update_job.parent/'MarketplaceTransferUpdater.exe'
            subprocess.Popen([str(helper),'--job',str(self.pending_update_job)],creationflags=subprocess.CREATE_NO_WINDOW)
            self.store.set_setting('pending_update_job',None)
            self.close()
        except Exception as exc:
            self._installing=False
            self.install_requested=False
            self.timer.start(1000)
            self.update_failed(str(exc),True)
