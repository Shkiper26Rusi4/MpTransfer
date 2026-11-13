import time
import re
from .bridge import BridgeError


class PageNavigator:
    def __init__(self, bridge):
        self.bridge=bridge
        self.command_timeout=getattr(bridge,'operation_timeout',40)
        self.read_retries=getattr(bridge,'read_retries',2)

    def inspect(self, **payload):
        result=self.bridge.call('diagnose',payload,timeout=self.command_timeout)
        if result.get('error') or result.get('challenge'):
            raise BridgeError(result.get('error') or 'Авито требует ручную проверку безопасности')
        return result

    def wait(self, predicate, timeout=25):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                result=self.inspect()
            except BridgeError as exc:
                # Retry observations only during an expected page transition, never writes.
                transient=('Frame with ID 0 was removed','back/forward cache','Receiving end does not exist','The message port closed')
                if not any(text in str(exc) for text in transient):
                    raise
                time.sleep(.3)
                continue
            if predicate(result):
                return result
            time.sleep(.3)
        raise BridgeError('Страница не перешла в ожидаемое состояние за отведённое время')

    def navigate(self, path):
        current=self.inspect()
        if current.get('path')==path and current.get('headings'):
            return current
        self.inspect(operation='navigate',path=path)
        return self.wait(lambda d:d.get('path')==path and bool(d.get('headings')))

    def section(self, marker):
        initial=self.inspect()
        old_ids={m['marker'] for m in initial.get('markers',[]) if re.fullmatch(r'item-snippet/\d+',m['marker'])}
        initially_selected=any(c.get('marker')==marker and c.get('selected')=='true' for c in initial.get('controls',[]))
        def predicate(d):
            control=next((c for c in d.get('controls',[]) if c.get('marker')==marker and c.get('selected')=='true'),None)
            if not control:
                return False
            count=re.search(r'\n\s*(\d[\d\s]*)$',control.get('label',''))
            ids={m['marker'] for m in d.get('markers',[]) if re.fullmatch(r'item-snippet/\d+',m['marker'])}
            if count:
                expected=int(re.sub(r'\s','',count.group(1)))
                if expected==0 and ids or expected>0 and not ids:
                    return False
            if not initially_selected and old_ids and ids==old_ids:
                return False
            return True
        attempts=max(1,self.read_retries+1)
        for attempt in range(attempts):
            page=self.inspect()
            if predicate(page):
                return page
            self.inspect(operation='tab',marker=marker)
            try:
                return self.wait(predicate,timeout=4)
            except BridgeError:
                if attempt==attempts-1:
                    raise
