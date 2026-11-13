"""DOM form operations confirmed against the current Avito vacuum-parts form."""
import base64
import hashlib
import mimetypes
import re
from decimal import Decimal
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from .bridge import BridgeError
from .photos import cache_photo
from .domain import norm
from .navigation import PageNavigator
from .titles import avito_title

VACUUM_ROUTE='Запчасти · Пылесосы и запчасти · Для дома'
CHEMISTRY_ROUTE='Бытовая химия · Хозяйственные товары · Товары для кухни'
LAUNDRY_ROUTE='Аксессуары · Запчасти и аксессуары · Стиральные и сушильные машины'
KITCHEN_SMALL_ROUTE='Аксессуары и запчасти · Мелкая кухонная техника · Для кухни'
HOOD_ROUTE='Запчасти и аксессуары · Вытяжки и аксессуары · Для кухни'
FRIDGE_ROUTE='Запчасти и аксессуары · Холодильники и морозильные камеры · Для кухни'
CATEGORY_PATHS={
    VACUUM_ROUTE:['Для дома и дачи','Бытовая техника','Для дома','Пылесосы и запчасти','Запчасти'],
    HOOD_ROUTE:['Для дома и дачи','Бытовая техника','Для кухни','Вытяжки и аксессуары','Запчасти и аксессуары'],
    KITCHEN_SMALL_ROUTE:['Для дома и дачи','Бытовая техника','Для кухни','Мелкая кухонная техника','Аксессуары и запчасти'],
    FRIDGE_ROUTE:['Для дома и дачи','Бытовая техника','Для кухни','Холодильники и морозильные камеры','Запчасти и аксессуары'],
}


class DraftVerificationError(BridgeError):
    def __init__(self, message, ident, product):
        super().__init__(message)
        self.ad_id=ident
        self.url='https://www.avito.ru/draft/'+ident
        self.expected_photo_count=len(product.photos)
        self.avito_title=product.title


def category_for(product):
    types=' '.join(str(v) for k,v in product.extra.items() if k.startswith('Тип Ozon ['))
    if 'пылесос' in norm(types):
        return VACUUM_ROUTE
    chemical_types={'Жидкое средство для стирки','Капсулы для стирки','Чистящее средство','Кондиционер для стирки белья','Таблетки для стирки','Порошок для посудомоечной машины','Ополаскиватель для посудомоечной машины','Стиральный порошок','Очиститель для посудомоечной машины','Таблетки для посудомоечной машины'}
    if types in chemical_types or 'ароматизатор' in norm(product.title):
        return CHEMISTRY_ROUTE
    if types in {'Аксессуары, запчасти для стиральной машины','Аксессуары, запчасти для сушильной машины'}:
        return LAUNDRY_ROUTE
    if types=='Аксессуары, запчасти для кофемашины' or types in {'Бутылка','Кувшин'} and 'контейнер для молока' in norm(product.title):
        return KITCHEN_SMALL_ROUTE
    if types=='Аксессуары, запчасти для вытяжки':return HOOD_ROUTE
    if types=='Аксессуары, запчасти для холодильника':return FRIDGE_ROUTE
    raise BridgeError('Категория этого типа товара ещё не проверена на реальной форме Авито')


def verify_draft_page(product, detail):
    if not product.photos:
        raise BridgeError('Нет ни одной фотографии для подтверждения черновика')
    if not re.fullmatch(r'/draft/\d+',detail.get('path','')) or product.title not in detail.get('headings',[]):
        raise BridgeError('Не подтверждена отдельная страница черновика')
    body=detail.get('pageText','')
    if norm(product.description) not in norm(body):
        raise BridgeError('Описание черновика не совпало с источником')
    if 'Состояние: '+product.condition not in body or 'Страна происхождения товара: '+product.origin_country not in body:
        raise BridgeError('Характеристики черновика не совпали с источником')
    price=re.search(r'([\d \xa0\u202f.,]+)₽',body)
    normalize=lambda text:re.sub(r'[ \xa0\u202f]','',text).replace(',','.')
    if not price or Decimal(normalize(price.group(1)))!=Decimal(normalize(product.price)):
        raise BridgeError('Цена черновика не совпала с источником')
    gallery=[i for i in detail.get('productImages',[]) if any('gallery-list-item-link' in str(parent.get('className','')).split() for parent in i.get('parents',[]))]
    if len(product.photos)==1 and not gallery:
        gallery=[i for i in detail.get('productImages',[]) if any('gallery-img-frame' in str(parent.get('className','')).split() for parent in i.get('parents',[]))]
    if len(gallery)!=len(product.photos) or not all(i.get('loaded') and '.img.avito.st/' in i.get('source','') for i in gallery):
        raise BridgeError('Количество или загрузка фотографий сохранённого черновика не подтверждены')
    return len(gallery)


