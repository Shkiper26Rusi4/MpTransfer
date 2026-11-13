from dataclasses import dataclass
import threading
import time
from .domain import Product, classify
from .bridge import BridgeError
from dataclasses import replace
from .pacing import AdaptivePacer, failure_kind


def classify_snapshot(store,product,snapshot):
    products=[Product(**p) for p in snapshot['products']]
    decision=store.setting('match_decision:'+product.key,{})
    if decision.get('kind')=='user_completed' and decision.get('confirmed_by')=='user':
        return 'exists'
    items=snapshot.get('items',[])
    if decision.get('kind')=='distinct' and len(items)==len(products):
        excluded=set(decision.get('candidate_ids',[]))
        products=[p for p,item in zip(products,items) if item['id'] not in excluded]
    if decision.get('kind')=='exists':
        return 'exists' if any(item['id']==decision.get('id') for item in items) else 'review'
    return classify(product,products,snapshot.get('complete') is True)


class AvitoAdapter:
    """Fail closed until a real-site adapter has been inspected and verified."""
    def __init__(self, bridge, store=None):
        self.bridge = bridge
        self.cache = {}
        self.last_inventory = None
        self.store=store
        self.progress=lambda text:None
        self.photo_limit=10

    @property
    def verified_for_batch(self):
        return not self.batch_ready_reason()

    def batch_ready_reason(self):
        proof=self.store.setting('avito_integration_validation',{}) if self.store else {}
        if len(proof.get('drafts',[]))<3 or not proof.get('duplicate_recheck'):
            return 'Массовый перенос заблокирован до проверки нескольких реальных черновиков, фотографий и защиты от повтора'
        if not self.last_inventory or not self.last_inventory.get('complete'):
            return 'Сначала выполните полную проверку товаров в текущем аккаунте'
        ids={item['id'] for item in self.last_inventory['items']}
        if not all(draft['id'] in ids for draft in proof['drafts']):
            return 'Тестовые черновики не обнаружены в текущем аккаунте; требуется повторная проверка интеграции'
        return ''

    def validate_product(self, product, batch=False):
        from .drafts import DraftCreator,category_for
        DraftCreator.validate(product)
        if batch and self.store:
            proof=self.store.setting('avito_integration_validation',{})
            if category_for(product) not in proof.get('categories',[]):
                raise BridgeError('Категория ещё не подтверждена реальным сохранением черновика')

    def diagnose(self):
        return self.bridge.call("diagnose")

    def inventory(self):
        from .inventory import InventoryCollector
        result=InventoryCollector(self.bridge,self.progress).collect_detailed(self.cache)
        self.cache={item['id']:item for item in result.get('items',[]) if item.get('detailsVerified')}
        self.last_inventory=result
        return result

    def create_draft(self, product, photos):
        from .drafts import DraftCreator
        if not self.last_inventory or not self.last_inventory.get('complete'):
            raise BridgeError('Нет полной свежей проверки аккаунта')
        policy=self.store.setting('photo_failure_policy','strict') if self.store else 'strict'
        failed=self.store.setting('failed_photo_origins',[]) if self.store else []
        self.last_creator=DraftCreator(self.bridge,photo_failure_policy=policy,failed_photo_origins=failed,progress=self.progress)
        return self.last_creator.create(product,{item['id'] for item in self.last_inventory['items']})

    def replace_photos(self,product,ident):
        from .drafts import replace_draft_photos
        policy=self.store.setting('photo_failure_policy','strict') if self.store else 'strict'
        failed=self.store.setting('failed_photo_origins',[]) if self.store else []
        result=replace_draft_photos(self.bridge,product,ident,policy,failed)
        self.cache.pop(str(ident),None)
        self.last_inventory=None
        return result

    def recover_saved(self,row,product):
        from .drafts import verify_draft_page
        from .navigation import PageNavigator
        from .titles import avito_title
        if not row.get('ad_id'):raise BridgeError('Нет найденного ID для сверки сохранения')
        ident=str(row['ad_id'])
        metadata=self.store.setting('pending_verification:'+str(row['id']),{}) if self.store else {}
        policy=self.store.setting('photo_failure_policy','strict') if self.store else 'strict'
        title=metadata.get('avito_title') or product.avito_title or avito_title(product.title)
        nav=PageNavigator(self.bridge)
        nav.inspect(operation='openSavedDraft',id=ident)
        nav.wait(lambda d:d.get('path')=='/draft/'+ident and bool(d.get('headings')))
        confirmed_count=[0]
        def confirmed(detail):
            gallery=[i for i in detail.get('productImages',[]) if any('gallery-list-item-link' in str(parent.get('className','')).split() for parent in i.get('parents',[]))]
            count=metadata.get('photo_count') or row.get('photo_count') or min(10,len(product.photos))
            if policy=='keep_loaded' and not metadata.get('photo_count') and gallery:
                count=len(gallery)
            if not 1<=count<=min(10,len(product.photos)):return False
            try:
                confirmed_count[0]=verify_draft_page(replace(product,title=title,photos=product.photos[:count]),detail)
                return True
            except BridgeError:return False
        nav.wait(confirmed,timeout=30)
        return {'verified':True,'id':ident,'url':'https://www.avito.ru/draft/'+ident,'photo_count':confirmed_count[0],'status':'draft'}


