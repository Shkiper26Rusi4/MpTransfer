from .domain import Product,classify
from .drafts import DraftCreator
from .bridge import BridgeError
from .engine import classify_snapshot


def transfer_one(store, adapter, product_id):
    row=next(row for row in store.rows() if row['id']==product_id)
    if row['status']=='created':
        return {'skipped':True,'id':row['ad_id'],'url':row['url'],'reason':'verified_saved_link'}
    if row['status'] in ('creating','unknown'):
        raise BridgeError('Результат предыдущей попытки неизвестен: сначала требуется сверка, повторное создание запрещено')
    product=store.product(row)
    DraftCreator.validate(product)
    snapshot=adapter.inventory()
    store.snapshot(snapshot)
    status=classify_snapshot(store,product,snapshot)
    if status!='missing':
        store.transition(product_id,status)
        return {'skipped':True,'reason':status}
    store.transition(product_id,'creating','Реальная проверка формы и сохранения черновика')
    try:
        result=adapter.create_draft(product,product.photos)
        if not result.get('verified') or result.get('status')!='draft':
            raise BridgeError('Нет подтверждения сохранения черновика')
        product.avito_title=result.get('avito_title',product.title)
        store.update_product(product_id,product)
        detail='Проверены отдельная страница черновика, поля и фотографии'
        if result.get('omitted_photo_count'):
            detail+=f". Из {len(product.photos)} фото использованы первые 10: лимит Авито. Исходные изображения сохранены."
        store.transition(product_id,'created',detail,ad_id=result['id'],url=result['url'],photo_count=result['photo_count'])
        return result
    except Exception as exc:
        creator=getattr(adapter,'last_creator',None)
        status='error' if creator is not None and not creator.input_started else 'unknown'
        store.transition(product_id,status,str(exc),ad_id=getattr(exc,'ad_id',None),url=getattr(exc,'url',None))
        if getattr(exc,'ad_id',None):
            store.set_setting('pending_verification:'+str(product_id),{'id':exc.ad_id,'url':exc.url,'photo_count':exc.expected_photo_count,'avito_title':exc.avito_title})
        raise