class DraftCreator:
    def __init__(self, bridge, photo_failure_policy='strict', failed_photo_origins=(), progress=lambda text:None):
        self.nav=PageNavigator(bridge)
        self.directory=bridge.directory
        self.input_started=False
        self.photo_failure_policy=photo_failure_policy
        self.failed_photo_origins=set(failed_photo_origins)
        self.progress=progress

    @staticmethod
    def validate(product):
        category_for(product)
        avito_title(product.avito_title or product.title)
        if product.condition not in ('Новое','Б/у') or not product.origin_country:
            raise BridgeError('Нужны подтверждённые состояние и страна происхождения')
        if not product.photos:
            raise BridgeError('Нет фотографий в источнике')
        if not product.description:
            raise BridgeError('Нет описания в исходной таблице')
        price=Decimal(re.sub(r'[ \xa0\u202f]','',product.price).replace(',','.'))
        if not price.is_finite() or price<=0 or price!=price.to_integral_value():
            raise BridgeError('Для Авито укажите положительную цену в целых рублях; автоматическое округление отключено')

    def create(self, product, baseline_ids):
        self.validate(product)
        source_title=product.title
        original_photo_count=len(product.photos)
        product=replace(product,title=avito_title(product.avito_title or product.title),photos=product.photos[:10])
        self.progress('Подготовка оригиналов фотографий')
        prepared=[None]*len(product.photos)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures={pool.submit(cache_photo,source,self.directory/'photos'):index for index,source in enumerate(product.photos)}
            for completed,future in enumerate(as_completed(futures),1):
                prepared[futures[future]]=future.result()
                self.progress(f'Подготовлено оригиналов: {completed}/{len(prepared)}')
        product=replace(product,photos=prepared)
        nav=self.nav
        self.progress('Открытие формы объявления')
        current=nav.inspect()
        if not current.get('accountVerified'):
            nav.navigate('/profile')
        nav.navigate('/additem')
        nav.inspect(operation='category',label='Для дома и дачи')
        nav.wait(lambda d:any(c.get('name')=='title' for c in d.get('controls',[])))
        self.input_started=True
        result=nav.inspect(operation='fill',name='title',value=source_title)
        if not result.get('filled',{}).get('verified'):
            raise BridgeError('Название не подтверждено')
        category=category_for(product)
        self.progress('Выбор категории')
        suggestions=nav.wait(lambda d:any(c.get('marker')=='category-title' and c.get('tag')=='button' for c in d.get('controls',[])))
        if any(c.get('marker')=='category-title' and c.get('label')==category for c in suggestions.get('controls',[])):
            nav.inspect(operation='category',label=category)
        elif category in CATEGORY_PATHS:
            nav.inspect(operation='category',label='Другая категория')
            for step in CATEGORY_PATHS[category]:
                nav.wait(lambda d:any(c.get('marker')=='category-wizard/button' and norm(c.get('label'))==norm(step) for c in d.get('controls',[])))
                nav.inspect(operation='category',label=step)
        else:
            raise BridgeError('Проверенная категория отсутствует в подсказках текущего товара')
        nav.wait(lambda d:any(c.get('name')=='description' for c in d.get('controls',[])))
        nav.inspect(operation='fill',name='title',value=product.title)
        self.progress('Заполнение описания, цены и характеристик')
        fill_details(nav,product)
        fill_characteristics(nav,product)
        attached=attach_photos(nav,product,self.directory/'photos',self.progress)
        product=replace(product,photos=attached['prepared_photo_paths'])
        def photos_ready(d):
            images=d.get('uploadedImages',[])
            return len(images)==len(product.photos) and all(i.get('sourceOrigin','').endswith('.img.avito.st') and (i.get('complete') or self.photo_failure_policy=='keep_loaded' and i['sourceOrigin'] in self.failed_photo_origins) for i in images)
        removed=0
        try:
            nav.wait(photos_ready,timeout=20)
        except BridgeError:
            if self.photo_failure_policy!='keep_loaded':
                raise
        if self.photo_failure_policy=='keep_loaded' and any(not i.get('complete') for i in nav.inspect().get('uploadedImages',[])):
            product,removed=remove_failed_photos(nav,product)
        self.progress('Сохранение и проверка черновика')
        result=save_current_draft(nav,product,baseline_ids)
        result['avito_title']=product.title
        result['source_photo_count']=original_photo_count
        result['omitted_photo_count']=original_photo_count-len(product.photos)
        result['removed_failed_photo_count']=removed
        result['photo_failure_policy']=self.photo_failure_policy
        return result


