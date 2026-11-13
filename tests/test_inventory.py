from marketplace_transfer.inventory import tabs_from_snapshot,merge_items


def test_observed_account_sections_counts():
    sections=tabs_from_snapshot({'controls':[
        {'role':'tab','marker':'personal-items-tabs/tab(draft)','label':'Черновики\n2'},
        {'role':'tab','marker':'personal-items-tabs/tab(old)','label':'Архив\n1 200'}]})
    assert [s.expected for s in sections]==[2,1200]
    merge_items(sections[0],{'markers':[{'marker':'item-snippet/123','text':'Фильтр\n100 ₽'}],
        'listingLinks':[{'id':'123','title':'Фильтр','path':'/filter_123'}]})
    assert sections[0].items['123']['title']=='Фильтр'
    assert not sections[0].complete
    assert not sections[0].items['123']['detailsVerified']


def test_cold_details_follow_discovered_second_page_link(monkeypatch):
    """A later-page draft has no visible anchor after returning to page one."""
    from marketplace_transfer.inventory import InventoryCollector
    class Browser:
        page = {'path':'/profile','headings':['Мои объявления'],'accountVerified':True}
        opened = []
        def call(self, command, payload, timeout=40):
            if payload.get('operation') == 'navigate':
                raise AssertionError('A hidden later-page link cannot be opened from page one')
            if payload.get('operation') == 'openListing':
                self.opened.append(payload['path'])
                self.page = {'path':payload['path'],'headings':['Старый черновик'],
                             'pageText':'Старый черновик\nМодель: GN XXL\nКоличество в упаковке: 16'}
            return self.page
    browser = Browser()
    collector = InventoryCollector(browser)
    monkeypatch.setattr(collector, 'collect', lambda: {
        'complete':False,'listingCoverageComplete':True,'sections':[],
        'items':[{'id':'1234567890','title':'Старый черновик','path':'/draft/1234567890',
                  'section':'Черновики','summary':'Старый черновик','detailsVerified':False}]})
    result = collector.collect_detailed()
    assert result['complete']
    assert result['products'][0]['quantity'] == '16'
    assert browser.opened == ['/draft/1234567890']
