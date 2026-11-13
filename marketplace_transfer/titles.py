import re


def avito_title(title, limit=50):
    result=' '.join(title.split())
    if len(result)<=limit:
        return result
    original_numbers=re.findall(r'\d+(?:[.,]\d+)?',result)
    phrases=['для стиральной и сушильной машины','для посудомоечных машин','для посудомоечной машины','для стиральных машин','для стиральной машины','для сушильных машин','для сушильной машины','для пылесосов','для пылесоса','для кофемашины','для кофемашин','для вытяжки','для холодильника']
    for phrase in phrases:
        result=re.sub(re.escape(phrase), '',result,flags=re.I)
        result=re.sub(r'\s+([,.;])',r'\1',result)
        result=' '.join(result.split())
        if len(result)<=limit:
            break
    for phrase in ['Стеклянный ','Угольные ','от запахов ','Fragrance Selection ','с полным выдвижением','технология ','Выдвижные ']:
        if len(result)<=limit:break
        result=re.sub(re.escape(phrase),'',result,flags=re.I)
        result=re.sub(r'\s+([,.;])',r'\1',result)
        result=' '.join(result.split()).strip(' ,')
    if len(result)>limit and result.casefold().startswith('набор ароматизаторов '):
        result='Ароматизаторы '+result[len('Набор ароматизаторов '):]
    if len(result)>limit:
        raise ValueError('Название длиннее 50 символов: нужна редакция с сохранением модели и комплектации')
    if re.findall(r'\d+(?:[.,]\d+)?',result)!=original_numbers:
        raise ValueError('Сокращение изменило числовые характеристики товара')
    return result