def save_current_draft(nav,product,baseline_ids,expected_id=None,expected_draft_count=None):
    nav.inspect(operation='saveDraft')
    account=nav.wait(lambda d:d.get('accountVerified') or expected_id is not None and d.get('path')=='/draft/'+str(expected_id) and bool(d.get('headings')))
    if expected_id is not None:
        ident=str(expected_id)
        if not account.get('accountVerified'):
            nav.navigate('/profile')
            account=nav.wait(lambda d:d.get('accountVerified'))
        count_label=next((c['label'] for c in account['controls'] if c.get('marker')=='personal-items-tabs/tab(draft)'),'')
        count=re.search(r'\n\s*(\d+)\s*$',count_label)
        if expected_draft_count is not None and (not count or int(count.group(1))!=expected_draft_count):
            raise BridgeError('При замене фото изменилось число черновиков: требуется сверка')
        nav.inspect(operation='openSavedDraft',id=ident)
        nav.wait(lambda d:d.get('path')=='/draft/'+ident and bool(d.get('headings')))
    else:
        listing=nav.section('personal-items-tabs/tab(draft)')
        cards=[m for m in listing.get('markers',[]) if re.fullmatch(r'item-snippet/\d+',m['marker']) and m['text'].split('\n')[0]==product.title]
        if len(cards)!=1:
            raise BridgeError('Новый черновик не найден однозначно; повтор запрещён до сверки')
        ident=cards[0]['marker'].split('/')[1]
    if expected_id is not None and ident!=expected_id:
        raise BridgeError('При обновлении фотографий изменился ID объявления')
    if expected_id is None and ident in baseline_ids:
        raise BridgeError('Найден ранее существовавший ID; создание не подтверждено')
    if expected_id is None:nav.navigate('/draft/'+ident)
    last_error=['']
    def confirmed(detail):
        try:
            verify_draft_page(product,detail)
            return True
        except BridgeError as exc:
            last_error[0]=str(exc)
            return False
    try:
        detail=nav.wait(confirmed,timeout=30)
    except BridgeError as exc:
        raise DraftVerificationError(f'Черновик {ident} найден, но проверка не завершена: '+last_error[0],ident,product) from exc
    count=verify_draft_page(product,detail)
    return {'verified':True,'id':ident,'url':'https://www.avito.ru/draft/'+ident,'status':'draft','photo_count':count,'detail':detail}


def replace_draft_photos(bridge,product,ident,policy='strict',failed_origins=()):
    if not product.photos:
        raise BridgeError('Нет фотографий для замены')
    with ThreadPoolExecutor(max_workers=4) as pool:
        photos=list(pool.map(lambda source:cache_photo(source,bridge.directory/'photos'),product.photos[:10]))
    nav=PageNavigator(bridge)
    nav.navigate('/profile')
    account=nav.wait(lambda d:any(c.get('marker')=='personal-items-tabs/tab(draft)' for c in d.get('controls',[])))
    count_label=next(c['label'] for c in account['controls'] if c.get('marker')=='personal-items-tabs/tab(draft)')
    count_match=re.search(r'\n\s*(\d+)\s*$',count_label)
    if not count_match:raise BridgeError('Не прочитан счётчик черновиков до замены')
    initial_count=int(count_match.group(1))
    nav.inspect(operation='openSavedDraft',id=ident)
    nav.wait(lambda d:d.get('path')=='/draft/'+ident and bool(d.get('headings')))
    nav.inspect(operation='editDraft',id=ident)
    current=nav.wait(lambda d:d.get('path')=='/additem' and any(c.get('name')=='description' for c in d.get('controls',[])))
    value=lambda name:next((c.get('value','') for c in current['controls'] if c.get('name')==name),'')
    marker_value=lambda marker:next((c.get('value','') for c in current['controls'] if c.get('marker')==marker),'')
    condition=next((c['label'] for c in current['controls'] if c.get('name') in ('params[110369]','params[110406]') and c.get('checked')),'')
    effective=replace(product,title=value('title'),description=value('description'),price=marker_value('price'),origin_country=marker_value('params[199748]/input'),condition=condition,photos=photos)
    if not effective.title or not effective.description or not effective.price:
        raise BridgeError('Не удалось прочитать текущие поля редактируемого черновика')
    if norm(effective.title)!=norm(product.avito_title or product.title) and not (product.article and re.search(r'\b'+re.escape(product.article)+r'\b',effective.description)):
        raise BridgeError('Черновик больше не соответствует исходному товару; замена фотографий остановлена')
    cleared=nav.inspect(operation='clearPhotos')
    if not cleared.get('cleared') or cleared.get('uploadedImages'):
        raise BridgeError('Старые фотографии не удалены из формы однозначно')
    attached=attach_photos(nav,effective,bridge.directory/'photos')
    effective=replace(effective,photos=attached['prepared_photo_paths'])
    try:
        nav.wait(lambda d:len(d.get('uploadedImages',[]))==len(effective.photos) and all(i['complete'] or policy=='keep_loaded' and i['sourceOrigin'] in failed_origins for i in d['uploadedImages']),timeout=20)
    except BridgeError:
        if policy!='keep_loaded':raise
    removed=0
    if policy=='keep_loaded' and any(not i['complete'] for i in nav.inspect()['uploadedImages']):
        effective,removed=remove_failed_photos(nav,effective)
    result=save_current_draft(nav,effective,set(),expected_id=str(ident),expected_draft_count=initial_count)
    result['removed_failed_photo_count']=removed
    return result