class TransferQueue:
    def __init__(self, store, adapter, callback=lambda x: None, interval=2, product_ids=None):
        self.store, self.adapter, self.callback = store, adapter, callback
        self.interval = max(0, interval)
        self.product_ids=set(product_ids) if product_ids is not None else None
        self.resume_event = threading.Event()
        self.resume_event.set()
        self.stop_event = threading.Event()
        self.pacer=AdaptivePacer(store)

    def pause(self):
        self.resume_event.clear()

    def resume(self):
        self.resume_event.set()

    def stop(self):
        self.stop_event.set()
        self.resume_event.set()

    def checkpoint(self):
        self.resume_event.wait()
        return not self.stop_event.is_set()

    def next_item_delay(self, item_started, finished_at):
        # A target cycle includes the check, creation and verification time.
        # Keep the independent site safety interval even when a card was slow.
        target=self.pacer.cycle
        return max(self.interval, target-max(0, finished_at-item_started))

    def check_all(self):
        with self.store.session('check') as sid:
            result=self._check_all()
            self.store.finish_session(sid,'completed' if result.get('complete') else 'incomplete')
            return result

    def _check_all(self):
        # A completed queue can leave a callback bound to a deleted Qt worker.
        # A fresh standalone scan must not emit through that previous worker.
        if hasattr(self.adapter,'progress'):
            self.adapter.progress=lambda text:None
        snapshot = self.adapter.inventory()
        self.store.snapshot(snapshot)
        listings = [Product(**p) for p in snapshot["products"]]
        for row in self.store.active_rows():
            if row["status"] == "created":
                continue
            p = self.store.product(row)
            confirmation=self.store.setting('match_decision:'+p.key,{})
            if confirmation.get('kind')=='user_completed' and confirmation.get('confirmed_by')=='user':
                detail='Пользователь подтвердил: внесено вручную' if confirmation.get('source')=='manual' else 'Пользователь подтвердил: уже есть на Авито'
                self.store.transition(row['id'],'exists',detail,photo_count=row['photo_count'])
                continue
            if p.errors:
                continue
            if row['status']=='unknown' and row.get('ad_id') and hasattr(self.adapter,'recover_saved'):
                try:
                    result=self.adapter.recover_saved(row,p)
                    if result.get('verified'):
                        self.store.transition(row['id'],'created','Сохранение восстановлено проверкой существующего черновика; повторного создания не было',ad_id=result['id'],url=result['url'],photo_count=result['photo_count'])
                        continue
                except Exception as exc:
                    self.store.transition(row['id'],'unknown','Сверка найденного черновика не завершена: '+str(exc),ad_id=row['ad_id'],url=row['url'])
                    continue
            status = classify_snapshot(self.store,p,snapshot)
            # An unknown save needs explicit reconciliation with a verified draft ID.
            if row["status"] == "unknown" and status == "missing":
                status = "review"
            self.store.transition(row["id"], status)
        return snapshot

    def run(self):
        if not getattr(self.adapter,'verified_for_batch',False):
            return self._run()
        ids=[r['id'] for r in self.store.active_rows() if r['status']=='missing' and (self.product_ids is None or r['id'] in self.product_ids)]
        if not ids:return
        with self.store.session('transfer',product_ids=ids) as sid:
            result=self._run()
            self.store.finish_session(sid,'stopped' if self.stop_event.is_set() else 'completed')
            return result

    def _run(self):
        # No local setting can enable an unverified production adapter.
        if not getattr(self.adapter, "verified_for_batch", False):
            reason=self.adapter.batch_ready_reason() if hasattr(self.adapter,'batch_ready_reason') else 'Массовый перенос заблокирован до реальной проверки одного черновика, фотографий и защиты от повтора'
            raise BridgeError(reason)
        rows = [r for r in self.store.active_rows() if r["status"] == "missing" and (self.product_ids is None or r['id'] in self.product_ids)]
        start = time.monotonic()
        done = errors = 0
        for index, row in enumerate(rows):
            if not self.checkpoint():
                break
            item_started=time.monotonic()
            p = self.store.product(row)
            def stage(text):
                elapsed=time.monotonic()-start
                self.callback({"title":p.title,"article":p.article,"stage":text,"done":done,"errors":errors,"total":len(rows),"processed":index,"elapsed":elapsed,"eta":elapsed/index*(len(rows)-index) if index else 0})
            if hasattr(self.adapter,'progress'):
                self.adapter.progress=stage
            stage('Повторная проверка')
            creating = False
            outcome='skipped'
            try:
                if hasattr(self.adapter,'validate_product'):
                    self.adapter.validate_product(p,batch=True)
                snapshot = self.adapter.inventory()
                self.store.snapshot(snapshot)
                if hasattr(self.adapter,'batch_ready_reason') and self.adapter.batch_ready_reason():
                    self.stop()
                    if not snapshot.get('complete'):
                        for pending in self.store.active_rows():
                            if pending['status']=='missing':self.store.transition(pending['id'],'unchecked','Полнота текущей проверки не подтверждена')
                    raise BridgeError(self.adapter.batch_ready_reason())
                status = classify_snapshot(self.store,p,snapshot)
                if status != "missing":
                    self.store.transition(row["id"], status)
                else:
                    self.store.transition(row['id'],'missing','Отсутствие подтверждено перед созданием')
                    if not p.photos:
                        raise ValueError("Нет фотографий")
                    if not self.checkpoint():break
                    self.store.transition(row["id"], "creating")
                    creating = True
                    result = self.adapter.create_draft(p, p.photos)
                    reduced_allowed=self.store.setting('photo_failure_policy')=='keep_loaded' and result.get('photo_failure_policy')=='keep_loaded' and 1<=result.get('photo_count',0)<=min(10,len(p.photos))
                    expected=min(getattr(self.adapter,'photo_limit',len(p.photos)),len(p.photos))
                    if not result.get("verified") or result.get("status") != "draft" or not result.get("id") or not result.get("url") or not (result.get("photo_count")==expected or reduced_allowed):
                        raise BridgeError("Сохранение черновика или фотографий не подтверждено")
                    p.avito_title=result.get('avito_title',p.title)
                    self.store.update_product(row['id'],p)
                    detail=f"Подтверждено фото: {result['photo_count']} из {len(p.photos)}"
                    self.store.transition(row["id"], "created",detail, ad_id=result["id"], url=result["url"], photo_count=result["photo_count"])
                    done += 1
                    outcome='success'
            except Exception as exc:
                creator=getattr(self.adapter,'last_creator',None)
                started=creating and (creator is None or creator.input_started)
                self.store.transition(row["id"], "unknown" if started else "error", str(exc),ad_id=getattr(exc,'ad_id',None),url=getattr(exc,'url',None))
                if getattr(exc,'ad_id',None):
                    self.store.set_setting('pending_verification:'+str(row['id']),{'id':exc.ad_id,'url':exc.url,'photo_count':exc.expected_photo_count,'avito_title':exc.avito_title})
                errors += 1
                outcome=failure_kind(exc)
                message=str(exc).casefold()
                if self.store.setting('error_policy','continue')=='stop' or outcome=='blocked' or any(word in message for word in ('проверку безопасности','captcha','капч','расширение не подключено')):
                    self.stop()
            decision=self.pacer.observe(outcome)
            if decision.stop:
                self.stop()
            def pacing_status():
                enabled=self.store.setting('adaptive_pacing_enabled',False)
                return f"Автотемп: {self.pacer.cycle:g} сек./товар · {decision.reason}" if enabled else ''
            elapsed = time.monotonic() - start
            processed = index + 1
            self.callback({"title": p.title, "article": p.article, "stage": "Обработано"+(' · '+decision.reason if decision.stop else ''), "done": done, "errors": errors, "total": len(rows), "processed": processed, "elapsed": elapsed, "eta": elapsed / processed * (len(rows)-processed),"pacing":pacing_status()})
            if index+1<len(rows) and not self.stop_event.is_set():
                deadline=time.monotonic()+self.next_item_delay(item_started,time.monotonic())
                while self.checkpoint() and time.monotonic()<deadline:
                    remaining=deadline-time.monotonic()
                    elapsed=time.monotonic()-start
                    self.callback({"title":p.title,"article":p.article,
                        "stage":f"Ожидание заданного темпа: {max(1,int(remaining))} сек.",
                        "done":done,"errors":errors,"total":len(rows),"processed":processed,
                        "elapsed":elapsed,"eta":elapsed/processed*(len(rows)-processed),"pacing":pacing_status()})
                    self.stop_event.wait(min(1,remaining))
