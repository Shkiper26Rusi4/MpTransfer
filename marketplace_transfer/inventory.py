"""Account-section traversal using DOM markers observed on the user's real Avito page."""
import re
from dataclasses import asdict
from dataclasses import dataclass, field
from .navigation import PageNavigator
from .domain import Product


class InventoryError(RuntimeError):
    pass


@dataclass
class SectionEvidence:
    marker: str
    label: str
    expected: int | None
    items: dict = field(default_factory=dict)
    complete: bool = False
    reason: str = ""


def tabs_from_snapshot(snapshot):
    result=[]
    for control in snapshot.get("controls", []):
        if control.get("role") != "tab" or not control.get("marker", "").startswith("personal-items-tabs/tab("):
            continue
        label=control.get("label", "")
        match=re.search(r"\n\s*(\d[\d\s]*)$", label)
        expected=int(re.sub(r"\s", "", match.group(1))) if match else None
        result.append(SectionEvidence(control["marker"],label,expected))
    return result


def merge_items(evidence, snapshot):
    links={item["id"]:item for item in snapshot.get("listingLinks", [])}
    for control in snapshot.get("controls", []):
        match=re.fullmatch(r"/draft/(\d+)", control.get("path", ""))
        if match:
            links[match.group(1)]={"path":control["path"]}
    for block in snapshot.get("markers", []):
        match=re.fullmatch(r"item-snippet/(\d+)", block.get("marker", ""))
        if not match:
            continue
        ident=match.group(1)
        link=links.get(ident,{})
        evidence.items[ident]={"id":ident,"title":block.get("text", "").split("\n")[0],
            "path":link.get("path"),"summary":block.get("text", ""),"section":evidence.label,
            "detailsVerified":False}


class InventoryCollector:
    def __init__(self, bridge, progress=lambda text: None):
        self.bridge=bridge
        self.progress=progress

    def inspect(self, **payload):
        result=self.bridge.call("diagnose",payload,timeout=40)
        if result.get("error"):
            raise InventoryError(result["error"])
        if result.get("challenge"):
            raise InventoryError("Авито требует ручную проверку безопасности")
        return result

    def collect(self):
        initial=self.inspect()
        if not initial.get("accountVerified"):
            raise InventoryError("Не подтверждён доступ к странице «Мои объявления»")
        sections=tabs_from_snapshot(initial)
        if not sections:
            raise InventoryError("Разделы аккаунта не обнаружены")
        all_items={}
        for section in sections:
            self.progress(section.label)
            page=PageNavigator(self.bridge).section(section.marker)
            selected=[c for c in page.get("controls",[]) if c.get("marker")==section.marker and c.get("selected")=="true"]
            if not selected:
                section.reason="Переход не подтверждён: перезагрузите расширение в chrome://extensions"
                continue
            for attempt in range(100):
                before=len(section.items)
                merge_items(section,page)
                if section.expected is not None and len(section.items)==section.expected and not page.get("truncated"):
                    section.complete=True
                    break
                if attempt and len(section.items)==before:
                    if page.get('pagination',{}).get('hasNext'):
                        nav=PageNavigator(self.bridge)
                        seen=set(section.items)
                        nav.inspect(operation='nextPage')
                        page=nav.wait(lambda d:any(m['marker'].startswith('item-snippet/') and m['marker'].split('/')[1] not in seen for m in d.get('markers',[])))
                        continue
                    section.reason="Число найденных карточек не совпало со счётчиком; пагинация не подтверждена"
                    break
                page=self.inspect(operation="scroll")
            all_items.update(section.items)
        section_complete=all(s.complete for s in sections)
        # Completeness of IDs is distinct from full product descriptions/variant attributes.
        return {"complete":False,"listingCoverageComplete":section_complete,
            "reason":"Нужны подробности карточек для надёжного сравнения характеристик и комплектаций",
            "sections":[{"marker":s.marker,"label":s.label,"expected":s.expected,"found":len(s.items),"complete":s.complete,"reason":s.reason} for s in sections],
            "items":list(all_items.values()),"products":[{"title":x["title"]} for x in all_items.values()]}

    def collect_detailed(self, cache=None):
        nav=PageNavigator(self.bridge)
        initial=nav.inspect()
        if not initial.get('accountVerified'):
            nav.navigate('/profile')
        result=self.collect()
        if not result['listingCoverageComplete']:
            return result
        products=[]
        for item in result['items']:
            cached=(cache or {}).get(item['id'])
            clean=lambda text:text.replace('timing','').strip()
            if cached and cached.get('title')==item['title'] and clean(cached.get('summary',''))==clean(item.get('summary','')) and cached.get('detailsVerified'):
                item['detail']=cached['detail']
                item['detailsVerified']=True
                products.append(asdict(product_from_detail(item)))
                continue
            self.progress('Подробности: '+item['title'])
            # The full traversal already observed this link, possibly on a later
            # page. Returning to page one would make its anchor unavailable.
            # openListing permits only same-origin draft/public listing paths.
            if not item.get('path'):
                products.append(asdict(Product(title=item['title'])))
                continue
            current=nav.inspect()
            if current.get('path')==item['path'] and current.get('headings'):
                detail=current
            else:
                nav.inspect(operation='openListing',path=item['path'])
                detail=nav.wait(lambda d:d.get('path')==item['path'] and bool(d.get('headings')))
            item['detailsVerified']=bool(detail.get('pageText') and item['title'] in detail.get('headings',[]))
            item['detail']=detail
            products.append(asdict(product_from_detail(item)))
        result['products']=products
        result['complete']=all(item['detailsVerified'] for item in result['items'])
        result['reason']='' if result['complete'] else 'Не все подробности карточек подтверждены'
        return result


def product_from_detail(item):
    detail=item.get('detail',{})
    title=item['title']
    text=detail.get('pageText','')
    # Parse only literal supplied attributes; absent values remain absent.
    fields={}
    labels={'article':'Артикул(?: Miele)?','brand':'Бренд','model':'Модель','quantity':'Количество в упаковке','kit':'Комплектация','dimensions':'Размеры','volume':'Объём','compatibility':'Совместимость','condition':'Состояние','origin_country':'Страна происхождения товара'}
    for field,label in labels.items():
        match=re.search(r'(?:^|\n)'+label+r'\s*:\s*([^\n]+)',text,re.I)
        if match:
            fields[field]=match.group(1).strip()
    extra={}
    if fields.get('article') and re.search(r'Артикул\s+Miele\s*:\s*'+re.escape(fields['article'])+r'\b',text,re.I):
        extra['article_namespace']='manufacturer:Miele'
    return Product(title=title,extra=extra,**fields)