def fill_details(nav, product):
    result=nav.inspect(operation='fill',name='description',value=product.description)
    if not result.get('filled',{}).get('verified'):
        raise BridgeError('Описание не подтверждено в форме')
    price=Decimal(re.sub(r'[ \xa0\u202f]','',product.price).replace(',','.'))
    if not price.is_finite() or price<=0 or price!=price.to_integral_value():
        raise BridgeError('Цена Авито должна быть положительной и указана в целых рублях')
    result=nav.inspect(operation='fill',marker='price',value=str(int(price)))
    field=next((c for c in result.get('controls',[]) if c.get('marker')=='price'),{})
    normalize=lambda s:str(s).replace(' ','').replace('\xa0','').replace('\u202f','').replace(',','.')
    if Decimal(normalize(field.get('value','')))!=Decimal(normalize(product.price)):
        raise BridgeError('Цена не совпала с исходной таблицей')
    return result


def fill_characteristics(nav, product):
    if not product.origin_country:
        raise BridgeError('В источнике отсутствует страна происхождения; Авито подставляет Россия. Требуется фактическое значение.')
    current=nav.inspect()
    if not any(m.get('marker')=='params[199748]/option' and m.get('text')==product.origin_country for m in current.get('markers',[])):
        nav.inspect(operation='inspectOptions',marker='params[199748]/input')
    result=nav.inspect(operation='selectCountry',label=product.origin_country)
    country=next((c for c in result.get('controls',[]) if c.get('marker')=='params[199748]/input'),{})
    if country.get('value')!=product.origin_country:
        raise BridgeError('Страна происхождения не подтверждена в форме')
    if product.condition:
        result=nav.inspect(operation='selectCondition',label=product.condition)
        if not any(c.get('name') in ('params[110369]','params[110406]') and c.get('label')==product.condition and c.get('checked') for c in result.get('controls',[])):
            raise BridgeError('Состояние не подтверждено в форме')
    return result


def attach_photos(nav, product, directory, progress=lambda text:None):
    ids=[]
    prepared=[]
    for index,source in enumerate(product.photos):
        path=Path(cache_photo(source,directory))
        data=path.read_bytes()
        ident=hashlib.sha256(data).hexdigest()
        if ident in ids:
            continue
        ids.append(ident)
        prepared.append(str(path))
        progress(f'Передача оригинала {index+1}/{len(product.photos)}')
        nav.inspect(operation='photoStart',id=ident,filename=f'{product.article or product.sku}-{index+1}{path.suffix}',mime=mimetypes.guess_type(path.name)[0],size=len(data))
        for offset in range(0,len(data),65536):
            chunk=data[offset:offset+65536]
            result=nav.inspect(operation='photoChunk',id=ident,offset=offset,base64=base64.b64encode(chunk).decode('ascii'))
            if result.get('received')!=offset+len(chunk):
                raise BridgeError('Передача оригинала фотографии не подтверждена')
    result=nav.inspect(operation='photoAttach',ids=ids)
    if not result.get('checksumsVerified') or result.get('filesProvided')!=len(ids):
        raise BridgeError('Выбор фотографий в форме не подтверждён')
    result['prepared_photo_paths']=prepared
    return result


def remove_failed_photos(nav, product):
    before=nav.inspect().get('uploadedImages',[])
    if len(before)!=len(product.photos):
        raise BridgeError('Состав фотографий формы не соответствует переданному набору')
    result=nav.inspect(operation='removeFailedPhotos')
    removed=set(result.get('removedPhotoMarkers',[]))
    photos=[source for source,image in zip(product.photos,before) if image['marker'] not in removed]
    remaining=result.get('uploadedImages',[])
    if not photos or len(remaining)!=len(photos) or not all(i.get('complete') for i in remaining):
        raise BridgeError('После удаления недоступных фото нет подтверждённого набора изображений')
    return replace(product,photos=photos),len(removed)
