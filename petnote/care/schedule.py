"""Explicit schedule opportunities and personal notices, not inferred care compliance."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import cadences, services
from .models import Activity, NoticeDismissal, RoutineRun
from .modules import MODULES, RETIRED_MODULES


def logical_module(key):
    return RETIRED_MODULES.get(key, key)


def opportunities(member, start, end, *, now=None):
    """Replay versioned configurations: a later edit cannot invent older obligations."""
    now = now or timezone.now()
    room = member.room
    allowed = set(services.visible_modules(member))
    tz = ZoneInfo(room.timezone)
    today = now.astimezone(tz).date()
    if end < start or (end-start).days > 366:
        raise ValueError("Schedule range must be between 0 and 366 days")
    routines = list(room.routines.filter(start_date__lte=end).prefetch_related('versions'))
    versions = list(room.module_versions.filter(effective_at__lt=datetime.combine(end+timedelta(days=1), time.min, tz)).order_by('-effective_at','-id'))
    current_enabled = {setting.key for setting in services.read_module_settings(room) if setting.enabled}
    runs = { (str(r.item_id), r.date):r for r in RoutineRun.objects.filter(item__routine__room=room,date__gte=start,date__lte=end).select_related('activity') }
    entries = []
    for offset in range((end-start).days+1):
        day = start+timedelta(days=offset)
        day_end = datetime.combine(day+timedelta(days=1), time.min, tz)
        historical = next((v for v in versions if v.effective_at < day_end),None)
        enabled = current_enabled if day >= today else set(historical.settings.get('enabled',[])) if historical else set()
        for routine in routines:
            if day < routine.start_date:
                continue
            version = next((v for v in routine.versions.all() if v.effective_on <= day),None)
            snap = version.snapshot if version else None
            if not snap or not snap.get('active') or day.weekday() not in snap.get('weekdays',[]):
                continue
            if snap.get('start_date') and day.isoformat() < snap['start_date']:
                continue
            if snap.get('end_date') and day.isoformat() > snap['end_date']:
                continue
            mixed_private = any(i['module'] not in allowed for i in snap['items'])
            for item in snap['items']:
                key = item['module']
                if key not in allowed:
                    continue
                run = runs.get((item['id'],day))
                activity = run.activity if run and run.activity_id and not run.activity.voided else None
                if activity and (activity.module != key or activity.room_id != room.pk or activity.occurred_at > now):
                    activity = None
                at = datetime.combine(day,time.fromisoformat(snap['at']),tz)
                done = bool(activity and activity.status == 'done' and (not activity.ended_at or activity.ended_at <= now))
                skipped = bool(run and run.skipped_at and not activity)
                paused = key not in enabled and not activity and not skipped
                due = at <= now and not paused
                entries.append({'key':item['id']+':'+day.isoformat(), 'item_id':item['id'],'routine_id':str(routine.pk),
                    'date':day,'at':at,'name':'공유된 루틴 활동' if mixed_private else snap['name'],
                    'note':'' if mixed_private else snap.get('note',''), 'module':key,'logical_module':logical_module(key),
                    'spec':MODULES[key], 'preset_id':item.get('preset_id'), 'active':bool(activity and activity.status=='active'),
                    'activity':activity,'done':done,'skipped':skipped,'paused':paused,'due':due,
                    'state':'완료' if done else '건너뜀' if skipped else '진행 중' if activity else '기능 사용 안 함' if paused else '완료 기록 없음' if due else '예정',
                    'record_url':reverse('record_detail',args=[room.pk,activity.pk]) if activity else None})
    return sorted(entries,key=lambda row:(row['at'],row['name'],row['item_id']))


def completion_summary(member,start,end,*,now=None):
    rows = opportunities(member,start,end,now=now)
    eligible = [x for x in rows if x['due'] or x['done'] or x['skipped']]
    by_module = {}
    for row in eligible:
        key = row['logical_module']
        summary = by_module.setdefault(key,{'module':key,'name':MODULES[key].name,'planned':0,'done':0,'skipped':0,'unrecorded':0,'rows':[]})
        summary['planned'] += 1
        summary['done'] += int(row['done'])
        summary['skipped'] += int(row['skipped'])
        summary['unrecorded'] += int(not row['done'] and not row['skipped'])
        summary['rows'].append(row)
    for item in by_module.values():
        item['percent'] = round(item['done'] / item['planned'] * 100,1) if item['planned'] else None
    return {'rows':rows,'modules':list(by_module.values()),'planned':len(eligible),
            'done':sum(x['done'] for x in eligible),'skipped':sum(x['skipped'] for x in eligible),
            'unrecorded':sum(not x['done'] and not x['skipped'] for x in eligible),
            'basis':'사용자가 설정한 일정 중 시점이 도래했거나 결과를 남긴 항목. 미기록은 미실시로 단정하지 않아요.'}


def relative_date(day,today):
    delta = (day-today).days
    return f'{delta}일 남음' if delta>0 else '오늘 예정' if delta==0 else f'{-delta}일 지남'


def notice_stage(delta):
    if delta>7: return None
    if delta>3: return 'week'
    if delta>1: return 'soon'
    if delta==1: return 'tomorrow'
    if delta==0:return 'due'
    return 'overdue'


def notices(member,*,include_dismissed=False):
    """In-app heads-up only. Closing does not complete, snooze, or delete a schedule."""
    if not include_dismissed and not member.notices_enabled:
        return []
    today=services.local_date(member.room)
    hidden={(r.notice_key,r.stage) for r in member.notice_dismissals.all()} if not include_dismissed else set()
    rows=[]
    for state in cadences.schedule(member):
        due=state['next_due'];delta=(due-today).days
        stage=notice_stage(delta)
        if stage is None:continue
        c=state['cadence'];key=f'cadence:{c.pk}:{c.version}:{due.isoformat()}'
        rows.append({'key':key,'stage':stage,'title':c.title,'due':due,'timing':relative_date(due,today),
                     'module':c.module,'url':reverse('cadence_list',args=[member.room_id]),'kind':'완료 후 간격 알림'})
    allowed=services.visible_modules(member)
    planned=Activity.objects.filter(room=member.room,module__in=allowed,status='planned',voided=False).order_by('occurred_at')
    for record in planned:
        due=record.occurred_at.astimezone(ZoneInfo(member.room.timezone)).date();stage=notice_stage((due-today).days)
        if stage is None:continue
        key=f'planned:{record.pk}:{record.version}:{due.isoformat()}'
        rows.append({'key':key,'stage':stage,'title':str(record.payload.get('routine_title') or record.payload.get('detail') or MODULES[record.module].name)[:100],
                     'due':due,'timing':relative_date(due,today),'module':record.module,
                     'url':reverse('record_detail',args=[member.room_id,record.pk]),'kind':'등록한 예정 기록'})
    rows.sort(key=lambda r:(r['due'],r['title']))
    if include_dismissed:return rows
    if not member.notices_enabled:return []
    return [row for row in rows if (row['key'],row['stage']) not in hidden]


@transaction.atomic
def dismiss(member,key,stage):
    member=services.fresh_member(member)
    valid=next((r for r in notices(member,include_dismissed=True) if r['key']==key and r['stage']==stage),None)
    if valid is None:
        raise ValidationError('이 안내의 일정이나 시점이 바뀌었어요. 새로고침해 주세요.')
    _,created=NoticeDismissal.objects.get_or_create(member=member,notice_key=key,stage=stage)
    return created
