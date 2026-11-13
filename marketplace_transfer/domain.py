from dataclasses import dataclass, field, asdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher


def norm(value):
    return " ".join(re.findall(r"[\w]+", unicodedata.normalize("NFKC", str(value or "")).casefold()))


@dataclass
class Product:
    title: str
    article: str = ""
    sku: str = ""
    ozon_id: str = ""
    brand: str = ""
    model: str = ""
    category: str = ""
    description: str = ""
    price: str = ""
    kit: str = ""
    quantity: str = ""
    dimensions: str = ""
    volume: str = ""
    compatibility: str = ""
    condition: str = ""
    origin_country: str = ""
    avito_title: str = ""
    photos: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def key(self):
        # Variant fields are part of identity, even when a source article is reused.
        fields = [self.article, self.sku, self.ozon_id, self.title, self.kit,
                  self.quantity, self.dimensions, self.volume, self.compatibility]
        return hashlib.sha256(json.dumps([norm(x) for x in fields], ensure_ascii=False).encode()).hexdigest()

    def validate(self):
        errors = []
        if not self.title.strip():
            errors.append("Не указано название")
        try:
            price = Decimal(self.price.replace(" ", "").replace("\xa0", "").replace('\u202f','').replace(",", "."))
            if not price.is_finite() or price <= 0:
                raise InvalidOperation
        except (InvalidOperation, AttributeError):
            errors.append("Цена должна быть положительным числом")
        self.errors = errors
        return errors


def compare(product: Product, candidate: Product):
    """Conservative matching: uncertainty must never become permission to create."""
    variants = ("kit", "quantity", "dimensions", "volume", "compatibility")
    for name in variants:
        a, b = norm(getattr(product, name)), norm(getattr(candidate, name))
        if a and b and a != b:
            return "different"
    def manufacturer_namespace(p):
        explicit=p.extra.get('article_namespace')
        if explicit:
            return explicit
        if p.article and re.search(r'Артикул\s+Miele\s*:\s*'+re.escape(p.article)+r'\b',p.description,re.I):
            return 'manufacturer:Miele'
        return None
    ns1,ns2=manufacturer_namespace(product),manufacturer_namespace(candidate)
    if ns1 and ns1==ns2 and norm(product.article) and norm(candidate.article) and norm(product.article)!=norm(candidate.article):
        common_source_id=any(norm(getattr(product,f)) and norm(getattr(product,f))==norm(getattr(candidate,f)) for f in ('sku','ozon_id'))
        return 'review' if common_source_id else 'different'
    # Numeric and XXL/XL variants in titles remain significant.
    exact_id = any(norm(getattr(product, f)) and norm(getattr(product, f)) == norm(getattr(candidate, f))
                   for f in ("article", "sku", "ozon_id"))
    similarity = SequenceMatcher(None, norm(product.title), norm(candidate.title)).ratio()
    def signature(s):
        raw=unicodedata.normalize('NFKC',s).casefold()
        numbers={str(Decimal(value.replace(',','.')).normalize()) for value in re.findall(r'\d+(?:[.,]\d+)?',raw)}
        return numbers | set(re.findall(r'\b(?:xxl|xl)\b',raw))
    if signature(product.title) != signature(candidate.title):
        return "review" if not signature(candidate.title) and (exact_id or similarity >= .66) else "different"
    missing_variant = any(bool(norm(getattr(product, f))) != bool(norm(getattr(candidate, f))) for f in variants)
    if exact_id and not missing_variant:
        return "exists"
    title_equal = norm(product.title) == norm(candidate.title)
    if title_equal or exact_id:
        return "review"
    a,b=norm(product.title),norm(candidate.title)
    if min(len(a),len(b))>=12 and (a in b or b in a):
        return 'review'
    if similarity >= .66:
        return "review"
    if norm(product.model) and norm(product.model) == norm(candidate.model):
        return "review"
    return "different"


def classify(product, listings, complete):
    results = [compare(product, item) for item in listings]
    if "exists" in results:
        return "exists"
    if "review" in results:
        return "review"
    return "missing" if complete else "unchecked"
