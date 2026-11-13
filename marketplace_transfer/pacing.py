"""Bounded adaptive pacing; never retries writes or bypasses site restrictions."""
from dataclasses import dataclass
import math
import re


def seconds_to_hourly(seconds):
    value=number(seconds,float('nan'))
    if not math.isfinite(value) or value<0:
        raise ValueError('Темп должен быть неотрицательным конечным числом')
    return 3600/value if value else 0


def hourly_to_seconds(hourly):
    return seconds_to_hourly(hourly)


def failure_kind(error):
    text=str(error).casefold()
    if any(word in text for word in ('captcha','капч','проверку безопасности',
            'расширение не подключено','слишком много запрос','too many requests',
            'доступ ограничен','временно ограничен')) or re.search(r'\b(?:HTTP\s*)?429\b',text,re.I):
        return 'blocked'
    response=getattr(error,'response',None)
    status=getattr(response,'status_code',None)
    if status in (401,403,429):
        return 'blocked'
    if status is not None and 500<=status<=599:
        return 'temporary'
    classes=' '.join(cls.__name__.casefold() for cls in type(error).__mro__)
    if isinstance(error,(TimeoutError,ConnectionError)) or 'timeout' in classes or 'networkerror' in classes or any(word in text for word in (
            'тайм-аут','timeout','timed out','временная ошибка','connection reset',
            'connection refused','connection aborted','connecterror','network error',
            'сетевая ошибка','frame with id 0 was removed','back/forward cache',
            'receiving end does not exist','the message port closed',
            'за отведённое время','команда просрочена','расширение занято')):
        return 'temporary'
    return 'data'


def number(value, default):
    try:
        result=float(value)
        return result if math.isfinite(result) else default
    except (ValueError,TypeError):
        return default


@dataclass(frozen=True)
class PacingDecision:
    cycle: float
    reason: str
    stop: bool = False


class AdaptivePacer:
    def __init__(self,store):
        self.store=store

    def configuration(self):
        enabled=self.store.setting('adaptive_pacing_enabled',False) is True
        minimum=max(17,min(3600,number(self.store.setting('adaptive_min_seconds',17),17)))
        maximum=max(minimum,min(3600,number(self.store.setting('adaptive_max_seconds',60),60)))
        target=max(0,min(86400,number(self.store.setting('target_cycle_seconds',0),0)))
        return enabled,minimum,maximum,target

    def state(self):
        enabled,minimum,maximum,target=self.configuration()
        signature=[minimum,maximum,target]
        state=self.store.setting('adaptive_pacing_state',{})
        if not isinstance(state,dict) or state.get('configuration')!=signature:
            state={'cycle':max(minimum,min(maximum,target or 60)),
                   'successes':0,'temporary_errors':0,'reason':'Начальный темп',
                   'configuration':signature}
        state['cycle']=max(minimum,min(maximum,number(state.get('cycle'),target or 60)))
        return enabled,state

    @property
    def cycle(self):
        enabled,state=self.state()
        return state['cycle'] if enabled else self.configuration()[3]

    def observe(self,outcome):
        enabled,state=self.state()
        if not enabled:
            return PacingDecision(self.cycle,'Требуется проверка доступа или ограничений' if outcome=='blocked' else 'Фиксированный темп',outcome=='blocked')
        _,minimum,maximum,_=self.configuration()
        stop=False
        if outcome=='success':
            state['temporary_errors']=0
            state['successes']=int(max(0,min(3,number(state.get('successes'),0))))+1
            state['reason']=f"Успешных подряд: {state['successes']} из 3"
            if state['successes']>=3:
                previous=state['cycle']
                state['cycle']=max(minimum,state['cycle']-10)
                state['successes']=0
                state['reason']='Три подтверждённых сохранения: интервал сокращён' if state['cycle']<previous else 'Достигнут самый быстрый заданный цикл'
        elif outcome=='temporary':
            state['successes']=0
            state['temporary_errors']=int(max(0,min(3,number(state.get('temporary_errors'),0))))+1
            previous=state['cycle']
            state['cycle']=min(maximum,math.ceil(max(state['cycle']+5,state['cycle']*1.25)))
            stop=state['temporary_errors']>=3
            state['reason']='Три временных сбоя подряд: остановка для проверки связи' if stop else ('Временный сбой: интервал увеличен' if state['cycle']>previous else 'Временный сбой: достигнут самый медленный заданный цикл')
        else:
            state['successes']=0
            state['temporary_errors']=0
            stop=outcome=='blocked'
            state['reason']='Требуется проверка доступа или ограничений' if stop else ('Ошибка карточки: темп не изменён' if outcome=='data' else 'Совпадение пропущено: темп не изменён')
        self.store.set_setting('adaptive_pacing_state',state)
        return PacingDecision(state['cycle'],state['reason'],stop)
