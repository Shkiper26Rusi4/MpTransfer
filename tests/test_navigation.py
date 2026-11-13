from marketplace_transfer.navigation import PageNavigator


def test_already_open_page_is_not_reloaded_before_next_command():
    class Bridge:
        def __init__(self):self.payloads=[]
        def call(self,command,payload,timeout):
            self.payloads.append(payload)
            return {'path':'/profile','headings':['Мои объявления']}
    bridge=Bridge()
    result=PageNavigator(bridge).navigate('/profile')
    assert result['path']=='/profile'
    assert bridge.payloads==[{}]
